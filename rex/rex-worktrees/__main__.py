"""Entry point. Rex runs this directory with python3 in a floating pane."""
import curses
import locale
import os
import sys

import app
import keyboard
import tabs
import ui
import util


def main(stdscr, mode, cwd, session, client="", origin=""):
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    ui.init_colors()
    stdscr.keypad(True)
    util.STATE["session"] = session
    # Ask the terminal to report focus changes, so the picker closes when another pane is focused.
    sys.stdout.write("\x1b[?1004h")
    sys.stdout.flush()
    ui.render(stdscr, [[], [("Loading worktrees…", ui.style("dim"))]])
    try:
        manager = app.Manager(stdscr, cwd, session, client, origin)
        if mode == "new":
            manager.new_screen()
        else:
            manager.list_screen()
    except keyboard.FocusLost:
        util.STATE["navigated"] = True  # Focus already moved where the user wanted it.
    except (RuntimeError, OSError, UnicodeError, ValueError) as error:
        try:
            ui.show_error(stdscr, str(error))
        except keyboard.FocusLost:
            util.STATE["navigated"] = True


def setup_locale():
    for name in ("", "C.UTF-8", "en_US.UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, name)
        except locale.Error:
            continue
        if "utf" in (locale.getpreferredencoding(False) or "").lower():
            return


def start():
    if len(sys.argv) not in (4, 5, 6) or sys.argv[1] not in ("list", "new"):
        sys.exit("This helper is launched by Rex worktree actions.")
    mode, cwd, session = sys.argv[1:4]
    client = sys.argv[4] if len(sys.argv) > 4 else ""
    origin = sys.argv[5] if len(sys.argv) > 5 else ""
    os.environ.setdefault("ESCDELAY", "25")
    # Rex starts the picker without a login shell, so add the usual tool locations, such as for gh.
    path = os.environ.get("PATH", "").split(os.pathsep)
    extra = [d for d in ("/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")) if d not in path]
    os.environ["PATH"] = os.pathsep.join(path + extra)
    setup_locale()
    try:
        curses.wrapper(main, mode, cwd, session, client, origin)
    except KeyboardInterrupt:
        pass
    finally:
        tabs.restore_focus(session, origin)


if __name__ == "__main__":
    start()
