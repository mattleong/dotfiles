"""Drawing: colors, rows, columns, and shared screens such as help and confirmation."""
import curses
import os
import re
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


def wrap_path(text, width):
    """Like wrap, but breaking a long path after a slash rather than inside a folder's name."""
    width = max(1, width)
    lines, line = [], ""
    for part in (p for p in re.split(r"(?<=[/ -])", text) if p):
        if line and len(line) + len(part.rstrip()) > width:
            lines.append(line.rstrip())
            line = ""
        while len(part) > width:
            lines.append(part[:width])
            part = part[width:]
        line += part
    return lines + [line.rstrip()] if line.strip() or not lines else lines


SHORT = 20  # Panes shorter than this drop the blank row above each screen's title.


def margin(height):
    return 0 if height < SHORT else 1


def compose(height, top, body, bottom):
    """A whole screen: the top rows, the body cut to the rows left, and the bottom rows (status
    and key hints) pinned to the bottom of the pane, so they never scroll off."""
    pad = margin(height)
    room = max(0, height - pad - len(top) - len(bottom))
    body = list(body[:room]) + [[]] * (room - len(body))
    return [[]] * pad + list(top) + body + list(bottom)


GAP = ("gap", [[]])


def fixed(*rows):
    """Rows that always show."""
    return ("fixed", list(rows))


def extra(*rows):
    """Rows that drop out when nothing else is left to shrink."""
    return ("extra", list(rows))


def items(rows, indent="  "):
    """A list that shortens to its first rows and a line such as "…and 12 more"."""
    return ("items", list(rows), indent)


LIST_COMFORT = 4  # Lists shorten to this many rows before the blank gaps between parts drop out.


def fit_parts(parts, room):
    """Fit a screen's body parts in room rows. Lists shorten first, to LIST_COMFORT rows each;
    then the blank gaps drop out; then lists shorten further, each keeping at least two rows when
    it keeps any (the heading above it still gives the count). Extra rows drop out only when the
    lists can't make enough room. Returns the rows."""
    parts = list(parts)
    lists = [p for p in parts if p[0] == "items"]

    def others():
        return sum(len(p[1]) for p in parts if p[0] != "items")

    comfort = sum(min(len(p[1]), LIST_COMFORT) for p in lists)
    while others() + comfort > room and GAP in parts:
        parts.remove(GAP)
    while others() > room and any(p[0] == "extra" for p in parts):
        parts.remove(next(p for p in reversed(parts) if p[0] == "extra"))
    budget = room - others()
    if budget >= sum(len(p[1]) for p in lists):
        return [row for part in parts for row in part[1]]
    # Share the rows out a little at a time, so every list keeps some.
    given = {id(p): 0 for p in lists}
    progress = True
    while progress:
        progress = False
        for p in lists:
            count = len(p[1])
            step = min(count, 2) if given[id(p)] == 0 else 1
            if given[id(p)] < count and 0 < step <= budget:
                given[id(p)] += step
                budget -= step
                progress = True
    out = []
    for part in parts:
        rows = part[1]
        if part[0] == "items" and given[id(part)] < len(rows):
            shown = max(0, given[id(part)] - 1)
            more = [[(f"{part[2]}…and {len(rows) - shown} more", style("dim"))]] if given[id(part)] else []
            rows = rows[:shown] + more
        out += rows
    return out


def scroll_top(top, index, count, capacity):
    """The first visible row of a list, moved only as far as needed to keep index in view."""
    return max(0, min(max(top, index - capacity + 1), index, max(0, count - capacity)))


def with_position(row, index, count, capacity, width):
    """Add the selected row's position, such as 8/17, at the right of row when the list scrolls."""
    if count <= capacity:
        return row
    mark = f"{index + 1}/{count}"
    row = list(row)
    while row and not row[-1][0].strip():
        row.pop()
    if row:
        row[-1] = (row[-1][0].rstrip(),) + tuple(row[-1][1:])
    used = len("".join(s[0] for s in row))
    if used + 2 + len(mark) > width:
        return row
    return row + [(" " * (width - used - len(mark)), 0), (mark, style("dim"))]


def input_view(text, pos, width):
    """The part of a line of input to show in width columns, scrolled to keep the cursor in view,
    with an ellipsis where text is hidden. Returns (visible text, cursor column)."""
    width = max(3, width)
    if len(text) < width:
        return text, pos
    start = max(0, min(pos - (width - 2), len(text) + 1 - width))
    visible = text[start:start + width]
    if start:
        visible = "…" + visible[1:]
    if start + width < len(text):
        visible = visible[:-1] + "…"
    return visible, pos - start


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
MIN_FOLDER = 8  # Narrower than this, the folder column is hidden.


