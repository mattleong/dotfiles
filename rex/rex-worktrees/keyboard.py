"""Keyboard input: reading keys, focus reports, line editing, and Vim-style motions."""
import curses
import re

import tabs
import util


ENTER = ("\n", "\r", curses.KEY_ENTER)
BACKSPACE = ("\x7f", "\b", curses.KEY_BACKSPACE)
ESCAPE = "\x1b"
# Every screen: q or esc closes or cancels, and enter or l selects the row under the cursor.
CLOSE = (ESCAPE, "q")
SELECT = ENTER + ("l",)


def edit_text(text, pos, key):
    """Apply a line-editing key at the cursor. Returns (text, cursor position)."""
    pos = max(0, min(pos, len(text)))
    if key == curses.KEY_LEFT:
        return text, max(0, pos - 1)
    if key == curses.KEY_RIGHT:
        return text, min(len(text), pos + 1)
    if key in (curses.KEY_HOME, "\x01"):
        return text, 0
    if key in (curses.KEY_END, "\x05"):
        return text, len(text)
    if key in BACKSPACE:
        return (text[:pos - 1] + text[pos:], pos - 1) if pos else (text, pos)
    if key in (curses.KEY_DC, "\x04"):
        return text[:pos] + text[pos + 1:], pos
    if key == "\x15":
        return text[pos:], 0
    if key == "\x0b":
        return text[:pos], pos
    if key == "\x17":
        start = len(re.sub(r"\S*\s*$", "", text[:pos]))
        return text[:start] + text[pos:], start
    if isinstance(key, str) and key.isprintable():
        return text[:pos] + key + text[pos:], pos + len(key)
    return text, pos


def motion(key, index, count, page, pending):
    """Apply a Vim-style motion. Returns (new index or None if not a motion, pending key)."""
    if count == 0:
        return None, None
    half = max(1, page // 2)
    if key == "g":
        return (0, None) if pending == "g" else (index, "g")
    moves = {
        "j": index + 1, curses.KEY_DOWN: index + 1, "\x0e": index + 1,
        "k": index - 1, curses.KEY_UP: index - 1, "\x10": index - 1,
        "G": count - 1, curses.KEY_END: count - 1, curses.KEY_HOME: 0,
        "\x04": index + half, "\x15": index - half,
        "\x06": index + page, curses.KEY_NPAGE: index + page,
        "\x02": index - page, curses.KEY_PPAGE: index - page,
    }
    if key not in moves:
        return None, None
    return min(count - 1, max(0, moves[key])), None


class FocusLost(Exception):
    """The picker's pane lost focus to another pane."""


def set_timeout(stdscr, ms):
    util.STATE["timeout"] = ms
    stdscr.timeout(ms)


def unget(key):
    if isinstance(key, int):
        curses.ungetch(key)
    else:
        curses.unget_wch(key)


def pending_input(stdscr):
    """Whether more keys are already queued, such as from a held-down key. Drawing is skipped until
    the queue drains, so the selection stops moving as soon as the key is released."""
    stdscr.timeout(0)
    try:
        key = stdscr.get_wch()
    except curses.error:
        return False
    finally:
        stdscr.timeout(util.STATE.get("timeout", -1))
    unget(key)
    return True


def read_key(stdscr):
    """Return the next key, or None when the input timeout passes first or after a focus event.

    The terminal reports focus changes as ESC [ I and ESC [ O. Losing focus to another pane raises
    FocusLost; losing it because the whole app went to the background doesn't."""
    try:
        key = stdscr.get_wch()
    except curses.error:
        return None
    if key != ESCAPE:
        return key
    stdscr.timeout(0)
    try:
        follow = []
        for _ in range(2):
            try:
                follow.append(stdscr.get_wch())
            except curses.error:
                break
        if follow[:1] == ["["] and follow[1:] in (["I"], ["O"]):
            if follow[1] == "O" and util.STATE.get("session"):
                try:
                    focused = tabs.picker_focused(util.STATE["session"])
                except (RuntimeError, ValueError, OSError):
                    focused = True
                if not focused:
                    raise FocusLost()
            return None
        for char in reversed(follow):
            unget(char)
        return ESCAPE
    finally:
        stdscr.timeout(util.STATE.get("timeout", -1))
