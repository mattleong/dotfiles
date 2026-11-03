"""Drawing: colors, rows, columns, and shared screens such as help and confirmation."""
import curses
import os
import textwrap
import time

import keyboard
import util
import worktrees


COLORS = {}
LAST_FRAME = {"rows": [], "cursor": None}  # What render drew last, so a spinner can draw over it.
SELECTED_PAIRS = {}  # Color pair number -> the same colors on the selected-row background.


def short_age(stamp, now=None):
    """A compact age such as 5m, 3h, 2d, 3w, 4mo, or 1y."""
    if not stamp:
        return ""
    seconds = max(0, (time.time() if now is None else now) - stamp)
    for unit, size in (("y", 365 * 86400), ("mo", 30 * 86400), ("w", 7 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return "now"


def init_colors():
    COLORS.update(dim=curses.A_DIM, bold=curses.A_BOLD)
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    palette = [("green", curses.COLOR_GREEN), ("yellow", curses.COLOR_YELLOW),
               ("cyan", curses.COLOR_CYAN), ("red", curses.COLOR_RED), ("blue", curses.COLOR_BLUE),
               ("magenta", curses.COLOR_MAGENTA)]
    for index, (name, color) in enumerate(palette, 1):
        curses.init_pair(index, color, background)
        COLORS[name] = curses.color_pair(index)
    if curses.COLORS >= 256 and curses.COLOR_PAIRS > 2 * len(palette) + 1:
        # A subtle gray behind the selected row, in each color.
        highlight = 236
        for index, color in enumerate([-1] + [c for _, c in palette]):
            pair = len(palette) + 1 + index
            curses.init_pair(pair, color, highlight)
            SELECTED_PAIRS[index] = curses.color_pair(pair)


def style(name):
    return COLORS.get(name, 0)


class Selected(list):
    """A row drawn on the selected-row background."""


def space(stdscr):
    """The columns a row can use: render indents rows by 2 and leaves the last column empty."""
    return max(1, stdscr.getmaxyx()[1] - 3)


def fit(row, width):
    """Fit a row's segments in width columns. A segment can have a third item, a rank: when the row
    is too wide, the highest-ranked segments are dropped first. Then the row is cut at the edge,
    with an ellipsis where that cuts through text."""
    row = list(row)
    while len("".join(s[0] for s in row).rstrip()) > width:
        ranks = [s[2] for s in row if len(s) > 2 and s[2]]
        if not ranks:
            break
        row = [s for s in row if len(s) < 3 or s[2] != max(ranks)]
    segments = [(s[0], s[1]) for s in row]
    if len("".join(text for text, _ in segments).rstrip()) <= width:
        return segments
    out, used = [], 0
    for text, attr in segments:
        room = width - used
        if len(text) > room:
            visible = text[:room]
            if visible.strip() and text[room:].strip():
                visible = text[:room - 1].rstrip() + "…"
            out.append((visible, attr))
            break
        out.append((text, attr))
        used += len(text)
    return out


def wrap(text, width):
    """Lines of text no wider than width, at least one."""
    return textwrap.wrap(text, max(1, width)) or [""]


def render(stdscr, rows, cursor=None):
    LAST_FRAME.update(rows=rows, cursor=cursor)
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    for y, row in enumerate(rows[:height]):
        highlight = isinstance(row, Selected) and SELECTED_PAIRS
        if highlight:
            try:
                stdscr.addnstr(y, 0, " " * width, width - 1, SELECTED_PAIRS[0])
            except curses.error:
                pass
        x = 2
        for text, attr in fit(row, width - 3):
            if x >= width - 1:
                break
            if highlight:
                attr = (attr & ~curses.A_COLOR) | SELECTED_PAIRS.get(curses.pair_number(attr), SELECTED_PAIRS[0])
            try:
                stdscr.addnstr(y, x, text, width - 1 - x, attr)
            except curses.error:
                pass
            x += len(text)
    if cursor:
        curses.curs_set(1)
        try:
            stdscr.move(min(cursor[0], height - 1), min(cursor[1] + 2, width - 1))
        except curses.error:
            pass
    else:
        curses.curs_set(0)
    stdscr.refresh()


def highlighted(text, hits, attr):
    """Split text into segments, emphasizing the characters at the hit positions."""
    segments, start = [], 0
    for i in range(1, len(text) + 1):
        if i == len(text) or (i in hits) != (start in hits):
            hit = start in hits
            segments.append((text[start:i], (style("yellow") | style("bold")) if hit else attr))
            start = i
    return segments


def pr_cell(entry):
    pr = entry.get("pr")
    if not pr:
        return []
    state = "draft" if pr.get("isDraft") and pr["state"] == "OPEN" else pr["state"].lower()
    color = {"open": "green", "merged": "magenta", "closed": "red"}.get(state, "dim")
    return [(f"{util.ICONS['pr']} #{pr['number']} {state}", style(color))]


def sync_cell(entry):
    cell = []
    for part in entry.get("track") or []:
        if part == worktrees.REMOTE_DELETED:
            cell.append((util.ICONS["deleted"] + " " + part, style("red")))
        else:
            cell.append((util.ICONS["ahead" if part.endswith("ahead") else "behind"] + " " + part, style("cyan")))
    return cell


def state_cell(entry):
    cell = []
    if entry.get("main"):
        cell.append((util.ICONS["main"] + " main checkout", style("blue")))
    if entry.get("open") is True:
        cell.append((util.ICONS["tab"] + " tab open", style("green")))
    if entry.get("dirty") is True:
        cell.append((util.ICONS["dirty"] + " uncommitted", style("yellow")))
    if "locked" in entry:
        cell.append((util.ICONS["locked"] + " locked", style("red")))
    if worktrees.missing(entry):
        cell.append((util.ICONS["missing"] + " folder missing", style("red")))
    if util.PENDING in (entry.get("dirty"), entry.get("open")):
        cell.append(("…", style("dim")))
    return cell


def badges(entry):
    """Every status label for a worktree, in column order."""
    return pr_cell(entry) + sync_cell(entry) + state_cell(entry)


def cell_width(cell, gap=3):
    return sum(len(text) for text, _ in cell) + gap * max(0, len(cell) - 1)


def badges_width(entry):
    return cell_width(badges(entry))


def short_cell(cell):
    """A cell's labels as just their icons and counts, such as ↑2 for "↑ 2 ahead" or #12 for a pull
    request, for narrow panes."""
    out = []
    for text, attr in cell:
        icon, _, rest = text.partition(" ")
        word = rest.split(" ")[0]
        out.append((icon + (word if word[:1].isdigit() or word[:1] == "#" else ""), attr))
    return out


def joined(cell, gap=" · "):
    """A cell's labels as row segments, with a dim separator between them."""
    row = []
    for i, (text, attr) in enumerate(cell):
        row += ([(gap, style("dim"))] if i else []) + [(text, attr)]
    return row


CELLS = [("PR", pr_cell), ("SYNC", sync_cell), ("STATE", state_cell)]
MIN_BRANCH = 20  # Narrower than this, the list switches to compact rows.


def column_widths(entries, space=None):
    """Widths for the list's columns, sized to every worktree so they don't shift while filtering.
    A column that's empty for every worktree gets width 0 and is hidden. Given the space available,
    the branch column narrows to fit; when even that isn't enough, rows turn compact: the branch,
    its age, and the labels' icons."""
    longest = max([len(e["name"]) for e in entries] + [6])
    widths = {"BRANCH": min(40, longest), "compact": False}
    for title, cell in CELLS:
        width = max([cell_width(cell(e)) for e in entries] + [0])
        widths[title] = max(width, len(title)) if width else 0
    widths["SOURCE"] = max([len(worktrees.worktree_source(e)) for e in entries] + [0])
    if space is None:
        return widths
    # Everything but the branch and the folder, which comes last and can be cut off.
    rest = 4 + 2 + 5 + sum(widths[t] + 3 for t, _ in CELLS if widths[t]) + (widths["SOURCE"] + 2 if widths["SOURCE"] else 0)
    if rest + widths["BRANCH"] <= space:
        return widths
    if space - rest >= min(MIN_BRANCH, longest):
        widths["BRANCH"] = space - rest
        return widths
    icons = max([cell_width(short_cell(badges(e)), 1) for e in entries] + [0])
    widths.update(compact=True, BADGES=icons,
                  BRANCH=max(6, min(longest, space - 4 - 2 - 3 - (icons + 2 if icons else 0))))
    return widths


def header_row(widths):
    dim = style("dim") | style("bold")
    row = [("    " + "BRANCH".ljust(widths["BRANCH"] + 2), dim), ("AGE  ", dim)]
    if widths["compact"]:
        return row
    for title, _ in CELLS:
        if widths[title]:
            row.append((title.ljust(widths[title] + 3), dim))
    row.append(("FROM", dim))
    return row


def entry_row(entry, selected, widths, match=None):
    _, name_hits, folder_hits = match or (0, set(), set())
    width = widths["BRANCH"]
    name = entry["name"] if len(entry["name"]) <= width else entry["name"][:width - 1] + "…"
    row = [("› " if selected else "  ", style("cyan") | style("bold")),
           ("● " if entry.get("current") else "  ", style("green"))]
    row += highlighted(name, {h for h in name_hits if h < len(name)}, style("bold") if selected else 0)
    row.append((" " * (width - len(name) + 2), 0))
    row.append((short_age(entry.get("time")).rjust(3) + "  ", style("dim")))
    if widths["compact"]:
        row += joined(short_cell(badges(entry)), " ")
        return Selected(row) if selected else row
    for title, cell_for in CELLS:
        if not widths[title]:
            continue
        cell = cell_for(entry)
        row += joined(cell)
        row.append((" " * (widths[title] - cell_width(cell) + 3), 0))
    if widths["SOURCE"]:
        row.append((worktrees.worktree_source(entry).ljust(widths["SOURCE"]) + "  ", style("blue")))
    row += highlighted(os.path.basename(entry["worktree"]), folder_hits, style("dim"))
    return Selected(row) if selected else row


class Flash:
    """A status message that fades: after 4 seconds, or 8 for errors."""
    SECONDS = 4
    ICONS = {"green": "ok", "red": "error"}

    def __init__(self):
        self.text, self.tone, self.at = None, "red", 0.0

    def set(self, text, tone="red"):
        self.text, self.tone, self.at = text, tone, time.monotonic()

    def clear(self):
        self.text = None

    def left(self):
        """Seconds until it fades, or None when there's no message. Clears an expired one."""
        if not self.text:
            return None
        left = self.SECONDS * (2 if self.tone == "red" else 1) - (time.monotonic() - self.at)
        if left <= 0:
            self.text = None
            return None
        return left

    def wait(self, wait):
        """The input timeout to use, shortened so the screen redraws when the message fades."""
        left = self.left()
        if left is None:
            return wait
        ms = int(left * 1000) + 50
        return ms if wait < 0 else min(wait, ms)

    def row(self):
        if self.left() is None:
            return []
        icon = util.ICONS[self.ICONS.get(self.tone, "info")]
        return [(icon + " " + self.text, style(self.tone))]


def section(title, extra="", tone="dim"):
    """A heading inside a screen, such as EXISTING BRANCHES. Warnings use a color as the tone."""
    return [(title.upper(), style(tone) | style("bold"))] + ([("  " + extra, style("dim"))] if extra else [])


def header(title, repo, extra=""):
    """The first line of every screen: its title and the repository. In a narrow pane, the extra
    note drops out first, then the repository."""
    return [(title, style("bold")), ("  " + repo, style("dim"), 1)] + ([("   " + extra, style("dim"), 2)] if extra else [])


def footer(enter=None, move=False, slash=None, toggle=False, actions=(), back="back", clear_filter=False):
    """The key hints at the bottom of every screen, always in the same order. In a narrow pane, the
    hints drop out: j/k first, then the actions from last to first, then esc and q. enter, /, and ?
    stay, and ? lists every key."""
    actions = list(actions)
    items = [("j/k", "move", len(actions) + 3)] if move else []
    if slash:
        items.append(("/", slash))
    if toggle:
        items.append(("space", "toggle"))
    if enter:
        items.append(("enter", enter))
    items += [(key, label, i + 3) for i, (key, label) in enumerate(actions)]
    items.append(("?", "help"))
    if clear_filter:
        items.append(("esc", "clear filter", 2))
    items.append(("q", back, 1))
    return help_row(*items)


TYPING_FOOTER = [("enter", "done"), ("esc", "clear")]


def help_row(*items):
    """Key hints: (key, label) pairs, with an optional rank for dropping them in a narrow pane."""
    row = []
    for key, label, *rank in items:
        row += [(key, style("bold")) + tuple(rank), (" " + label + "   ", style("dim")) + tuple(rank)]
    return row


def search_row(text, filtering, placeholder):
    """An input: the placeholder while it's empty and inactive, otherwise just its text. The
    cursor shows while typing."""
    if filtering or text:
        return [(text, 0)]
    return [(placeholder, style("dim"))]


NAVIGATION_HELP = [
    ("j / k", "move down / up"),
    ("gg / G", "first / last"),
    ("ctrl+d / ctrl+u", "half page down / up"),
    ("ctrl+f / ctrl+b", "page down / up"),
]
CLOSE_HELP = ("q / esc", "go back, or cancel")


def show_help(stdscr, title, sections):
    """List a screen's keys. j/k and the other motions scroll it when it doesn't fit."""
    top, pending = 0, None
    keyboard.set_timeout(stdscr, -1)
    while True:
        body = help_body(sections, space(stdscr))
        height, _ = stdscr.getmaxyx()
        visible = max(1, height - 4)
        scrolls = len(body) > visible
        top = min(top, max(0, len(body) - visible))
        rows = [[], header(title + " — keys", util.STATE.get("label", ""))] + body[top:top + visible]
        rows += [[]] * max(0, height - len(rows) - 1)
        rows.append(help_row(*([("j/k", "scroll")] if scrolls else []), ("q", "back")))
        render(stdscr, rows)
        key = keyboard.read_key(stdscr)
        if key in keyboard.CLOSE or key in keyboard.ENTER or key == "?":
            return
        last = max(0, len(body) - visible)
        moved, pending = keyboard.motion(key, top, last + 1, visible, pending)
        if moved is not None:
            top = moved


def help_body(sections, width):
    """The help screen's rows: each key, then its description, wrapped to fit beside the keys."""
    body = []
    for heading, items in sections:
        body += [[], section(heading)]
        keys = max(len(k) for k, _ in items)
        for key, label in items:
            for i, line in enumerate(wrap(label, width - keys - 3)):
                body.append([((key if i == 0 else "").ljust(keys), style("bold")), ("   " + line, 0)])
    return body


def show_error(stdscr, message):
    def rows():
        lines = wrap(util.ICONS["error"] + " " + message, space(stdscr))
        return ([[], header("Worktree operation failed", util.STATE.get("label", "")), []]
                + [[(line, style("red"))] for line in lines] + [[], help_row(("q", "close"))])
    wait_to_close(stdscr, rows)


def wait_to_close(stdscr, rows):
    """Show rows, a function that returns them for the pane's size, until q, esc, enter, or ? is
    pressed, like closing any other screen."""
    keyboard.set_timeout(stdscr, -1)
    render(stdscr, rows())
    while True:
        key = keyboard.read_key(stdscr)
        if key == curses.KEY_RESIZE:
            render(stdscr, rows())
        elif key in keyboard.CLOSE or key in keyboard.ENTER or key == "?":
            return


def confirm(stdscr, title, repo, lines, keys, enter, help_items):
    """Show lines and wait for one of keys (a dict of key -> result). q and esc cancel."""
    keyboard.set_timeout(stdscr, -1)
    while True:
        rows = [[], header(title, repo), []]
        rows += [line if isinstance(line, list) else [line] for line in lines()]
        rows += [[], footer(enter=enter, back="cancel")]
        render(stdscr, rows)
        key = keyboard.read_key(stdscr)
        if key in keyboard.CLOSE:
            return None
        if key == "?":
            show_help(stdscr, title, [("Keys", help_items + [CLOSE_HELP])])
            continue
        if key in keyboard.ENTER:
            key = "\n"
        if key in keys:
            result = keys[key]()
            if result is not None:
                return result


def pointer(selected):
    return ("› " if selected else "  ", style("cyan") | style("bold"))
