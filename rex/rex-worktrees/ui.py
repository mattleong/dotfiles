"""Drawing: colors, rows, columns, and shared screens such as help, details, and errors."""
import curses
import os
import re
import textwrap
import time

import branching
import keyboard
import util
import worktrees


COLORS = {}
LAST_FRAME = {"rows": [], "cursor": None}  # What render drew last, so a spinner can draw over it.
SELECTED_PAIRS = {}  # Color pair number -> the same colors on the selected-row background.

# Each role's color. Screens ask for a role, never a color, so a color keeps one meaning wherever it
# shows; only this module names colors (see the README's design rules).
ROLES = {
    "select": "cyan",  # The › pointer, input prompts, checkboxes, the spinner, the sort note.
    "live": "green",  # The current worktree's ●, tab, an open PR, success messages, the create row.
    "attention": "yellow",  # Uncommitted changes, warning headings, a key pressed where it can't act.
    "danger": "red",  # A missing folder, a broken worktree, errors, a closed PR, “Discard uncommitted changes”.
    "finished": "magenta",  # A merged PR, a remote branch that's gone.
    "source": "blue",  # The main checkout, remotes, the tools that made worktrees, a commit's author.
    "dim": "dim",  # Metadata: ages, paths, hints, a draft PR, locked.
    # A commit's hash, the color git log gives it. It shows only in commit lists, where nothing
    # else is yellow, so it can't be taken for a warning.
    "hash": "yellow",
}


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
    COLORS.update(dim=curses.A_DIM, bold=curses.A_BOLD, underline=curses.A_UNDERLINE)
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
    """The attribute for a role (see ROLES), or for bold or underline. A color's own name gives
    nothing: outside this module, colors are asked for by role."""
    return COLORS.get(ROLES.get(name, name if name in ("bold", "underline") else None), 0)


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
    """Lines of text no wider than width, at least one. Words break only at spaces, so a branch
    such as fix/login-flow stays whole when it fits. A word too long for a line, such as a long
    path, breaks after a slash, and after a dash only where the part between slashes is too long
    itself; never inside a word unless that's too long as well. The spaces inside a line stay as
    they are, as in a command, where they can matter."""
    width = max(1, width)
    if all(len(w) <= width for w in text.split()):
        return textwrap.wrap(text, width, break_on_hyphens=False) or [""]
    text = re.sub(r"\s", " ", text.expandtabs())  # As textwrap does.
    parts = []
    # Each word, with the spaces after it; the first has none when the text starts with an indent.
    for word, spaces in re.findall(r"(\S*)( *)", text):
        pieces = [word]
        if len(word) > width:
            pieces = []
            for piece in re.split(r"(?<=[^/]/)", word):
                pieces += [p for p in re.split(r"(?<=-)", piece) if p] if len(piece) > width else [piece]
        pieces[-1] += spaces
        parts += [p for p in pieces if p]
    return break_at(parts, width)


def wrap_lines(text, width):
    """Like wrap, but keeping the text's own line breaks, such as in a message from gh or Git."""
    return [line for paragraph in text.strip().splitlines() or [""] for line in wrap(paragraph, width)]


def hang(icon, text, width):
    """An icon and its text, wrapped to width, keeping the text's line breaks, with the lines after
    the first indented under the text rather than the icon."""
    pad = len(icon) + 1
    return [(icon + " " if i == 0 else " " * pad) + line for i, line in enumerate(wrap_lines(text, width - pad))]


def clip(lines, limit, width):
    """At most limit lines, the last ending in an ellipsis when any were left out."""
    if len(lines) <= limit:
        return lines
    lines = lines[:max(1, limit)]
    return lines[:-1] + [lines[-1][:width - 1].rstrip() + "…"]


def wrap_path(text, width):
    """Like wrap, but breaking a long path after a slash rather than inside a folder's name: after
    a dash only when a folder's name is too long for a line."""
    width = max(1, width)
    whole = [p for p in re.split(r"(?<=[/ ])", text) if p]
    if all(len(p.rstrip()) <= width for p in whole):
        return break_at(whole, width)
    return break_at([p for p in re.split(r"(?<=[/ -])", text) if p], width)


