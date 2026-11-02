#!/usr/bin/env python3
"""Interactive Git worktree actions, launched in a Rex floating terminal."""
from concurrent.futures import ThreadPoolExecutor
import curses
import hashlib
import json
import locale
import os
from pathlib import Path
import re
import subprocess
import sys

TERMINAL = "com.superlogical.terminal"
COLORS = {}
# Set once a tab has been focused, so exiting doesn't pull focus back to the original pane.
STATE = {"navigated": False}


# Git and Rex ---------------------------------------------------------------

def run(*args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or f"{args[0]} failed")
    return result.stdout


def git(cwd, *args):
    return run("git", "-C", cwd, *args)


def git_text(cwd, *args):
    return os.fsdecode(git(cwd, *args)).strip()


def worktrees(cwd):
    raw = git(cwd, "worktree", "list", "--porcelain", "-z")
    entries, item = [], {}
    for field in raw.split(b"\0"):
        if not field:
            if item:
                entries.append(item)
                item = {}
            continue
        key, _, value = field.partition(b" ")
        item[key.decode()] = os.fsdecode(value)
    return entries


def inside(path, root):
    try:
        root = os.path.realpath(root)
        return os.path.commonpath((root, os.path.realpath(path))) == root
    except ValueError:
        return False


def session_windows(session):
    return json.loads(run("rex", "session", "inspect", session, "--json"))["windows"]


def terminal_cwds(session):
    """Return (window, cwd) for each terminal in the session, except this picker."""
    picker = os.environ.get("REX_BLOCK")
    blocks = [(window, block["block_id"])
              for window in session_windows(session)
              for layer in window.get("layers", [])
              for block in layer.get("blocks", [])
              if block.get("creator_name") == TERMINAL and block.get("block_id") != picker]

    def cwd(item):
        try:
            process = json.loads(run("rex", "block", "call", "-s", session, "-b", item[1], "process"))
        except (RuntimeError, ValueError, OSError):
            return None
        return (process.get("foreground") or {}).get("cwd") or (process.get("child") or {}).get("cwd")

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [(window, path) for (window, _), path in zip(blocks, pool.map(cwd, blocks)) if path]


def windows_for_path(session, path, terminals=None):
    """Find tabs with a terminal process currently at or below the worktree."""
    matches, seen = [], set()
    for window, cwd in terminal_cwds(session) if terminals is None else terminals:
        if window["window_id"] not in seen and inside(cwd, path):
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
        run("rex", "window", "close", "-s", session, window["window_id"])


def branch_name(entry):
    if "branch" in entry:
        return entry["branch"].removeprefix("refs/heads/")
    return "detached " + entry.get("HEAD", "")[:7]


def repo_name(cwd):
    """Name the repository after its main worktree, or its bare directory."""
    name = Path(worktrees(cwd)[0]["worktree"]).name
    return name[:-4] if name.endswith(".git") and len(name) > 4 else name


def tab_label(entry):
    return f"{repo_name(entry['worktree'])} - {branch_name(entry)}"[:128]


def open_tab(session, entry, client=None):
    path = entry["worktree"]
    matches = windows_for_path(session, path)
    if matches:
        window_id = matches[0]["window_id"]
        run("rex", "focus", "-s", session, window_id)
    else:
        created = run("rex", "window", "new", "-s", session, tab_label(entry), "-c", path, "--json")
        window_id = json.loads(created)["window_id"]
    STATE["navigated"] = True
    if client:
        # The server's active window doesn't move the app's view; ask the app itself to show the tab.
        try:
            run("rex", "-C", client, "do", "session.select", f"session_id={session}", f"window_id={window_id}")
        except RuntimeError as error:
            raise RuntimeError(f"Couldn't switch to the tab: {error}") from error
    return window_id


# Worktree state ---------------------------------------------------------------