def column_widths(entries, space=None):
    """Widths for the list's columns, sized to every worktree so they don't shift while filtering.
    A column that's empty for every worktree gets width 0 and is hidden. Given the space available,
    the branch column gets all it needs and the folder column, which comes last, gets what's left,
    or is hidden when that's under MIN_FOLDER. Then the branch narrows; when even that isn't
    enough, rows turn compact: the branch, its age, and the labels' icons."""
    longest = max([len(e["name"]) for e in entries] + [6])
    widths = {"BRANCH": longest, "compact": False}
    for title, cell in CELLS:
        width = max([cell_width(cell(e)) for e in entries] + [0])
        widths[title] = max(width, len(title)) if width else 0
    widths["SOURCE"] = max([len(worktrees.worktree_source(e)) for e in entries] + [0])
    folder = max([len(os.path.basename(e["worktree"])) for e in entries] + [0])
    widths["FOLDER"] = max(len("FOLDER"), (widths["SOURCE"] + 1 if widths["SOURCE"] else 0) + folder)
    if space is None:
        return widths
    rest = 4 + 2 + 5 + sum(widths[t] + 3 for t, _ in CELLS if widths[t])
    left = space - rest - longest
    if left >= MIN_FOLDER:
        widths["FOLDER"] = min(widths["FOLDER"], left)
        return widths
    widths["FOLDER"] = 0
    if space - rest >= min(MIN_BRANCH, longest):
        widths["BRANCH"] = min(longest, space - rest)
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
    if widths["FOLDER"]:
        row.append(("FOLDER", dim))
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
    if widths["FOLDER"]:
        # The tool that created the worktree, if any, then its folder.
        if widths["SOURCE"]:
            row.append((worktrees.worktree_source(entry).ljust(widths["SOURCE"]) + " ", style("blue")))
        row += highlighted(os.path.basename(entry["worktree"]), folder_hits, style("dim"))
    return Selected(row) if selected else row


class Flash:
    """A status message. Errors stay until the next key; others fade after 4 seconds."""
    SECONDS = 4
    ICONS = {"green": "ok", "red": "error"}
    LINES = 3  # A longer message is cut; Manager.say shows long errors on their own screen.

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
        if self.tone == "red":
            return float("inf")
        left = self.SECONDS - (time.monotonic() - self.at)
        if left <= 0:
            self.text = None
            return None
        return left

    def wait(self, wait):
        """The input timeout to use, shortened so the screen redraws when the message fades."""
        left = self.left()
        if left is None or left == float("inf"):
            return wait
        ms = int(left * 1000) + 50
        return ms if wait < 0 else min(wait, ms)

    def rows(self, width):
        """The message wrapped to width, in up to LINES rows; none when there's no message."""
        if self.left() is None:
            return []
        icon = util.ICONS[self.ICONS.get(self.tone, "info")]
        lines = wrap(icon + " " + self.text, width)
        if len(lines) > self.LINES:
            lines = lines[:self.LINES]
            lines[-1] = lines[-1][:width - 1] + "…"
        return [[(line, style(self.tone))] for line in lines]


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


def typing_footer(enter):
    """The key hints while typing in a filter: enter acts on the selected row at once."""
    return help_row(("enter", enter), ("↑/↓", "move", 2), ("tab", "done", 1), ("esc", "clear"))


def help_row(*items):
    """Key hints: (key, label) pairs, with an optional rank for dropping them in a narrow pane."""
    row = []
    for key, label, *rank in items:
        row += [(key, style("bold")) + tuple(rank), (" " + label + "   ", style("dim")) + tuple(rank)]
    return row


def search_row(text, pos, filtering, width):
    """A filter input, scrolled to keep the cursor in view. Returns (row, cursor column or None)."""
    if not filtering and not text:
        return [("/ to filter", style("dim"))], None
    prompt = ("/ ", style("cyan") | style("bold") if filtering else style("dim"))
    if not text:
        return [prompt, ("type to filter", style("dim"))], 2
    visible, column = input_view(text, pos, width - 2)
    return [prompt, (visible, 0)], (2 + column if filtering else None)


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
        if margin(stdscr.getmaxyx()[0]) == 0 and body[:1] == [[]]:
            body = body[1:]
        height, _ = stdscr.getmaxyx()
        visible = max(1, height - margin(height) - 2)
        scrolls = len(body) > visible
        top = min(top, max(0, len(body) - visible))
        title_row = with_position(header(title + " — keys", util.STATE.get("label", "")),
                                  top + visible - 1, len(body), visible, space(stdscr))
        rows = compose(height, [title_row], body[top:top + visible],
                       [help_row(*([("j/k", "scroll")] if scrolls else []), ("q", "back"))])
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
        if keys > width // 3:
            # Wide keys, such as the labels' samples, go above their descriptions.
            for key, label in items:
                body += ([[(key, style("bold"))]] if key else [])
                body += [[("    " + line, 0)] for line in wrap(label, width - 4)]
            continue
        for key, label in items:
            for i, line in enumerate(wrap(label, width - keys - 3)):
                body.append([((key if i == 0 else "").ljust(keys), style("bold")), ("   " + line, 0)])
    return body


def show_error(stdscr, message, title="Something went wrong", hint=""):
    """Show a message too long for the status line, wrapped, until it's closed."""
    def rows():
        width, height = space(stdscr), stdscr.getmaxyx()[0]
        lines = [[(line, style("red"))] for line in wrap(util.ICONS["error"] + " " + message, width)]
        lines += [[]] + [[(line, style("dim"))] for line in wrap(hint, width)] if hint else []
        return compose(height, [header(title, util.STATE.get("label", "")), []], lines, [help_row(("q", "close"))])
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


def confirm(stdscr, title, repo, parts, keys, enter, help_items):
    """Show body parts (see fit_parts), given by a function of the pane's width, and wait for one
    of keys (a dict of key -> result). q and esc cancel."""
    keyboard.set_timeout(stdscr, -1)
    while True:
        height = stdscr.getmaxyx()[0]
        top, bottom = [header(title, repo), []], [[], footer(enter=enter, back="cancel")]
        body = fit_parts(parts(space(stdscr)), height - margin(height) - len(top) - len(bottom))
        render(stdscr, compose(height, top, body, bottom))
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