def break_at(parts, width):
    """Join parts into lines no wider than width, splitting a part only when it's wider itself.
    A line after the first starts at a word, without the spaces before it; an indent that leaves
    no room for the word after it drops out."""
    lines, line = [], ""
    for part in parts:
        if len(line) + len(part.rstrip()) > width:
            if line.strip():
                lines.append(line.rstrip())
            line = ""
        if not line and lines:
            part = part.lstrip()
        while len(part.rstrip()) > width:
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


def items(rows, indent="  ", heading=None, summary=None, hint=""):
    """A list, under an optional heading, that shortens to its first rows and a line such as
    "…and 12 more", followed by hint. Given a summary row, such as "16 uncommitted files", the list
    shrinks to just that row rather than vanishing."""
    return ("items", list(rows), indent, [heading] if heading else [], summary, hint)


LIST_COMFORT = 4  # Lists shorten to this many rows before the blank gaps between parts drop out.


def full_size(part):
    return len(part[1]) + (len(part[3]) if part[0] == "items" else 0)


def fit_parts(parts, room):
    """Fit a screen's body parts in room rows. Lists shorten first, to LIST_COMFORT rows each;
    then the blank gaps drop out, then the extra rows; then lists shorten further, each keeping at
    least its summary row, or else its heading and two rows when it keeps any. Lists that can be
    shown whole get the rows first, smallest first. Returns the rows."""
    parts = list(parts)
    lists = [p for p in parts if p[0] == "items"]

    def others():
        return sum(len(p[1]) for p in parts if p[0] != "items")

    comfort = sum(min(full_size(p), len(p[3]) + LIST_COMFORT) for p in lists)
    while others() + comfort > room and GAP in parts:
        parts.remove(GAP)
    while others() + comfort > room and any(p[0] == "extra" for p in parts):
        parts.remove(next(p for p in reversed(parts) if p[0] == "extra"))
    budget = room - others()
    if budget >= sum(full_size(p) for p in lists):
        return [row for part in parts for row in (part[3] + part[1] if part[0] == "items" else part[1])]
    given = {id(p): 0 for p in lists}
    for p in lists:  # Every summary first, so no list vanishes.
        if p[4] and budget >= 1:
            given[id(p)], budget = 1, budget - 1
    for p in sorted(lists, key=full_size):  # Then whole lists, smallest first.
        if full_size(p) - given[id(p)] <= budget:
            budget -= full_size(p) - given[id(p)]
            given[id(p)] = full_size(p)
    progress = True
    while progress:  # Then the rows left, a little at a time, so every list keeps some.
        progress = False
        for p in lists:
            least = len(p[3]) + 2
            target = min(full_size(p), least) if given[id(p)] < least else given[id(p)] + 1
            step = target - given[id(p)]
            if given[id(p)] < full_size(p) and 0 < step <= budget:
                given[id(p)] = target
                budget -= step
                progress = True
    out = []
    for part in parts:
        if part[0] != "items":
            out += part[1]
            continue
        _, rows, indent, heading, summary, hint = part
        size = given[id(part)]
        if size >= full_size(part):
            out += heading + rows
        elif size >= len(heading) + 2:
            shown = size - len(heading) - 1
            out += heading + rows[:shown] + [[(f"{indent}…and {len(rows) - shown} more{hint}", style("dim"))]]
        elif size and summary:
            out.append(summary)
    return out


def all_rows(parts):
    """Every row of a screen's body parts, with nothing shortened, for a details screen."""
    return fit_parts(parts, sum(full_size(p) for p in parts))


def cut(parts, room):
    """Whether fitting the parts in room rows leaves anything out."""
    return sum(full_size(p) for p in parts) > room


def scroll_top(top, index, count, capacity):
    """The first visible row of a list, moved only as far as needed to keep index in view."""
    return max(0, min(max(top, index - capacity + 1), index, max(0, count - capacity)))


def with_position(row, index, count, capacity, width):
    """Add the selected row's position, such as 8/17, at the right of row when the list scrolls."""
    if count <= capacity:
        return row
    return with_mark(row, f"{index + 1}/{count}", width)


