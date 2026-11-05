---@diagnostic disable: undefined-global
-- Prefix: Ctrl+B, then the indicated key.
-- p: pane mode     r: resize mode
-- t: tab mode      g: session mode   w: worktree picker
-- s, h/j/k/l: split in a direction
-- Escape exits all modes; tab and session modes exit after one action.
-- Ctrl+B twice sends Ctrl+B to the terminal.

-- Direct pane and sidebar shortcuts
rex.bind("ctrl+b>h", "pane.focus", { direction = "left" })
rex.bind("ctrl+b>j", "pane.focus", { direction = "down" })
rex.bind("ctrl+b>k", "pane.focus", { direction = "up" })
rex.bind("ctrl+b>l", "pane.focus", { direction = "right" })
rex.bind("ctrl+b>shift+h", "pane.move", { direction = "left" })
rex.bind("ctrl+b>shift+j", "pane.move", { direction = "down" })
rex.bind("ctrl+b>shift+k", "pane.move", { direction = "up" })
rex.bind("ctrl+b>shift+l", "pane.move", { direction = "right" })
rex.bind("ctrl+b>z", "pane.zoom")
rex.bind("ctrl+b>x", "client.close")
rex.bind("ctrl+b>b", "client.sidebar.toggle")
-- Ctrl+B, s, then h/j/k/l splits in that direction.
for key, direction in pairs({ h = "left", j = "down", k = "up", l = "right" }) do
	rex.bind("ctrl+b>s>" .. key, "pane.split", { direction = direction })
end

-- Send Ctrl+B to terminal applications that need it.
rex.bind("ctrl+b>ctrl+b", "pane.send_key", { key = "ctrl+b" })

-- Moving a pane to a new tab also leaves pane mode.
rex.action({
	name = "move_pane_to_new_tab_and_exit",
	title = "Move Pane to New Tab and Exit Mode",
	run = function()
		rex.client.queue("pane.move_to_new_tab")
		rex.client.queue("client.mode.exit_all")
	end,
})

-- Pane mode: Ctrl+B, p. Stays active for repeated actions.
rex.mode("pane", { exclusive = true })
rex.bind("ctrl+b>p", "client.mode.enter", { name = "pane" })
for key, direction in pairs({ h = "left", j = "down", k = "up", l = "right" }) do
	rex.bind("pane/" .. key, "pane.focus", { direction = direction })
	rex.bind("pane/shift+" .. key, "pane.move", { direction = direction })
	rex.bind("pane/s>" .. key, "pane.split", { direction = direction })
end
rex.bind("pane/t", "move_pane_to_new_tab_and_exit")
rex.bind("pane/z", "pane.zoom")
rex.bind("pane/x", "client.close")
rex.bind("pane/=", "pane.balance")
rex.bind("pane/r", "client.mode.enter", { name = "resize" })

-- Tab mode: Ctrl+B, t. Exits after one action.
rex.mode("tab", { exclusive = true })
rex.bind("ctrl+b>t", "client.mode.enter", { name = "tab", once = true })
rex.bind("tab/c", "client.tab.new")
rex.bind("tab/n", "client.tab.next")
rex.bind("tab/p", "client.tab.previous")
rex.bind("tab/h", "client.tab.move", { direction = "backward" })
rex.bind("tab/l", "client.tab.move", { direction = "forward" })
rex.bind("tab/r", "client.tab.rename")
rex.bind("tab/x", "client.tab.close")
for index = 1, 9 do
	rex.bind("tab/" .. index, "client.tab.goto", { index = index })
end

-- Session mode: Ctrl+B, g. Exits after one action.
rex.mode("session", { exclusive = true })
rex.bind("ctrl+b>g", "client.mode.enter", { name = "session", once = true })
rex.bind("session/c", "session.new")
rex.bind("session/n", "session.next")
rex.bind("session/p", "session.previous")
rex.bind("session/w", "session.switch")
rex.bind("session/r", "session.rename")
rex.bind("session/x", "session.close")

-- Worktree actions open a temporary floating terminal for interaction.
local PICKER_LABEL = "worktrees"
-- Open pickers by block ID: their layer, folder, and the pane they were opened from. The picker runs
-- under a root-owned login wrapper, so its own working directory can't be read.
local pickers = {}

