"""Rex sessions and tabs: finding a worktree's tabs, opening, focusing, and closing them."""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import util
import worktrees


def session_windows(session):
    return json.loads(util.run("rex", "session", "inspect", session, "--json"))["windows"]


def related_sessions(session):
    """The picker's session plus the repository's own session, where worktree tabs are opened."""
    sessions = [session] if session else []
    for other in util.STATE.get("repo_sessions", []):
        if other not in sessions:
            sessions.append(other)
    return sessions


# Lists every terminal's working directory in the given sessions, in one rex process instead of
# one per pane. Empty Lua tables come back as {}, so callers treat them as empty lists.
SCAN_SCRIPT = """
local result = {}
for _, sid in ipairs(rex.args.sessions or {}) do
  -- A script can only read sessions it's attached to.
  local _, attach_error = rex.session.attach({ session_id = sid })
  local view, view_error = rex.call("session.view", { session_id = sid })
  if not view then
    return { error = tostring(view_error or attach_error) }
  end
  for _, window in ipairs(view.windows or {}) do
    local found = { session_id = sid, window = window, cwds = {} }
    for _, layer in ipairs(window.layers or {}) do
      for _, block in ipairs(layer.blocks or {}) do
        if block.creator_name == "com.superlogical.terminal" and block.block_id ~= rex.args.skip then
          local p = rex.call("com.superlogical.terminal.process", { session_id = sid, block_id = block.block_id })
          local cwd = p and ((p.foreground and p.foreground.cwd) or (p.child and p.child.cwd))
          if cwd then table.insert(found.cwds, cwd) end
        end
      end
    end
    table.insert(result, found)
  end
end
return { windows = result }
"""


def terminal_cwds(session):
    """Return (window, cwd) for each terminal in the picker's and the repository's sessions,
    except this picker. Each window records its session under "session_id"."""
    args = {"sessions": related_sessions(session), "skip": os.environ.get("REX_BLOCK") or ""}
    try:
        found = json.loads(util.run("rex", "do", "-e", SCAN_SCRIPT, "--args", json.dumps(args), "--json"))
    except (RuntimeError, ValueError, OSError):
        found = None
    if not isinstance(found, dict) or "windows" not in found:
        return terminal_cwds_slowly(session)
    pairs = []
    windows = found["windows"]
    for item in windows if isinstance(windows, list) else []:
        window = dict(item.get("window") or {}, session_id=item.get("session_id"))
        pairs += [(window, cwd) for cwd in (item.get("cwds") or []) if isinstance(cwd, str)]
    return pairs


def terminal_cwds_slowly(session):
    """The same as terminal_cwds, asking for each pane separately, for Rex versions where the
    one-call script doesn't work."""
    picker = os.environ.get("REX_BLOCK")
    blocks = []
    for sid in related_sessions(session):
        try:
            windows = session_windows(sid)
        except (RuntimeError, ValueError, OSError):
            continue
        for window in windows:
            window["session_id"] = sid
            blocks += [(window, block["block_id"]) for layer in window.get("layers", [])
                       for block in layer.get("blocks", [])
                       if block.get("creator_name") == util.TERMINAL and block.get("block_id") != picker]

    def cwd(item):
        window, block = item
        try:
            process = json.loads(util.run("rex", "block", "call", "-s", window["session_id"], "-b", block, "process"))
        except (RuntimeError, ValueError, OSError):
            return None
        return (process.get("foreground") or {}).get("cwd") or (process.get("child") or {}).get("cwd")

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [(window, path) for (window, _), path in zip(blocks, pool.map(cwd, blocks)) if path]


def windows_for_path(session, path, terminals=None):
    """Find tabs with a terminal process currently at or below the worktree."""
    matches, seen = [], set()
    for window, cwd in terminal_cwds(session) if terminals is None else terminals:
        if window["window_id"] not in seen and util.inside(cwd, path):
            seen.add(window["window_id"])
            matches.append(window)
    return matches


def close_worktree_tabs(session, windows):
    """Close the picker's own window last, since doing so terminates this process."""
    picker = os.environ.get("REX_BLOCK")

    def contains_picker(window):
        return any(block.get("block_id") == picker for layer in window.get("layers", [])
                   for block in layer.get("blocks", []))

    for window in sorted(windows, key=contains_picker):
        util.run("rex", "window", "close", "-s", window.get("session_id", session), window["window_id"])