def with_mark(row, mark, width):
    """Add mark, dim, at the right of row, where there's room for it."""
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
    """Split text into segments, the characters at the hit positions bold and underlined, with no
    color of their own, since a color means a state."""
    segments, start = [], 0
    for i in range(1, len(text) + 1):
        if i == len(text) or (i in hits) != (start in hits):
            hit = start in hits
            segments.append((text[start:i], (style("bold") | style("underline")) if hit else attr))
            start = i
    return segments


# A worktree's states and their labels: one word each, the same at every width, and its role. Each
# label's icon is util.ICONS under the same name. Sentences elsewhere, such as the list's summary,
# use the same words (see word).
LABELS = {"main": ("main", "source"), "tab": ("tab", "live"), "dirty": ("changes", "attention"),
          "locked": ("locked", "dim"), "missing": ("missing", "danger"), "broken": ("broken", "danger"),
          "deleted": ("gone", "finished")}


def word(state):
    """A state's word, such as "changes" for uncommitted changes (see LABELS)."""
    return LABELS[state][0]


def label(state):
    """A state's label as a segment: its icon and word, in its role (see LABELS)."""
    text, role = LABELS[state]
    return util.ICONS[state] + " " + text, style(role)


def pr_cell(entry, short=False):
    """The pull request's number and state. short leaves out the icon, for New worktree's branch
    rows in a narrower pane."""
    pr = entry.get("pr")
    if not pr:
        return []
    state = "draft" if pr.get("isDraft") and pr["state"] == "OPEN" else pr["state"].lower()
    role = {"open": "live", "merged": "finished", "closed": "danger"}.get(state, "dim")
    text = f"#{pr['number']} {state}"
    return [(text if short else f"{util.ICONS['pr']} {text}", style(role))]


def sync_cell(entry):
    """Commits ahead of and behind the remote branch, such as ↑2 and ↓3, in plain text, since
    they're a fact rather than a state, or that the remote branch is gone."""
    cell = []
    for part in entry.get("track") or []:
        if part == worktrees.REMOTE_DELETED:
            cell.append(label("deleted"))
        else:
            count, _, direction = part.partition(" ")
            cell.append((util.ICONS[direction] + count, 0))
    return cell


def state_cell(entry):
    shown = [("main", entry.get("main")), ("tab", entry.get("open") is True),
             ("dirty", entry.get("dirty") is True), ("locked", "locked" in entry),
             ("missing", worktrees.missing(entry)), ("broken", worktrees.broken(entry))]
    cell = [label(key) for key, on in shown if on]
    # A row with nothing to show yet says its status is still loading; one with a label already
    # doesn't, so the mark can't make the column wider for a moment (see column_widths).
    if not cell and util.PENDING in (entry.get("dirty"), entry.get("open")):
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
    """A cell's labels as just their icons and counts, such as ↑2, or #12 for a pull request, for
    narrow panes."""
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


def joined_rows(cell, width, gap=" · "):
    """A cell's labels joined as by joined, in as many rows as they need to fit in width, breaking
    only between labels."""
    rows = [[]]
    for text, attr in cell:
        used = sum(len(t) for t, _ in rows[-1])
        if rows[-1] and used + len(gap) + len(text) > width:
            rows.append([])
        rows[-1] += ([(gap, style("dim"))] if rows[-1] else []) + [(text, attr)]
    return rows if rows[0] else []


def middle(text, width, hits=()):
    """text cut to width by taking out its middle, keeping more of its end, where names such as
    repo-login and repo-long differ. Returns (text, the hit positions moved to match)."""
    if len(text) <= width:
        return text, set(hits)
    if width < 2:
        return text[:max(0, width)], {h for h in hits if h < width}
    head = (width - 1) // 3
    tail = width - 1 - head
    cut = len(text) - tail
    moved = {h for h in hits if h < head} | {h - cut + head + 1 for h in hits if h >= cut}
    return text[:head] + "…" + text[cut:], moved


