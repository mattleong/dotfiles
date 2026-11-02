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
local function worktree_action(mode)
	return function(ctx)
		if not ctx.session_id or not ctx.block_id then
			error("Worktrees require a focused terminal and session")
		end
		local process, err = rex.block.call("com.superlogical.terminal", "process", { block_id = ctx.block_id })
		if not process then error(err) end
		local cwd = (process.foreground and process.foreground.cwd) or (process.child and process.child.cwd)
		if not cwd then error("Cannot determine the focused terminal's working directory") end
		local home = os.getenv("HOME")
		if not home then error("HOME is not set") end
		local result, layer_err = rex.session.new_layer({
			session_id = ctx.session_id,
			bounds = { 0.25, 0.2, 0.5, 0.45 },
			focus = true,
			layout = rex.layout.block({
				flavor = "com.superlogical.terminal.shell",
				options = {
					shell = "none",
					cwd = "/",
					command = { "python3", home .. "/.config/rex/worktree.py", mode, cwd, ctx.session_id, ctx.client_id or "", ctx.block_id },
				},
			}),
		})
		if not result then error(layer_err) end
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
