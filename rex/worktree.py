#!/usr/bin/env python3
"""Interactive Git worktree manager, launched in a Rex floating terminal."""
from concurrent.futures import ThreadPoolExecutor
import curses
import hashlib
import json
import locale
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading

TERMINAL = "com.superlogical.terminal"
COLORS = {}
PENDING = "pending"
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


def git_ok(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


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


def setup_command(cwd):
    """The repository's opt-in setup command for new worktrees: git config rex.worktreeSetup."""
    result = subprocess.run(["git", "-C", cwd, "config", "--get", "rex.worktreeSetup"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return os.fsdecode(result.stdout).strip() if result.returncode == 0 else ""


def open_tab(session, entry, client=None, setup=None):
    path = entry["worktree"]
    matches = windows_for_path(session, path)
    if matches:
        window_id = matches[0]["window_id"]
        run("rex", "focus", "-s", session, window_id)
    elif setup:
        # Type the setup command into the new tab's shell, so its output stays visible there.
        args = {"window_label": tab_label(entry), "layout": {"block": {
            "flavor": TERMINAL + ".shell", "options": {"cwd": path, "initial_input": setup + "\n"}}}}
        created = run("rex", "api", "call", "-s", session, "session.new_window", json.dumps(args))
        window_id = json.loads(created)["window_id"]
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


def missing(entry):
    return "prunable" in entry or not os.path.isdir(entry["worktree"])


def is_dirty(entry):
    if missing(entry):
        return None
    try:
        return bool(git(entry["worktree"], "status", "--porcelain", "--untracked-files=normal"))
    except (RuntimeError, OSError):
        return None


def current_entry(entries, cwd):
    matches = [e for e in entries if inside(cwd, e["worktree"])]
    return max(matches, key=lambda e: len(os.path.realpath(e["worktree"])), default=None)


def base_entries(cwd):
    """List worktrees quickly; status fields start as PENDING."""
    raw = worktrees(cwd)
    if not raw:
        raise RuntimeError("No worktrees found")
    raw[0]["main"] = True
    entries = [e for e in raw if "bare" not in e]
    tracks = tracking(cwd)
    current = current_entry(entries, cwd)
    for entry in entries:
        entry.update(name=branch_name(entry), current=entry is current, dirty=PENDING, open=PENDING,
                     track=tracks.get(entry.get("branch"), ""))
    return entries


def fill_status(entries, session):
    """Fill in the slower fields: uncommitted changes and open tabs."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        for entry, state in zip(entries, pool.map(is_dirty, entries)):
            entry["dirty"] = state
    terminals = terminal_cwds(session)
    for entry in entries:
        entry["open"] = any(inside(path, entry["worktree"]) for _, path in terminals)


def load_entries(cwd, session):
    entries = base_entries(cwd)
    fill_status(entries, session)
    return entries


def load_status_async(entries, session):
    """Fill status on daemon threads, so the list appears at once and exiting never waits."""
    work = queue.Queue()
    for entry in entries:
        work.put(entry)

    def dirty_worker():
        while True:
            try:
                entry = work.get_nowait()
            except queue.Empty:
                return
            entry["dirty"] = is_dirty(entry)

    def open_worker():
        try:
            terminals = terminal_cwds(session)
        except (RuntimeError, ValueError, OSError):
            terminals = []
        for entry in entries:
            entry["open"] = any(inside(path, entry["worktree"]) for _, path in terminals)

    for target in [dirty_worker] * min(6, max(1, len(entries))) + [open_worker]:
        threading.Thread(target=target, daemon=True).start()


def recent_commits(path, count=5):
    if not os.path.isdir(path):
        return []
    try:
        out = git_text(path, "log", f"-n{count}", "--format=%h%x00%s%x00%cr")
    except (RuntimeError, OSError):
        return []
    return [line.split("\0") for line in out.splitlines() if line.count("\0") == 2]


# Matching ---------------------------------------------------------------------

def fuzzy(token, text):
    """Match token as a subsequence of text. Returns (score, positions) or None."""
    lower, positions, start = text.lower(), [], 0
    for char in token:
        found = lower.find(char, start)
        if found < 0:
            return None
        positions.append(found)
        start = found + 1
    score = 0
    for i, pos in enumerate(positions):
        score += 1
        if i and positions[i - 1] == pos - 1:
            score += 3
        if pos == 0 or text[pos - 1] in "/-_. ":
            score += 2
    return score, positions


def match_entry(entry, query):
    """Fuzzy-match every token against the name, or else the path. Returns (score, name hits, path hits)."""
    total, name_hits, path_hits = 0, set(), set()
    for token in query.lower().split():
        named = fuzzy(token, entry["name"])
        if named:
            total += named[0] + 5
            name_hits.update(named[1])
            continue
        # Paths are long and full of common letters, so they match as plain substrings only.
        found = tilde(entry["worktree"]).lower().find(token)
        if found < 0:
            return None
        total += len(token)
        path_hits.update(range(found, found + len(token)))
    return total, name_hits, path_hits


def filter_entries(entries, query):
    if not query.strip():
        return list(entries)
    scored = [(match_entry(e, query), i, e) for i, e in enumerate(entries)]
    return [e for m, i, e in sorted((s for s in scored if s[0]), key=lambda s: (-s[0][0], s[1]))]


# Removal ----------------------------------------------------------------------

def removal_problem(entry):
    """Reasons a worktree can't be removed at all, even with force."""
    if entry.get("main"):
        return "The main worktree can't be removed."
    if "locked" in entry:
        return "This worktree is locked."
    if missing(entry):
        return "This worktree's directory is missing; press p to prune it."
    return None


def changed_files(entry):
    out = git(entry["worktree"], "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")
    return [line for line in os.fsdecode(out).splitlines() if line]


def default_branch(cwd):
    """The repository's default branch, preferring the remote's: origin/main, main, and so on."""
    result = subprocess.run(["git", "-C", cwd, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode == 0 and result.stdout.strip():
        return os.fsdecode(result.stdout).strip()
    for ref in ("origin/main", "origin/master", "main", "master"):
        if git_ok(cwd, "rev-parse", "--verify", "--quiet", ref + "^{commit}"):
            return ref
    return None


def merged_into(cwd, entry):
    """Return the default branch a worktree's branch is fully merged into, or None."""
    if "branch" not in entry:
        return None
    target = default_branch(cwd)
    if not target or target in (entry["name"], "origin/" + entry["name"]):
        return None
    return target if git_ok(cwd, "merge-base", "--is-ancestor", entry["branch"], target) else None


def remove_worktree(repo, session, entry, windows, force=False, delete_branch=False):
    """Remove a worktree from the main worktree's repository, then close its tabs."""
    git(repo, "worktree", "remove", *(["--force"] if force else []), "--", entry["worktree"])
    problems = []
    if delete_branch:
        try:
            # Callers only offer this after merged_into() confirmed the branch is merged.
            git(repo, "branch", "-D", "--", entry["name"])
        except RuntimeError as error:
            problems.append(f"branch not deleted: {error}")
    try:
        close_worktree_tabs(session, windows)
    except (RuntimeError, OSError) as error:
        problems.append(f"a tab could not be closed: {error}")
    if problems:
        raise RuntimeError("Worktree removed, but " + "; ".join(problems))


def prune_worktrees(repo):
    git(repo, "worktree", "prune")


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
    """Collect local branches and remote-only branches, newest first, plus base choices."""
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
    bases = [{"ref": "HEAD", "label": head}]
    default = default_branch(cwd)
    if default and default != head:
        bases.append({"ref": default, "label": default})
    suggestions = [{"name": b, "ref": b, "kind": "local"} for b in local if b not in checked_out]
    for name, refs in remote.items():
        if name not in local and name not in checked_out:
            suggestions += [{"name": name, "ref": ref, "kind": "remote"} for ref in refs]
    return {"local": set(local), "remote": remote, "checked_out": checked_out,
            "suggestions": suggestions, "head": head, "bases": bases}


def plan_branch(cwd, branches, text, suggestion=None, base=None):
    """Describe what creating a worktree for the input would do."""
    base = base or {"ref": "HEAD", "label": branches["head"]}
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
        if not git_ok(cwd, "check-ref-format", "--branch", name):
            return {"branch": name, "error": "Not a valid branch name."}
        if name in branches["checked_out"]:
            return {"branch": name, "error": "Already checked out at " + tilde(branches["checked_out"][name])}
        refs = branches["remote"].get(name, [])
        tracked = refs[0] if len(refs) == 1 else next((r for r in refs if r.startswith("origin/")), None)
        if name in branches["local"]:
            plan = {"branch": name, "summary": "existing branch", "args": [name]}
        elif tracked:
            plan = {"branch": name, "summary": "track " + tracked, "args": ["--track", "-b", name, None, tracked]}
        else:
            # --no-track keeps a branch started from origin/main from pushing to or tracking main.
            plan = {"branch": name, "summary": "new branch from " + base["label"],
                    "args": ["--no-track", "-b", name, None, base["ref"]]}
    path = new_path(cwd, plan["branch"])
    if path.exists():
        plan["error"] = "Destination already exists: " + tilde(str(path))
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


def highlighted(text, hits, attr):
    """Split text into segments, emphasizing the characters at the hit positions."""
    segments, start = [], 0
    for i in range(1, len(text) + 1):
        if i == len(text) or (i in hits) != (start in hits):
            hit = start in hits
            segments.append((text[start:i], (style("yellow") | style("bold")) if hit else attr))
            start = i
    return segments


def badges(entry):
    result = []
    if entry.get("main"):
        result.append(("primary", style("blue")))
    if entry.get("open") is True:
        result.append(("open", style("green")))
    if entry.get("dirty") is True:
        result.append(("modified", style("yellow")))
    if entry.get("track"):
        result.append((entry["track"], style("red") if "gone" in entry["track"] else style("cyan")))
    if "locked" in entry:
        result.append(("locked", style("red")))
    if missing(entry):
        result.append(("missing", style("red")))
    if PENDING in (entry.get("dirty"), entry.get("open")):
        result.append(("…", style("dim")))
    return result


def entry_row(entry, selected, name_width, badge_width, match=None):
    _, name_hits, path_hits = match or (0, set(), set())
    name = entry["name"][:name_width]
    row = [("› " if selected else "  ", style("cyan") | style("bold")),
           ("● " if entry.get("current") else "  ", style("green"))]
    row += highlighted(name, name_hits, style("bold") if selected else 0)
    row.append((" " * (name_width - len(name) + 2), 0))
    used = 0
    for text, attr in badges(entry):
        row += [(text, attr), (" ", 0)]
        used += len(text) + 1
    row.append((" " * max(0, badge_width - used) + " ", 0))
    row += highlighted(tilde(entry["worktree"]), path_hits, style("dim"))
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


def read_key(stdscr):
    """Return the next key, or None when the input timeout passes first."""
    try:
        return stdscr.get_wch()
    except curses.error:
        return None


def search_row(text, filtering, placeholder):
    if filtering:
        return [("/", style("cyan")), (text, 0)]
    if text:
        return [("/", style("dim")), (text, 0)]
    return [(placeholder, style("dim"))]


# Screens ----------------------------------------------------------------------

NAVIGATION_HELP = [
    ("j / k", "move down / up"),
    ("gg / G", "first / last"),
    ("ctrl+d / ctrl+u", "half page down / up"),
    ("ctrl+f / ctrl+b", "page down / up"),
]


def show_help(stdscr, title, sections):
    rows = [[], [(title + " — keys", style("bold"))]]
    for heading, items in sections:
        rows += [[], [(heading, style("dim"))]]
        width = max(len(k) for k, _ in items)
        rows += [[(k.ljust(width), style("bold")), ("   " + label, 0)] for k, label in items]
    rows += [[], [("Press any key to go back.", style("dim"))]]
    stdscr.timeout(-1)
    render(stdscr, rows)
    read_key(stdscr)


def confirm(stdscr, title, lines, keys, help_items=None):
    """Show lines and wait for one of keys (a dict of key -> result). Esc cancels."""
    stdscr.timeout(-1)
    while True:
        rows = [[], [(title, style("bold"))], []]
        rows += [line if isinstance(line, list) else [line] for line in lines()]
        rows += [[], help_row(*(help_items or []), ("esc", "cancel"))]
        render(stdscr, rows)
        key = read_key(stdscr)
        if key == ESCAPE:
            return None
        if key in ENTER:
            key = "\n"
        if key in keys:
            result = keys[key]()
            if result is not None:
                return result


class Manager:
    def __init__(self, stdscr, cwd, session, client):
        self.stdscr, self.session, self.client = stdscr, session, client
        self.origin_cwd = cwd
        self.repo = worktrees(cwd)[0]["worktree"]
        self.message, self.message_style = None, "red"
        self.commits = {}
        self.reload()

    @property
    def cwd(self):
        # The original directory disappears if its worktree is removed.
        return self.origin_cwd if os.path.isdir(self.origin_cwd) else self.repo

    def reload(self):
        self.entries = base_entries(self.cwd)
        self.commits = {}
        load_status_async(self.entries, self.session)

    def say(self, text, tone="red"):
        self.message, self.message_style = text, tone

    def list_screen(self):
        stdscr = self.stdscr
        query, filtering, pending = "", False, None
        selected = next((e for e in self.entries if e.get("current")), None)
        top = 0
        while True:
            stdscr.timeout(150 if any(PENDING in (e["dirty"], e["open"]) for e in self.entries) else -1)
            shown = filter_entries(self.entries, query)
            if selected not in shown:
                selected = shown[0] if shown else None
            height, _ = stdscr.getmaxyx()
            preview_rows = 7 if height >= 22 else 0
            capacity = max(1, height - 7 - preview_rows)
            index = shown.index(selected) if selected in shown else 0
            top = min(max(top, index - capacity + 1), index, max(0, len(shown) - capacity))
            name_width = min(40, max([len(e["name"]) for e in self.entries] + [8]))
            badge_width = max([sum(len(t) + 1 for t, _ in badges(e)) for e in self.entries] + [0])

            rows = [[], [("Worktrees", style("bold")), ("  " + repo_name(self.repo), style("dim"))],
                    search_row(query, filtering, "/ to filter"), []]
            for e in shown[top:top + capacity]:
                rows.append(entry_row(e, e is selected, name_width, badge_width,
                                      match_entry(e, query) if query.strip() else None))
            if not shown:
                rows.append([("No matching worktrees", style("dim"))])
            rows += [[]] * max(0, height - len(rows) - 3 - preview_rows)
            if preview_rows:
                rows += self.preview(selected)
            if self.message:
                rows.append([(self.message, style(self.message_style))])
            else:
                rows.append([(f"{len(shown)}/{len(self.entries)}", style("dim"))])
            if filtering:
                rows.append(help_row(("enter", "done"), ("esc", "clear")))
            else:
                rows.append(help_row(("enter", "open"), ("n", "new"), ("dd", "remove"), ("/", "filter"),
                                     ("?", "help"), ("esc", "clear filter") if query else ("q", "quit")))
            render(stdscr, rows, cursor=(2, 1 + len(query)) if filtering else None)

            key = read_key(stdscr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            self.message = None
            if filtering:
                if key in ENTER:
                    filtering = False
                elif key == ESCAPE:
                    filtering, query = False, ""
                elif key in BACKSPACE and not query:
                    filtering = False
                elif key in (curses.KEY_DOWN, "\x0e", curses.KEY_UP, "\x10"):
                    new_index, _ = motion(key, index, len(shown), capacity, None)
                    if new_index is not None:
                        selected = shown[new_index]
                else:
                    query = edit_text(query, key)
                continue
            if key == "d":
                if pending == "d":
                    pending = None
                    if selected and self.remove(selected):
                        return
                    selected, query = None, ""
                else:
                    pending = "d"
                continue
            new_index, pending = motion(key, index, len(shown), capacity, pending)
            if new_index is not None:
                selected = shown[new_index]
            elif key == "/":
                filtering = True
            elif key == "?":
                show_help(stdscr, "Worktrees", [("Move", NAVIGATION_HELP), ("Actions", [
                    ("enter / l", "open the worktree's tab"),
                    ("n", "new worktree"),
                    ("dd", "remove the worktree"),
                    ("p", "prune missing worktrees"),
                    ("/", "filter (fuzzy)"),
                    ("esc", "clear the filter, or quit"),
                    ("q", "quit"),
                ])])
            elif key == ESCAPE and query:
                query = ""
            elif key in (ESCAPE, "q"):
                return
            elif key == "n":
                if self.new_screen():
                    return
                stdscr.timeout(-1)
            elif key == "p":
                self.prune()
                selected, query = None, ""
            elif key in ENTER or key == "l":
                if selected is None:
                    continue
                if missing(selected):
                    self.say("This worktree's directory is missing; press p to prune it.")
                    continue
                try:
                    open_tab(self.session, selected, self.client)
                    return
                except (RuntimeError, OSError) as error:
                    self.say(str(error))

    def preview(self, entry):
        rows = [[("─" * 3 + " Recent commits", style("dim"))]]
        if entry is None:
            return rows + [[]] * 6
        path = entry["worktree"]
        if path not in self.commits:
            self.commits[path] = recent_commits(path)
        commits = self.commits[path]
        if missing(entry):
            rows.append([("Directory missing", style("red"))])
        elif not commits:
            rows.append([("No commits", style("dim"))])
        for sha, subject, when in commits[:5]:
            rows.append([(sha, style("yellow")), ("  " + subject, 0), ("  " + when, style("dim"))])
        return rows + [[]] * (7 - len(rows))

    def remove(self, entry):
        """Confirm and remove a worktree. Returns True if the picker should exit."""
        problem = removal_problem(entry)
        if problem:
            self.say(problem)
            return False
        try:
            files = changed_files(entry)
            windows = windows_for_path(self.session, entry["worktree"])
            merged = merged_into(self.repo, entry)
        except (RuntimeError, OSError) as error:
            self.say(str(error))
            return False
        choice = {"delete_branch": False}

        def lines():
            out = [[(entry["name"], style("bold"))], [(tilde(entry["worktree"]), style("dim"))], []]
            if files:
                out.append([(f"Uncommitted changes ({len(files)}):", style("yellow"))])
                out += [[("  " + f, 0)] for f in files[:8]]
                if len(files) > 8:
                    out.append([(f"  …and {len(files) - 8} more", style("dim"))])
                out += [[("Force removing discards these changes.", style("red"))], []]
            if windows:
                out.append([("These tabs and all their panes will close:", style("yellow"))])
                out += [[("  " + (w.get("label") or w["window_id"]), 0)] for w in windows]
                out.append([])
            if merged:
                box = "[x]" if choice["delete_branch"] else "[ ]"
                out.append([(box + " ", style("cyan")), ("Also delete branch ", 0), (entry["name"], style("bold")),
                            ("  (no unmerged commits)", style("dim"))])
            elif "branch" in entry:
                out.append([("The branch ", 0), (entry["name"], style("bold")), (" will be kept.", 0)])
            return out

        def toggle():
            choice["delete_branch"] = not choice["delete_branch"]

        keys, help_items = {}, []
        if files:
            keys["F"] = lambda: "force"
            help_items.append(("F", "force remove"))
        else:
            keys["\n"] = lambda: "remove"
            help_items.append(("enter", "remove"))
        if merged:
            keys["b"] = toggle
            help_items.append(("b", "toggle branch deletion"))
        result = confirm(self.stdscr, "Remove worktree?", lines, keys, help_items)
        if not result:
            return False
        try:
            remove_worktree(self.repo, self.session, entry, windows, force=result == "force",
                            delete_branch=choice["delete_branch"])
        except (RuntimeError, OSError) as error:
            self.say(str(error))
        else:
            self.say(f"Removed {entry['name']}" + (" and its branch" if choice["delete_branch"] else ""), "green")
        self.reload()
        return False

    def prune(self):
        stale = [e for e in self.entries if "prunable" in e]
        if not stale:
            self.say("No missing worktrees to prune.", "dim")
            return

        def lines():
            out = [[("These worktrees' directories no longer exist:", 0)]]
            out += [[("  " + e["name"], style("bold")), ("  " + tilde(e["worktree"]), style("dim"))] for e in stale]
            return out + [[], [("Only Git's records are removed; branches are kept.", style("dim"))]]

        if confirm(self.stdscr, f"Prune {len(stale)} missing worktree{'s' if len(stale) != 1 else ''}?",
                   lines, {"\n": lambda: True}, [("enter", "prune")]):
            try:
                prune_worktrees(self.repo)
                self.say(f"Pruned {len(stale)} missing worktree{'s' if len(stale) != 1 else ''}.", "green")
            except (RuntimeError, OSError) as error:
                self.say(str(error))
            self.reload()

    def new_screen(self):
        """Create a worktree. Returns True once one is created and opened."""
        stdscr = self.stdscr
        stdscr.timeout(-1)
        cwd = self.cwd
        branches = load_branches(cwd, self.entries)
        suggestions, bases = branches["suggestions"], branches["bases"]
        text, index, top, message, filtering, pending, base = "", 0, 0, None, False, None, 0
        while True:
            items = [s for s in suggestions if fuzzy_all(text, s["ref"])]
            if text.strip():
                items.sort(key=lambda s: -fuzzy_all(text, s["ref"]))
            typed = normalize_branch(text)
            if typed and typed not in {s["name"] for s in items}:
                items.insert(0, None)  # None is the new branch named by the filter text.
            index = min(index, max(0, len(items) - 1))
            plans = {}

            def plan_for(i):
                if i not in plans:
                    plans[i] = plan_branch(cwd, branches, text, items[i], bases[base])
                return plans[i]

            height, _ = stdscr.getmaxyx()
            capacity = max(1, height - 9)
            top = min(max(top, index - capacity + 1), index, max(0, len(items) - capacity))
            base_row = [("Base for new branches: ", style("dim"))]
            for i, option in enumerate(bases):
                base_row.append((option["label"], (style("cyan") | style("bold")) if i == base else style("dim")))
                base_row.append(("  ", 0))
            rows = [[], [("New worktree", style("bold")), ("  " + repo_name(self.repo), style("dim"))],
                    search_row(text, filtering, "/ to filter or name a new branch"), base_row, []]
            for i in range(top, min(top + capacity, len(items))):
                item, selected = items[i], i == index
                row = [("› " if selected else "  ", style("cyan") | style("bold"))]
                if item is None:
                    is_new = plan_for(i).get("summary", "").startswith("new branch")
                    attr = (style("bold") if selected else 0) | (style("green") if is_new else 0)
                    row += [(("+ " if is_new else "") + typed, attr), ("  new" if is_new else "", style("dim"))]
                else:
                    hits = set()
                    for token in text.lower().split():
                        hits.update((fuzzy(token, item["ref"]) or (0, []))[1])
                    row += highlighted(item["ref"], hits, style("bold") if selected else 0)
                    row.append(("  remote" if item["kind"] == "remote" else "", style("dim")))
                rows.append(row)
            if not items:
                rows.append([("No branches. Press / to name a new one.", style("dim"))])
            rows += [[]] * max(0, height - len(rows) - 3)
            if items:
                plan = plan_for(index)
                preview = [("→ ", style("dim")), (plan.get("branch", ""), style("bold"))]
                preview.append(("  " + plan["error"], style("red")) if plan.get("error")
                               else ("  " + plan["summary"], style("dim")))
                rows.append(preview)
            else:
                rows.append([])
            rows.append([(message, style("red"))] if message else [])
            if filtering:
                rows.append(help_row(("enter", "done"), ("esc", "clear")))
            else:
                rows.append(help_row(("enter", "create"), ("/", "filter or name"),
                                     *([("b", "base")] if len(bases) > 1 else []), ("?", "help"),
                                     ("esc", "clear filter") if text else ("q/esc", "back")))
            render(stdscr, rows, cursor=(2, 1 + len(text)) if filtering else None)
            key = read_key(stdscr)
            message = None
            if key is None or key == curses.KEY_RESIZE:
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
            elif key == "b" and len(bases) > 1:
                base = (base + 1) % len(bases)
            elif key == "?":
                show_help(stdscr, "New worktree", [("Move", NAVIGATION_HELP), ("Actions", [
                    ("enter / l", "create a worktree for the selection"),
                    ("/", "filter branches, or name a new one"),
                    ("b", "switch the base for new branches"),
                    ("esc", "clear the filter, or go back"),
                    ("q", "go back"),
                ])])
            elif key == ESCAPE and text:
                text, index = "", 0
            elif key in (ESCAPE, "q"):
                return False
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
                    open_tab(self.session, {"worktree": str(plan["path"]), "branch": plan["branch"]},
                             self.client, setup_command(cwd))
                except (RuntimeError, OSError) as error:
                    raise RuntimeError(f"Worktree created at {plan['path']}, but opening its tab failed: {error}") from error
                return True


def fuzzy_all(query, text):
    """Score text against every token in query; 0 means no match. An empty query matches everything."""
    total = 1
    for token in query.lower().split():
        match = fuzzy(token, text)
        if not match:
            return 0
        total += match[0]
    return total


def main(stdscr, mode, cwd, session, client=""):
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    init_colors()
    stdscr.keypad(True)
    render(stdscr, [[], [("Loading worktrees…", style("dim"))]])
    try:
        manager = Manager(stdscr, cwd, session, client)
        if mode == "new":
            manager.new_screen()
        else:
            manager.list_screen()
    except (RuntimeError, OSError, UnicodeError, ValueError) as error:
        stdscr.timeout(-1)
        render(stdscr, [[], [("Worktree operation failed", style("red") | style("bold"))], [],
                        [(str(error), 0)], [], [("Press any key to close.", style("dim"))]])
        read_key(stdscr)


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
    if len(sys.argv) not in (4, 5, 6) or sys.argv[1] not in ("list", "new"):
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