def find_session(label):
    """The ID of the session with this label, or None."""
    sessions = json.loads(util.run("rex", "api", "call", "session.list", "{}")).get("sessions") or []
    return next((s["session_id"] for s in sessions if s.get("label") == label), None)


def remember_repo_session(session_id):
    sessions = util.STATE.setdefault("repo_sessions", [])
    if session_id and session_id not in sessions:
        sessions.append(session_id)


def new_repo_tab(entry, initial_input=None, focus=True):
    """Open a tab for the worktree in its repository's session, named after the repository,
    creating the session if needed. Returns (session ID, window ID)."""
    label = worktrees.repo_name(entry["worktree"])
    options = {"cwd": entry["worktree"]}
    if initial_input:
        options["initial_input"] = initial_input
    window = {"window_label": tab_label(entry), "layout": {"block": {"flavor": util.TERMINAL + ".shell", "options": options}}}
    session_id = find_session(label)
    if session_id:
        created = json.loads(util.run("rex", "api", "call", "-s", session_id, "session.new_window",
                                 json.dumps({**window, "focus": focus})))
        window_id = created["window_id"]
    else:
        created = json.loads(util.run("rex", "api", "call", "session.create",
                                 json.dumps({"label": label, "initial_windows": [window]})))
        session_id, window_id = created["session_id"], created["initial_windows"][0]["window_id"]
    remember_repo_session(session_id)
    return session_id, window_id


def show_tab(client, session_id, window_id):
    """Ask the app to show the tab; the server's active window alone doesn't move the app's view."""
    if client:
        try:
            util.run("rex", "-C", client, "do", "session.select", f"session_id={session_id}", f"window_id={window_id}")
        except RuntimeError as error:
            raise RuntimeError(f"Couldn't switch to the tab: {error}") from error


def tab_label(entry):
    """Just the branch: worktree tabs live in their repository's session, which names the repository."""
    return worktrees.branch_name(entry)[:128]


def open_tab(session, entry, client=None, setup=None, focus=True, terminals=None):
    """Focus the worktree's tab, or create one in the repository's session. With focus=False, only
    create it, in the background. Returns the window ID, or None when focus=False and a tab is
    already open."""
    matches = windows_for_path(session, entry["worktree"], terminals)
    if matches and not focus:
        return None
    if not focus:
        return new_repo_tab(entry, focus=False)[1]
    if matches:
        window = matches[0]
        session_id, window_id = window.get("session_id", session), window["window_id"]
        util.run("rex", "focus", "-s", session_id, window_id)
    else:
        # A setup command is typed into the new tab's shell, so its output stays visible there.
        session_id, window_id = new_repo_tab(entry, setup + "\n" if setup else None)
    util.STATE["navigated"] = True
    show_tab(client, session_id, window_id)
    return window_id


def open_split(session, entry, block):
    """Open the worktree in a new pane beside block, and focus it."""
    if not block:
        raise RuntimeError("Can't tell which pane to split.")
    util.run("rex", "split", "-s", session, "-b", block, "--split=right", "-c", entry["worktree"],
        "--label", worktrees.branch_name(entry))
    util.STATE["navigated"] = True


def open_editor(session, entry, client=None):
    """Open the worktree in a new tab in the repository's session, typing your editor command into
    its interactive shell, where $VISUAL or $EDITOR are set."""
    session_id, window_id = new_repo_tab(entry, 'eval "${VISUAL:-${EDITOR:-vi}}" .\n')
    util.STATE["navigated"] = True
    show_tab(client, session_id, window_id)


def picker_focused(session):
    """Whether this picker's pane is still the session's focused pane."""
    picker = os.environ.get("REX_BLOCK")
    if not picker:
        return True
    view = json.loads(util.run("rex", "session", "inspect", session, "--json"))
    for window in view.get("windows", []):
        if any(b.get("block_id") == picker for layer in window.get("layers", []) for b in layer.get("blocks", [])):
            return window["window_id"] == view.get("active_window_id") and window.get("focused_block_id") == picker
    return False


def restore_focus(session, block):
    """Return focus to the pane the picker was opened from, unless a tab was opened."""
    if not block or util.STATE["navigated"]:
        return
    try:
        util.run("rex", "focus", "-s", session, block)
    except (RuntimeError, OSError):
        pass  # The pane may have closed, for example with its worktree's tab.