CELLS = [("PR", pr_cell), ("SYNC", sync_cell), ("STATE", state_cell)]
MAX_BRANCH = 40  # So one long branch can't push the other columns away; the preview shows it whole.
MIN_BRANCH = 28  # The branch keeps this much before the labels turn into icons.
MIN_FOLDER = 12  # Narrower than this, the folder column is hidden.


def folder_prefix(entries):
    """The main checkout's folder name and a dash, such as "app-", which the other worktrees'
    folders often start with; the folder column leaves it out."""
    main = next((e for e in entries if e.get("main")), None)
    return os.path.basename(main["worktree"]) + "-" if main else ""


def folder_name(entry, prefix=""):
    """The worktree's folder name, without the prefix the worktrees share, or nothing when it just
    repeats the branch, as for worktrees the picker created. Returns (name, how many characters
    were left out)."""
    name = os.path.basename(entry["worktree"])
    if prefix and name.startswith(prefix) and len(name) > len(prefix):
        name, skipped = name[len(prefix):], len(prefix)
    else:
        skipped = 0
    if "branch" in entry and name in (branching.folder_slug(entry["name"]), branching.folder_slug(entry["name"], True)):
        return "", 0
    return name, skipped


def column_widths(entries, space=None):
    """Widths for the list's columns, sized to every worktree so they don't shift while filtering.
    A column that's empty for every worktree gets width 0 and is hidden. The branch column gets
    what its longest name needs, up to MAX_BRANCH; longer names lose their middle (see middle).
    Given the space available, the folder column, which comes last, gets what's left, or is hidden
    when that's under MIN_FOLDER. When the labels don't fit beside the branch, it narrows, to
    MIN_BRANCH at most. When even that isn't enough, rows turn compact: the branch, its age, and
    the labels' icons. Folders leave out the main checkout's folder name, which the others' often
    start with, and are left blank when they just repeat the branch. The folder column starts
    with the tool that made each worktree, if any, in a narrow column of its own."""
    longest = min(MAX_BRANCH, max([len(e["name"]) for e in entries] + [6]))
    widths = {"BRANCH": longest, "compact": False}
    for title, cell in CELLS:
        width = max([cell_width(cell(e)) for e in entries] + [0])
        widths[title] = max(width, len(title)) if width else 0
    if widths["STATE"]:
        # Room for changes, on purpose: it's the label that most often arrives after the list
        # shows, and the columns after STATE would shift when it did. Otherwise STATE is as wide
        # as its widest cell.
        widths["STATE"] = max(widths["STATE"], cell_width([label("dirty")]))
    # The tools that made worktrees, such as claude, get an untitled column of their own before
    # the folders' names, on purpose, so the names line up under FOLDER even when only some rows
    # have a tool; it's as wide as the longest tool's name, and left out when no row has one.
    widths["SOURCE"] = max([len(worktrees.worktree_source(e)) for e in entries] + [0])
    widths["PREFIX"] = folder_prefix(entries)
    names = max([len(folder_name(e, widths["PREFIX"])[0]) for e in entries] + [0])
    tags = widths["SOURCE"] + 1 if widths["SOURCE"] else 0
    # The heading goes over the folders' names, after the tools' column; with no names to show,
    # just the tools, over them. With neither, every folder just repeats its branch: no column.
    if names:
        folder = tags + max(names, len("FOLDER"))
    elif tags:
        folder = max(widths["SOURCE"], len("FOLDER"))
    else:
        folder = 0
    widths["FOLDER"], widths["FOLDER_AT"] = folder, tags if names else 0
    if space is None:
        return widths

    rest = 4 + 2 + 5 + sum(widths[t] + 3 for t, _ in CELLS if widths[t])
    left = space - rest - longest
    if left >= MIN_FOLDER and folder:
        widths["FOLDER"] = min(folder, left)
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
        row.append((" " * widths.get("FOLDER_AT", 0) + "FOLDER", dim))  # Over the names, past the tools.
    return row