-- Where a block is: its session, window, and label. The block is looked for in the given session
-- first, then in every other session, in case a client reports a session other than the pane's.
local function locate(session_id, block_id)
	local ids = { session_id }
	local list = rex.call("session.list")
	for _, s in ipairs((list and list.sessions) or {}) do
		if s.session_id ~= session_id then ids[#ids + 1] = s.session_id end
	end
	for _, id in ipairs(ids) do
		local view = rex.session.view({ session_id = id })
		for _, window in ipairs((view and view.windows) or {}) do
			for _, layer in ipairs(window.layers or {}) do
				for _, block in ipairs(layer.blocks or {}) do
					if block.block_id == block_id then
						return { session_id = id, window_id = window.window_id, label = block.label, block_id = block_id }
					end
				end
			end
		end
	end
	return nil
end

local TERMINAL = "com.superlogical.terminal"

-- The pane the invoking client is actually showing. Some clients, such as Rex's iPhone app, report
-- the session's shared focused pane, which may be another device's, or a picker the phone has since
-- swiped away from. Each terminal is owned by the client that last showed or used it, and records
-- when each client last asked for a size, which a client does for the pane it's showing. So of the
-- reported pane and each tab's focused pane, use the one this client owns and sized most recently.
local function client_pane(session_id, block_id, client_id)
	if not client_id then return block_id end
	local candidates, seen = { block_id }, { [block_id] = true }
	local view = rex.session.view({ session_id = session_id })
	for _, window in ipairs((view and view.windows) or {}) do
		local focused = window.focused_block_id
		if focused and not seen[focused] then
			seen[focused] = true
			candidates[#candidates + 1] = focused
		end
	end
	local best, best_at = nil, nil
	for _, candidate in ipairs(candidates) do
		local size = rex.block.call(TERMINAL, "size", { block_id = candidate })
		if size and size.owner == client_id then
			local at = ""
			for _, d in ipairs(size.desired or {}) do
				if d.client_id == client_id then at = d.updated_at or "" end
			end
			if not best or at > best_at then best, best_at = candidate, at end
		end
	end
	return best or block_id
end

local function worktree_action(mode)
	return function(ctx)
		if not ctx.session_id or not ctx.block_id then
			error("Worktrees require a focused terminal and session")
		end
		local where = locate(ctx.session_id, ctx.block_id)
		local session_id = (where and where.session_id) or ctx.session_id
		local pane = client_pane(session_id, ctx.block_id, ctx.client_id)
		if pane ~= ctx.block_id then where = locate(session_id, pane) or where end
		local cwd, origin = nil, pane
		local open = pickers[pane]
		if open then
			-- Invoked from inside a picker: replace it, opening the requested screen for the same folder.
			pickers[pane] = nil
			rex.session.close_layer({ session_id = session_id, layer_id = open.layer_id })
			cwd, origin = open.cwd, open.origin
		elseif where and where.label == PICKER_LABEL then
			return -- A picker from before a config reload is already open and focused.
		else
			local process, err = rex.block.call(TERMINAL, "process", { block_id = pane })
			if not process then error(err) end
			cwd = (process.foreground and process.foreground.cwd) or (process.child and process.child.cwd)
			if not cwd then
				local running = (process.foreground and process.foreground.name) or "a program"
				error("Can't tell which folder the focused pane is in (it's running " .. running
					.. "). Focus a shell inside a Git repository and try again.")
			end
		end
		local home = os.getenv("HOME")
		if not home then error("HOME is not set") end
		-- Open the picker in the tab of the pane it was invoked from. Without window_id it would open in
		-- the session's active tab, which on a phone needn't be the tab on screen.
		local result, layer_err = rex.session.new_layer({
			session_id = session_id,
			window_id = where and where.window_id,
			bounds = { 0.25, 0.2, 0.5, 0.45 },
			focus = true,
			layout = rex.layout.block({
				flavor = "com.superlogical.terminal.shell",
				label = PICKER_LABEL,
				options = {
					shell = "none",
					cwd = "/",
					command = { "python3", home .. "/.config/rex/rex-worktrees", mode, cwd, session_id, ctx.client_id or "", origin },
				},
			}),
		})
		if not result then error(layer_err) end
		local block_id = result.block_ids and result.block_ids[1]
		if block_id then pickers[block_id] = { layer_id = result.layer_id, cwd = cwd, origin = origin } end
	end
end

-- Ctrl+B, w opens the worktree picker; New Worktree is also in the command palette.
rex.action({ name = "worktrees", title = "Worktrees…", category = "Worktrees", run = worktree_action("list") })
rex.action({ name = "worktree_new", title = "New Worktree…", category = "Worktrees", run = worktree_action("new") })
rex.bind("ctrl+b>w", "worktrees")

-- Resize mode: Ctrl+B, r. Stays active for repeated adjustments.
rex.mode("resize", { exclusive = true })
rex.bind("ctrl+b>r", "client.mode.enter", { name = "resize" })
rex.bind("resize/h", "pane.resize", { direction = "left" })
rex.bind("resize/j", "pane.resize", { direction = "down" })
rex.bind("resize/k", "pane.resize", { direction = "up" })
rex.bind("resize/l", "pane.resize", { direction = "right" })
rex.bind("resize/left", "pane.resize", { direction = "left" })
rex.bind("resize/down", "pane.resize", { direction = "down" })
rex.bind("resize/up", "pane.resize", { direction = "up" })
rex.bind("resize/right", "pane.resize", { direction = "right" })
rex.bind("resize/=", "pane.balance")
rex.bind("resize/escape", "client.mode.exit_all")