def tracking(cwd):
    out = git_text(cwd, "for-each-ref", "--format=%(refname)%00%(upstream:track)", "refs/heads")
    result = {}
    for line in out.splitlines():
        ref, _, track = line.partition("\0")
        ahead = re.search(r"ahead (\d+)", track)
        behind = re.search(r"behind (\d+)", track)
        parts = (["↑" + ahead.group(1)] if ahead else []) + (["↓" + behind.group(1)] if behind else [])
        if "gone" in track:
            parts.append("gone")
        result[ref] = " ".join(parts)
    return result


def is_dirty(entry):
    if "prunable" in entry or not os.path.isdir(entry["worktree"]):
        return None
    try:
        return bool(git(entry["worktree"], "status", "--porcelain", "--untracked-files=normal"))
    except (RuntimeError, OSError):
        return None


def current_entry(entries, cwd):
    matches = [e for e in entries if inside(cwd, e["worktree"])]
    return max(matches, key=lambda e: len(os.path.realpath(e["worktree"])), default=None)


def load_entries(cwd, session):
    raw = worktrees(cwd)
    if not raw:
        raise RuntimeError("No worktrees found")
    raw[0]["main"] = True
    entries = [e for e in raw if "bare" not in e]
    tracks = tracking(cwd)
    terminals = terminal_cwds(session)
    current = current_entry(entries, cwd)
    with ThreadPoolExecutor(max_workers=8) as pool:
        dirty = list(pool.map(is_dirty, entries))
    for entry, state in zip(entries, dirty):
        entry["name"] = branch_name(entry)
        entry["current"] = entry is current
        entry["dirty"] = state
        entry["track"] = tracks.get(entry.get("branch"), "")
        entry["open"] = any(inside(path, entry["worktree"]) for _, path in terminals)
    return entries


def filter_entries(entries, query):
    tokens = query.lower().split()
    return [e for e in entries
            if all(t in (e.get("name", branch_name(e)) + " " + e["worktree"]).lower() for t in tokens)]


def removal_problem(entry):
    if entry.get("main"):
        return "The main worktree can't be removed."
    if "locked" in entry:
        return "This worktree is locked."
    if "prunable" in entry or not os.path.isdir(entry["worktree"]):
        return "This worktree's directory is missing; prune it with git worktree prune."
    try:
        status = git(entry["worktree"], "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")
    except (RuntimeError, OSError) as error:
        return str(error)
    if status:
        return "This worktree has uncommitted or untracked changes."
    return None


def remove_worktree(cwd, session, entry, windows):
    git(cwd, "worktree", "remove", "--", entry["worktree"])
    try:
        close_worktree_tabs(session, windows)
    except (RuntimeError, OSError) as error:
        raise RuntimeError(f"Worktree removed, but a tab could not be closed: {error}") from error


# Branches -------------------------------------------------------------------

def normalize_branch(value):
    """Turn a human-entered name into a Git-compatible branch name."""
    parts = []
    for part in value.strip().split("/"):
        part = re.sub(r"[\s\x00-\x1f\x7f~^:?*\[\\]+", "-", part)
        part = part.replace("@{", "-")
        part = re.sub(r"\.{2,}", "-", part)
        part = re.sub(r"-+", "-", part).strip(".-")
        if part.endswith(".lock"):
            part = part[:-5].rstrip(".-") + "-lock"
        if part:
            parts.append(part)
    branch = "/".join(parts)
    return "HEAD-branch" if branch == "HEAD" else branch