def entry_row(entry, selected, widths, match=None):
    _, name_hits, folder_hits = match or (0, set(), set())
    width = widths["BRANCH"]
    # A long name loses its middle, keeping its start and its end, where similar names differ.
    name, name_hits = middle(entry["name"], width, name_hits)
    row = [pointer(selected), ("● " if entry.get("current") else "  ", style("live"))]
    row += highlighted(name, name_hits, style("bold") if selected else 0)
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
        # The tool that created the worktree, if any, then its folder, cut in the middle to fit.
        room = widths["FOLDER"]
        if widths["SOURCE"]:
            row.append((worktrees.worktree_source(entry).ljust(widths["SOURCE"]) + " ", style("source")))
            room -= widths["SOURCE"] + 1
        folder, skipped = folder_name(entry, widths.get("PREFIX", ""))
        if folder:
            folder, hits = middle(folder, max(1, room), {h - skipped for h in folder_hits if h >= skipped})
            row += highlighted(folder, hits, style("dim"))
    return Selected(row) if selected else row


class Flash:
    """A status message, in a role (see ROLES): danger for an error, live for success, attention
    for a key that can't act, or dim. Errors stay until the next key; others fade after 4 seconds."""
    SECONDS = 4
    ICONS = {"live": "ok", "danger": "error"}
    LINES = 3  # A longer message is cut; Manager.say shows long errors on their own screen.

    def __init__(self):
        self.text, self.tone, self.at = None, "danger", 0.0

    def set(self, text, tone="danger"):
        self.text, self.tone, self.at = text, tone, time.monotonic()

    def clear(self):
        self.text = None

    def left(self):
        """Seconds until it fades, or None when there's no message. Clears an expired one."""
        if not self.text:
            return None
        if self.tone == "danger":
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
        return [[(line, style(self.tone))] for line in clip(hang(icon, self.text, width), self.LINES, width)]


def section(title, extra="", tone="dim"):
    """A heading inside a screen, such as EXISTING BRANCHES. A warning's heading is in the
    attention role (see ROLES)."""
    return [(title.upper(), style(tone) | style("bold"))] + ([("  " + extra, style("dim"))] if extra else [])


def header(title, repo, extra="", note=""):
    """The first line of every screen: its title and the repository, then extra details and a
    note, such as how the list is sorted. In a narrow pane, the extra details drop out first, then
    the note, then the repository."""
    return ([(title, style("bold")), ("  " + repo, style("dim"), 1)]
            + ([("   " + extra, style("dim"), 3)] if extra else [])
            + ([("   " + note, style("select"), 2)] if note else []))


class Keys:
    """The keys a screen offers in its current state, in the order its footer lists them, each
    with whether it can act now. The footer (see row) lists only the ones that can, and the
    screen asks refused before acting on a key, which says why it can't; both come from the same
    answer, so a footer never lists a key the screen would refuse.

    A screen builds its Keys afresh for every frame, adding each key it handles. In a narrow
    pane the footer drops its keys by rank, highest first: by convention j/k first, then the
    screen's own actions from last to first, then tab, esc, and q; enter, the key that starts
    typing (the list's /, New worktree's i), and ? stay. On a screen that confirms something, q
    stays instead, and ? goes before tab."""

    def __init__(self):
        self.offered = {}  # Each key's name -> (label, rank, whether it can act, why not).

    def add(self, key, label="", rank=0, can=True, why=""):
        """Offer key, by the name the footer gives it, such as enter, dd, or ↑/↓, to do label.
        It can't act now when can is False or there's a why: what pressing it says. A screen that
        explains a refusal another way, such as by making a note stand out, leaves why empty.
        With no label, the key works but isn't listed, as for keys only help lists. Returns self."""
        self.offered[key] = (label, rank, can and not why, why)
        return self

    def can(self, key):
        """Whether key can act now. One that isn't offered can't."""
        return key in self.offered and self.offered[key][2]

    def refused(self, key, say=None):
        """Whether key can't act now. When it can't and there's a reason, say(reason) says it."""
        if self.can(key):
            return False
        why = self.offered.get(key, ("", 0, False, ""))[3]
        if why and say:
            say(why)
        return True

    def listed(self):
        """The keys the footer lists: the ones that can act now and have a label."""
        return [key for key, (label, _, can, _) in self.offered.items() if can and label]

    def row(self):
        """The footer's row (see help_row)."""
        return help_row(*[(key,) + self.offered[key][:2] for key in self.listed()])

    def fits(self, key, width):
        """Whether the footer still lists key once fitted in width columns."""
        return key in [text for text, _ in fit(self.row(), width)]


def help_row(*items):
    """Key hints: (key, label) pairs, with an optional rank for dropping them in a narrow pane
    (see fit); rank 0 never drops."""
    row = []
    for key, label, *rank in items:
        rank = tuple(r for r in rank if r)
        row += [(key, style("bold")) + rank, (" " + label + "   ", style("dim")) + rank]
    return row


SEARCH_MIN = 12  # The columns a filter's text keeps before a mark beside it drops out.


def search_row(text, pos, filtering, width, placeholder="type to filter", mark="", prompt="/ ", count="",
               idle=None):
    """A text input after its prompt, scrolled to keep the cursor in view. count, such as how many
    rows the text matches, goes right after the text, dim, so it reads with it, when the whole
    text fits beside it; mark, such as the selected row's place in a list that scrolls, goes dim
    at the right while the text keeps SEARCH_MIN columns. The list's prompt is /, the key that
    starts its filter; a screen whose prompt is a word, such as New worktree's, gives idle, what
    its input says, after the prompt, when it's empty and not being typed in, such as which key
    starts typing. Not typing, the prompt is dim and there's no cursor. Returns (row, cursor
    column or None)."""
    if mark and width - len(mark) - len(prompt) >= SEARCH_MIN:
        row, column = search_row(text, pos, filtering, width - len(mark) - 2, placeholder, prompt=prompt, count=count,
                                 idle=idle)
        return with_mark(row, mark, width), column
    if not filtering and not text and idle is None:
        return [("/ to filter", style("dim"))], None
    shown = (prompt, style("select") | style("bold") if filtering else style("dim"))
    if not text:  # Cut to its width, so a mark beside it still shows.
        room = max(1, width - len(prompt))
        placeholder = placeholder if filtering else idle
        placeholder = placeholder if len(placeholder) <= room else placeholder[:room - 1].rstrip() + "…"
        return [shown, (placeholder, style("dim"))], len(prompt) if filtering else None
    # The text and the cursor after it come first: the count shows only where they fit whole.
    tail = [("  " + count, style("dim"))] if count and len(prompt) + len(text) + 1 + 2 + len(count) <= width else []
    visible, column = input_view(text, pos, width - len(prompt))
    return [shown, (visible, 0)] + tail, (len(prompt) + column if filtering else None)


NAVIGATION_HELP = [
    ("j / k", "move down / up"),
    ("gg / G", "first / last"),
    ("5j / 5G", "a count first: 5j or 5k moves 5 rows, and 5G or 5gg goes to row 5; esc cancels it"),
    ("H / M / L", "the top / middle / bottom row in view"),
    ("ctrl+d / ctrl+u", "half page down / up"),
    ("ctrl+f / ctrl+b", "page down / up"),
]
CLOSE_HELP = ("q / esc", "go back, or cancel")


def show_help(stdscr, title, sections):
    """List a screen's keys."""
    page(stdscr, title + " — keys", lambda width: help_body(sections, width))


def page(stdscr, title, body_for, close="back"):
    """Show rows, given by body_for(width), below a title, until q, esc, enter, or ? closes them.
    When they don't fit, j/k and the other motions scroll them, and the title shows which rows are
    in view, such as 1–24 of 42."""
    top, pending = 0, None
    keyboard.set_timeout(stdscr, -1)
    while True:
        height, _ = stdscr.getmaxyx()
        body = body_for(space(stdscr))
        if margin(height) == 0 and body[:1] == [[]]:
            body = body[1:]
        visible = max(1, height - margin(height) - 2)
        scrolls = len(body) > visible
        top = min(top, max(0, len(body) - visible))
        title_row = header(title, util.STATE.get("label", ""))
        if scrolls:
            title_row = with_mark(title_row, f"{top + 1}–{top + visible} of {len(body)}", space(stdscr))
        keys = Keys().add("j/k", "scroll", can=scrolls).add("q", close)
        render(stdscr, compose(height, [title_row], body[top:top + visible], [keys.row()]))
        key = keyboard.read_key(stdscr)
        if key is None or key == curses.KEY_RESIZE:
            continue
        # A motion first, so esc cancels a count or a g typed partway rather than closing. There's
        # no H, M, or L: the index here is the scroll position, not a row.
        last = max(0, len(body) - visible)
        moved, pending = keyboard.motion(key, top, last + 1, visible, pending)
        if moved is not None:
            top = moved
        elif key in keyboard.CLOSE or key in keyboard.ENTER or key == "?":
            return