def new_path(cwd, branch):
    common = git_text(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    repo = repo_name(cwd)
    # Hash the common Git directory to distinguish repositories with the same name.
    repo_key = repo + "-" + hashlib.sha256(os.fsencode(os.path.realpath(common))).hexdigest()[:10]
    slug = branch.replace("/", "-")[:100]
    suffix = hashlib.sha256(branch.encode()).hexdigest()[:8]
    return Path.home() / ".local/share/rex/worktrees" / repo_key / (slug + "-" + suffix)


def load_branches(cwd, entries):
    """Collect local branches and remote-only branches, newest first."""
    checked_out = {e["branch"].removeprefix("refs/heads/"): e["worktree"] for e in entries if "branch" in e}
    local = git_text(cwd, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads").splitlines()
    remotes = sorted(git_text(cwd, "remote").splitlines(), key=len, reverse=True)
    remote = {}
    refs = git_text(cwd, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/remotes")
    for ref in refs.splitlines():
        for name in remotes:
            if ref.startswith(name + "/") and ref != name + "/HEAD":
                remote.setdefault(ref[len(name) + 1:], []).append(ref)
                break
    head = git_text(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if head == "HEAD":
        head = git_text(cwd, "rev-parse", "--short", "HEAD")
    suggestions = [{"name": b, "ref": b, "kind": "local"} for b in local if b not in checked_out]
    for name, refs in remote.items():
        if name not in local and name not in checked_out:
            suggestions += [{"name": name, "ref": ref, "kind": "remote"} for ref in refs]
    return {"local": set(local), "remote": remote, "checked_out": checked_out,
            "suggestions": suggestions, "head": head}


def plan_branch(cwd, branches, text, suggestion=None):
    """Describe what creating a worktree for the input would do."""
    if suggestion:
        name, ref = suggestion["name"], suggestion["ref"]
        if suggestion["kind"] == "local":
            plan = {"branch": name, "summary": "existing branch", "args": [name]}
        else:
            plan = {"branch": name, "summary": "track " + ref, "args": ["--track", "-b", name, None, ref]}
    else:
        name = normalize_branch(text)
        if not name:
            return {"error": "Enter a branch name." if not text.strip() else "No usable branch-name characters."}
        if subprocess.run(["git", "-C", cwd, "check-ref-format", "--branch", name],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            return {"branch": name, "error": "Not a valid branch name."}
        if name in branches["checked_out"]:
            where = branches["checked_out"][name].replace(str(Path.home()), "~", 1)
            return {"branch": name, "error": "Already checked out at " + where}
        refs = branches["remote"].get(name, [])
        tracked = refs[0] if len(refs) == 1 else next((r for r in refs if r.startswith("origin/")), None)
        if name in branches["local"]:
            plan = {"branch": name, "summary": "existing branch", "args": [name]}
        elif tracked:
            plan = {"branch": name, "summary": "track " + tracked, "args": ["--track", "-b", name, None, tracked]}
        else:
            plan = {"branch": name, "summary": "new branch from " + branches["head"],
                    "args": ["-b", name, None, "HEAD"]}
    path = new_path(cwd, plan["branch"])
    if path.exists():
        plan["error"] = "Destination already exists: " + str(path).replace(str(Path.home()), "~", 1)
    plan["path"] = path
    return plan


def create_worktree(cwd, plan):
    path = plan["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    args = plan["args"]
    if None in args:  # -b/--track forms put the path before the start point.
        args = [str(path) if a is None else a for a in args]
    else:
        args = [str(path), *args]
    git(cwd, "worktree", "add", *args)


# Drawing ----------------------------------------------------------------------

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
    for index, (name, color) in enumerate(
            [("green", curses.COLOR_GREEN), ("yellow", curses.COLOR_YELLOW),
             ("cyan", curses.COLOR_CYAN), ("red", curses.COLOR_RED), ("blue", curses.COLOR_BLUE)], 1):
        curses.init_pair(index, color, background)
        COLORS[name] = curses.color_pair(index)


def style(name):
    return COLORS.get(name, 0)


def render(stdscr, rows, cursor=None):
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    for y, row in enumerate(rows[:height]):
        x = 2
        for text, attr in row:
            if x >= width - 1:
                break
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


def tilde(path):
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def badges(entry):
    result = []
    if entry.get("main"):
        result.append(("primary", style("blue")))
    if entry.get("open"):
        result.append(("open", style("green")))
    if entry.get("dirty"):
        result.append(("modified", style("yellow")))
    if entry.get("track"):
        result.append((entry["track"], style("red") if "gone" in entry["track"] else style("cyan")))
    if "locked" in entry:
        result.append(("locked", style("red")))
    if "prunable" in entry or not os.path.isdir(entry["worktree"]):
        result.append(("missing", style("red")))
    return result


def entry_row(entry, selected, name_width, badge_width):
    row = [("› " if selected else "  ", style("cyan") | style("bold")),
           ("● " if entry.get("current") else "  ", style("green")),
           (entry["name"][:name_width].ljust(name_width), style("bold") if selected else 0), ("  ", 0)]
    used = 0
    for text, attr in badges(entry):
        row += [(text, attr), (" ", 0)]
        used += len(text) + 1
    row += [(" " * max(0, badge_width - used) + " ", 0), (tilde(entry["worktree"]), style("dim"))]
    return row


ENTER = ("\n", "\r", curses.KEY_ENTER)
BACKSPACE = ("\x7f", "\b", curses.KEY_BACKSPACE)
ESCAPE = "\x1b"


def edit_text(text, key):
    if key in BACKSPACE:
        return text[:-1]
    if key == "\x15":
        return ""
    if key == "\x17":
        return re.sub(r"\S*\s*$", "", text.rstrip())
    if isinstance(key, str) and key.isprintable():
        return text + key
    return text


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


def help_row(*items):
    row = []
    for key, label in items:
        row += [(key, style("bold")), (" " + label + "   ", style("dim"))]
    return row


# Screens ----------------------------------------------------------------------

def confirm(stdscr, title, lines):
    while True:
        rows = [[], [(title, style("bold"))], []]
        rows += [[(text, attr)] for text, attr in lines]
        rows += [[], help_row(("y/enter", "confirm"), ("n/q/esc", "cancel"))]
        render(stdscr, rows)
        key = stdscr.get_wch()
        if key in ENTER or key == "y":
            return True
        if key in (ESCAPE, "n", "q"):
            return False


def pick(stdscr, session, title, verb, entries, on_enter):
    query, message, filtering, pending = "", None, False, None
    selected = next((e for e in entries if e.get("current")), entries[0] if entries else None)
    name_width = min(40, max([len(e["name"]) for e in entries] + [8]))
    badge_width = max([sum(len(t) + 1 for t, _ in badges(e)) for e in entries] + [0])
    top = 0
    while True:
        shown = filter_entries(entries, query)
        if selected not in shown:
            selected = shown[0] if shown else None
        height, _ = stdscr.getmaxyx()
        capacity = max(1, height - 7)
        index = shown.index(selected) if selected in shown else 0
        top = min(max(top, index - capacity + 1), index, max(0, len(shown) - capacity))
        if filtering:
            search = [("/", style("cyan")), (query, 0)]
        elif query:
            search = [("/", style("dim")), (query, 0)]
        else:
            search = [("/ to filter", style("dim"))]
        rows = [[], [(title, style("bold"))], search, []]
        rows += [entry_row(e, e is selected, name_width, badge_width) for e in shown[top:top + capacity]]
        if not shown:
            rows.append([("No matching worktrees", style("dim"))])
        rows += [[]] * max(0, height - len(rows) - 2)
        if message:
            rows.append([(message, style("red"))])
        else:
            rows.append([(f"{len(shown)}/{len(entries)}", style("dim"))])
        if filtering:
            rows.append(help_row(("enter", "done"), ("esc", "clear")))
        else:
            rows.append(help_row(("j/k", "move"), ("/", "filter"), ("enter", verb),
                                 ("esc", "clear filter") if query else ("q/esc", "quit")))
        render(stdscr, rows, cursor=(2, 1 + len(query)) if filtering else None)
        key = stdscr.get_wch()
        message = None
        if key == curses.KEY_RESIZE:
            continue
        if filtering:
            if key in ENTER:
                filtering = False
            elif key == ESCAPE:
                filtering, query = False, ""
            elif key in BACKSPACE and not query:
                filtering = False
            else:
                new_index, _ = motion(key, index, len(shown), capacity, None) if key in (
                    curses.KEY_UP, curses.KEY_DOWN, "\x0e", "\x10") else (None, None)
                if new_index is not None:
                    selected = shown[new_index]
                else:
                    query = edit_text(query, key)
            continue
        new_index, pending = motion(key, index, len(shown), capacity, pending)
        if new_index is not None:
            selected = shown[new_index]
        elif key == "/":
            filtering = True
        elif key == ESCAPE and query:
            query = ""
        elif key in (ESCAPE, "q"):
            return
        elif key in ENTER or key == "l":
            if selected is None:
                message = "No worktree selected."
                continue
            try:
                done, message = on_enter(selected)
            except (RuntimeError, OSError) as error:
                done, message = False, str(error)
            if done:
                return


def new_worktree(stdscr, cwd, session, entries):
    branches = load_branches(cwd, entries)
    suggestions = branches["suggestions"]
    text, index, top, message, filtering, pending = "", 0, 0, None, False, None
    while True:
        tokens = text.lower().split()
        items = [s for s in suggestions if all(t in s["ref"].lower() for t in tokens)]
        typed = normalize_branch(text)
        if typed and typed not in {s["name"] for s in items}:
            items.insert(0, None)  # None is the new branch named by the filter text.
        index = min(index, max(0, len(items) - 1))
        plans = {}

        def plan_for(i):
            if i not in plans:
                plans[i] = plan_branch(cwd, branches, text, items[i])
            return plans[i]

        height, _ = stdscr.getmaxyx()
        capacity = max(1, height - 8)
        top = min(max(top, index - capacity + 1), index, max(0, len(items) - capacity))
        if filtering:
            search = [("/", style("cyan")), (text, 0)]
        elif text:
            search = [("/", style("dim")), (text, 0)]
        else:
            search = [("/ to filter or name a new branch", style("dim"))]
        rows = [[], [("New worktree", style("bold"))], search, []]
        for i in range(top, min(top + capacity, len(items))):
            item, selected = items[i], i == index
            row = [("› " if selected else "  ", style("cyan") | style("bold"))]
            if item is None:
                is_new = plan_for(i).get("summary", "").startswith("new branch")
                row += [(("+ " if is_new else "") + typed, (style("bold") if selected else 0) | (style("green") if is_new else 0)),
                        ("  new" if is_new else "", style("dim"))]
            else:
                row += [(item["ref"], style("bold") if selected else 0),
                        ("  remote" if item["kind"] == "remote" else "", style("dim"))]
            rows.append(row)
        if not items:
            rows.append([("No branches. Press / to name a new one.", style("dim"))])
        rows += [[]] * max(0, height - len(rows) - 3)
        if items:
            plan = plan_for(index)
            preview = [("→ ", style("dim")), (plan.get("branch", ""), style("bold"))]
            if plan.get("error"):
                preview.append(("  " + plan["error"], style("red")))
            else:
                preview.append(("  " + plan["summary"], style("dim")))
            rows.append(preview)
        else:
            rows.append([])
        rows.append([(message, style("red"))] if message else [])
        if filtering:
            rows.append(help_row(("enter", "done"), ("esc", "clear")))
        else:
            rows.append(help_row(("j/k", "move"), ("/", "filter"), ("enter", "create"),
                                 ("esc", "clear filter") if text else ("q/esc", "quit")))
        render(stdscr, rows, cursor=(2, 1 + len(text)) if filtering else None)
        key = stdscr.get_wch()
        message = None
        if key == curses.KEY_RESIZE:
            continue
        if filtering:
            if key in ENTER:
                filtering = False
            elif key == ESCAPE:
                filtering, text = False, ""
            elif key in BACKSPACE and not text:
                filtering = False
            elif key in (curses.KEY_DOWN, "\x0e") and items:
                index = min(len(items) - 1, index + 1)
            elif key in (curses.KEY_UP, "\x10"):
                index = max(0, index - 1)
            else:
                text, index = edit_text(text, key), 0
            continue
        new_index, pending = motion(key, index, len(items), capacity, pending)
        if new_index is not None:
            index = new_index
        elif key == "/":
            filtering = True
        elif key == ESCAPE and text:
            text, index = "", 0
        elif key in (ESCAPE, "q"):
            return
        elif (key in ENTER or key == "l") and items:
            plan = plan_for(index)
            if plan.get("error"):
                continue  # The preview line already shows the error.
            try:
                create_worktree(cwd, plan)
            except (RuntimeError, OSError) as error:
                message = str(error)
                continue
            try:
                open_tab(session, {"worktree": str(plan["path"]), "branch": plan["branch"]}, client)
            except (RuntimeError, OSError) as error:
                raise RuntimeError(f"Worktree created at {plan['path']}, but opening its tab failed: {error}") from error
            return


def main(stdscr, mode, cwd, session, client=""):
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    init_colors()
    stdscr.keypad(True)
    render(stdscr, [[], [("Loading worktrees…", style("dim"))]])
    try:
        entries = load_entries(cwd, session)
        if mode == "new":
            new_worktree(stdscr, cwd, session, entries)
        elif mode == "open":
            def on_open(entry):
                if "prunable" in entry or not os.path.isdir(entry["worktree"]):
                    return False, "This worktree's directory is missing."
                open_tab(session, entry, client)
                return True, None
            pick(stdscr, session, "Open worktree", "open", entries, on_open)
        else:
            def on_remove(entry):
                problem = removal_problem(entry)
                if problem:
                    return False, problem
                windows = windows_for_path(session, entry["worktree"])
                lines = [(entry["name"], style("bold")), (tilde(entry["worktree"]), style("dim")), ("", 0),
                         ("The branch will be kept.", 0)]
                if windows:
                    lines += [("", 0), ("These tabs and all their panes will close:", style("yellow"))]
                    lines += [("  " + (w.get("label") or w["window_id"]), 0) for w in windows]
                if not confirm(stdscr, "Remove worktree?", lines):
                    return False, None
                remove_worktree(cwd, session, entry, windows)
                return True, None
            pick(stdscr, session, "Remove worktree", "remove", entries, on_remove)
    except (RuntimeError, OSError, UnicodeError, ValueError) as error:
        render(stdscr, [[], [("Worktree operation failed", style("red") | style("bold"))], [],
                        [(str(error), 0)], [], [("Press any key to close.", style("dim"))]])
        stdscr.get_wch()


def setup_locale():
    for name in ("", "C.UTF-8", "en_US.UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, name)
        except locale.Error:
            continue
        if "utf" in (locale.getpreferredencoding(False) or "").lower():
            return


def restore_focus(session, block):
    """Return focus to the pane the picker was opened from, unless a tab was opened."""
    if not block or STATE["navigated"]:
        return
    try:
        run("rex", "focus", "-s", session, block)
    except (RuntimeError, OSError):
        pass  # The pane may have closed, for example with its worktree's tab.


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5, 6) or sys.argv[1] not in ("open", "new", "remove"):
        sys.exit("This helper is launched by Rex worktree actions.")
    mode, cwd, session = sys.argv[1:4]
    client = sys.argv[4] if len(sys.argv) > 4 else ""
    origin = sys.argv[5] if len(sys.argv) > 5 else ""
    os.environ.setdefault("ESCDELAY", "25")
    setup_locale()
    try:
        curses.wrapper(main, mode, cwd, session, client)
    except KeyboardInterrupt:
        pass
    finally:
        restore_focus(session, origin)