HELP_WIDTH = 80  # Help wraps at this width even in a wider pane, so its lines stay easy to read.
HELP_KEYS = 18  # A key wider than this, or than a quarter of the pane, goes on its own row.


def help_body(sections, width):
    """The help screen's rows: each key, then its description, wrapped beside it. The keys share
    one column across every section, so the descriptions line up; a key too wide for it, such as
    a list of tools, goes on its own row, above its description. A key can be a (text, attr)
    segment, such as a label in its role (see label)."""
    width = min(width, HELP_WIDTH)
    widest = min(HELP_KEYS, width // 4)
    texts = [key[0] if isinstance(key, tuple) else key for _, items in sections for key, _ in items]
    column = max([len(k) for k in texts if len(k) <= widest] + [0])
    body = []
    for heading, items in sections:
        body += [[], section(heading)]
        for key, text in items:
            key, attr = key if isinstance(key, tuple) else (key, style("bold"))
            if len(key) > column:
                body.append([(key, attr)])
                key = ""
            for i, line in enumerate(wrap(text, width - column - 3)):
                body.append([((key if i == 0 else "").ljust(column), attr), ("   " + line, 0)])
    return body


def tidy_error(message):
    """A message, such as Git's, made to read well wrapped to any width: its fatal: or error: line
    first, with what came before it, such as "Preparing worktree", after the rest; and each
    sentence Git broke across lines, such as its advice, joined again, so it wraps to the pane.
    A line starting with a lowercase letter continues the one before, unless it starts with a word
    and a colon, as hint: does; other lines, blank ones, and indented ones stay as they are."""
    lines = message.strip().splitlines()
    first = next((i for i, line in enumerate(lines) if re.match(r"(fatal|error):", line)), 0)
    if first:
        lines = lines[first:] + [""] + lines[:first]
    out = []
    for line in lines:
        if out and out[-1].strip() and line[:1].islower() and not re.match(r"[a-z]+:(\s|$)", line):
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
    return "\n".join(out)


def show_error(stdscr, message, title="Something went wrong", hint=""):
    """Show a message too long for the status line, wrapped, keeping its paragraphs (see
    tidy_error), until it's closed. j/k scroll it when it doesn't fit."""
    def body(width):
        lines = hang(util.ICONS["error"], tidy_error(message), width)
        rows = [[]] + [[(line, style("danger"))] for line in lines]
        return rows + ([[]] + [[(line, style("dim"))] for line in wrap(hint, width)] if hint else [])
    page(stdscr, title, body, close="close")


TAB_CUE = " (tab shows all)"  # After text cut short, when the footer has no room for "tab details".
DETAILS_HELP = ("tab", "everything this would do, in full, when the pane is too small to show it")


def show_details(stdscr, title, parts):
    """Every row of body parts (see fit_parts), scrolling. parts is a function of the pane's width
    and whether to show everything in full, wrapping what the compact screen cuts short."""
    page(stdscr, title + " — details", lambda width: [[]] + all_rows(parts(width, True)))


def row_text(row):
    return "".join(s[0] for s in row).rstrip()


def abridged(parts, full, room, width):
    """Whether a screen's body parts, fitted in room rows, leave anything out that details would
    show: rows cut to fit, or text cut short, compared with the parts in full."""
    rows = all_rows(parts)
    return (cut(parts, room) or any(len(row_text(r)) > width for r in rows)
            or [row_text(r) for r in rows] != [row_text(r) for r in all_rows(full)])


def pointer(selected):
    return ("› " if selected else "  ", style("select") | style("bold"))
