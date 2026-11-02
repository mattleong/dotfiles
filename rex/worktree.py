#!/usr/bin/env python3
"""Interactive Git worktree manager, launched in a Rex floating terminal."""
from concurrent.futures import ThreadPoolExecutor
import curses
import functools
import hashlib
import json
import locale
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

TERMINAL = "com.superlogical.terminal"
COLORS = {}
SELECTED_PAIRS = {}  # Color pair number -> the same colors on the selected-row background.
# Nerd Font icons (Font Awesome range, stable across Nerd Font versions).
ICONS = {
    "main": "\uf015", "tab": "\uf120", "dirty": "\uf040", "ahead": "\uf062", "behind": "\uf063",
    "deleted": "\uf127", "missing": "\uf114", "locked": "\uf023", "remote": "\uf0c2",
    "local": "\ue725", "ok": "\uf00c", "error": "\uf00d", "info": "\uf05a", "folder": "\uf07b",
    "pr": "\uf407",
}
# Where tools keep the worktrees they create, to tag each worktree with its source.
WORKTREE_SOURCES = [(".herdr/worktrees", "herdr"), (".t3/worktrees", "t3"),
                    (".local/share/rex/worktrees", "rex")]
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


@functools.lru_cache(maxsize=None)
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


def open_tab(session, entry, client=None, setup=None, focus=True):
    """Focus the worktree's tab, or create one. With focus=False, only create it, in the background.
    Returns the window ID, or None when focus=False and a tab is already open."""
    path = entry["worktree"]
    matches = windows_for_path(session, path)
    if matches and not focus:
        return None
    if not focus:
        created = run("rex", "window", "new", "-s", session, tab_label(entry), "-c", path, "--focus=false", "--json")
        return json.loads(created)["window_id"]
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


def open_split(session, entry, block):
    """Open the worktree in a new pane beside block, and focus it."""
    if not block:
        raise RuntimeError("Can't tell which pane to split.")
    run("rex", "split", "-s", session, "-b", block, "--split=right", "-c", entry["worktree"],
        "--label", branch_name(entry))
    STATE["navigated"] = True


# Worktree state ---------------------------------------------------------------

REMOTE_DELETED = "remote deleted"


def tracking(cwd):
    out = git_text(cwd, "for-each-ref", "--format=%(refname)%00%(upstream:track)", "refs/heads")
    result = {}
    for line in out.splitlines():
        ref, _, track = line.partition("\0")
        ahead = re.search(r"ahead (\d+)", track)
        behind = re.search(r"behind (\d+)", track)
        parts = ([ahead.group(1) + " ahead"] if ahead else []) + ([behind.group(1) + " behind"] if behind else [])
        if "gone" in track:
            parts.append(REMOTE_DELETED)
        result[ref] = parts
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
    times = commit_times(cwd, [e["HEAD"] for e in entries if e.get("HEAD")])
    current = current_entry(entries, cwd)
    for entry in entries:
        entry.update(name=branch_name(entry), current=entry is current, dirty=PENDING, open=PENDING,
                     track=tracks.get(entry.get("branch"), []), time=times.get(entry.get("HEAD")))
    return entries


def commit_times(cwd, shas):
    """Commit times for a list of commits, in one Git call."""
    if not shas:
        return {}
    try:
        out = git_text(cwd, "show", "-s", "--format=%H %ct", *sorted(set(shas)))
    except RuntimeError:
        return {}
    return {sha: int(stamp) for sha, _, stamp in (line.partition(" ") for line in out.splitlines()) if stamp}


def short_age(stamp, now=None):
    """A compact age such as 5m, 3h, 2d, 3w, 4mo, or 1y."""
    if not stamp:
        return ""
    seconds = max(0, (time.time() if now is None else now) - stamp)
    for unit, size in (("y", 365 * 86400), ("mo", 30 * 86400), ("w", 7 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return "now"


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


def recent_commits(path, count=5, base=None):
    """Commits for the preview: those not in base when there are any, else the latest ones.
    Returns (description, [(sha, subject, author, unix time)])."""
    if not os.path.isdir(path):
        return "", []
    since = 0
    if base:
        try:
            since = int(git_text(path, "rev-list", "--count", f"{base}..HEAD") or 0)
        except (RuntimeError, ValueError):
            since = 0
    try:
        out = git_text(path, "log", f"-n{count}", "--format=%h%x00%s%x00%an%x00%ct",
                       *([f"{base}..HEAD"] if since else []))
    except (RuntimeError, OSError):
        return "", []
    commits = []
    for line in out.splitlines():
        parts = line.split("\0")
        if len(parts) == 4:
            commits.append((parts[0], parts[1], parts[2], int(parts[3] or 0)))
    if since:
        return f"{since} commit{'s' if since != 1 else ''} not in {base}", commits
    return "Recent commits", commits


def pull_requests_enabled(repo):
    if not shutil.which("gh"):
        return False
    result = subprocess.run(["git", "-C", repo, "config", "--get", "rex.worktreePullRequests"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return os.fsdecode(result.stdout).strip().lower() not in ("false", "no", "off", "0")


def pull_requests(repo):
    """Map branch names to their newest pull request, from your PRs and the repository's recent ones."""
    fields = "number,state,headRefName,isDraft"
    found = {}
    for extra in (["--author", "@me"], []):
        try:
            out = run("gh", "pr", "list", "--state", "all", "--limit", "200", "--json", fields, *extra, cwd=repo)
            prs = json.loads(out)
        except (RuntimeError, OSError, ValueError):
            continue
        for pr in prs:
            branch = pr.get("headRefName")
            if branch and (branch not in found or pr["number"] > found[branch]["number"]):
                found[branch] = pr
    return found


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
    """Fuzzy-match every token against the name, or else the path. Returns (score, name hits, folder hits)."""
    total, name_hits, path_hits = 0, set(), set()
    for token in query.lower().split():
        named = fuzzy(token, entry["name"])
        if named:
            total += named[0] + 5
            name_hits.update(named[1])
            continue
        # Paths are long and full of common letters, so they match as plain substrings only.
        if token not in tilde(entry["worktree"]).lower():
            return None
        total += len(token)
        found = os.path.basename(entry["worktree"]).lower().find(token)
        if found >= 0:
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
    """The remote's default branch, such as origin/main or upstream/master.

    Uses each remote's recorded HEAD, trying origin first, then common names on each remote, then
    a local main or master when there's no remote."""
    remotes = git_text(cwd, "remote").splitlines()
    remotes.sort(key=lambda name: name != "origin")
    for remote in remotes:
        result = subprocess.run(["git", "-C", cwd, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if result.returncode == 0 and result.stdout.strip():
            return os.fsdecode(result.stdout).strip()
    for ref in [f"{r}/{name}" for r in remotes for name in ("main", "master")] + ["main", "master"]:
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


@functools.lru_cache(maxsize=None)
def worktree_root_key(cwd):
    common = git_text(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    # Hash the common Git directory to distinguish repositories with the same name.
    return repo_name(cwd) + "-" + hashlib.sha256(os.fsencode(os.path.realpath(common))).hexdigest()[:10]


def new_path(cwd, branch):
    repo_key = worktree_root_key(cwd)
    slug = branch.replace("/", "-")[:100]
    suffix = hashlib.sha256(branch.encode()).hexdigest()[:8]
    return Path.home() / ".local/share/rex/worktrees" / repo_key / (slug + "-" + suffix)


RECENT_DAYS = 30


def load_branches(cwd, entries, now=None):
    """Collect branches without worktrees: recent local ones, then remote-only ones, then older local ones."""
    checked_out = {e["branch"].removeprefix("refs/heads/"): e["worktree"] for e in entries if "branch" in e}

    def refs(namespace):
        out = git_text(cwd, "for-each-ref", "--sort=-committerdate",
                       "--format=%(refname:short)%00%(committerdate:unix)", namespace)
        return [(ref, int(stamp or 0)) for ref, _, stamp in (line.partition("\0") for line in out.splitlines())]

    local = refs("refs/heads")
    local_names = {name for name, _ in local}
    remotes = sorted(git_text(cwd, "remote").splitlines(), key=len, reverse=True)
    remote = {}
    remote_refs = []
    for ref, _ in refs("refs/remotes"):
        for name in remotes:
            if ref.startswith(name + "/") and ref != name + "/HEAD":
                remote.setdefault(ref[len(name) + 1:], []).append(ref)
                remote_refs.append((ref[len(name) + 1:], ref))
                break
    head = git_text(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if head == "HEAD":
        head = git_text(cwd, "rev-parse", "--short", "HEAD")
    # The default branch comes first, so new branches start from it unless you choose otherwise.
    bases = [{"ref": "HEAD", "label": head}]
    default = default_branch(cwd)
    if default and default != head:
        bases.insert(0, {"ref": default, "label": default})
    cutoff = (time.time() if now is None else now) - RECENT_DAYS * 86400
    available = [(name, stamp) for name, stamp in local if name not in checked_out]
    suggestions = [{"name": n, "ref": n, "kind": "local"} for n, stamp in available if stamp >= cutoff]
    suggestions += [{"name": name, "ref": ref, "kind": "remote"} for name, ref in remote_refs
                    if name not in local_names and name not in checked_out]
    suggestions += [{"name": n, "ref": n, "kind": "local"} for n, stamp in available if stamp < cutoff]
    return {"local": local_names, "remote": remote, "checked_out": checked_out,
            "suggestions": suggestions, "head": head, "bases": bases}


def plan_branch(cwd, branches, text, suggestion=None, base=None):
    """Describe what creating a worktree would do: for an existing branch, or a new one named by text."""
    base = base or {"ref": "HEAD", "label": branches["head"]}
    if suggestion:
        name, ref = suggestion["name"], suggestion["ref"]
        if suggestion["kind"] == "local":
            plan = {"branch": name, "action": f"check out existing branch {name}", "args": [name]}
        else:
            plan = {"branch": name, "action": f"check out {ref} as local branch {name}, tracking it",
                    "args": ["--track", "-b", name, None, ref]}
    else:
        name = normalize_branch(text)
        if not name:
            return {"error": "Enter a branch name." if not text.strip() else "No usable branch-name characters."}
        if not git_ok(cwd, "check-ref-format", "--branch", name):
            return {"branch": name, "error": "Not a valid branch name."}
        if name in branches["checked_out"]:
            return {"branch": name, "error": "Already checked out at " + tilde(branches["checked_out"][name])}
        if name in branches["local"]:
            return {"branch": name, "error": "A branch with this name exists; select it under Existing branches."}
        if branches["remote"].get(name):
            return {"branch": name, "error": f"{branches['remote'][name][0]} exists; select it under Existing branches."}
        # --no-track keeps a branch started from origin/main from pushing to or tracking main.
        plan = {"branch": name, "action": f"create new branch {name} from {base['label']}",
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


def render(stdscr, rows, cursor=None):
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
        for text, attr in row:
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


def pr_cell(entry):
    pr = entry.get("pr")
    if not pr:
        return []
    state = "draft" if pr.get("isDraft") and pr["state"] == "OPEN" else pr["state"].lower()
    color = {"open": "green", "merged": "magenta", "closed": "red"}.get(state, "dim")
    return [(f"{ICONS['pr']} #{pr['number']} {state}", style(color))]


def sync_cell(entry):
    cell = []
    for part in entry.get("track") or []:
        if part == REMOTE_DELETED:
            cell.append((ICONS["deleted"] + " " + part, style("red")))
        else:
            cell.append((ICONS["ahead" if part.endswith("ahead") else "behind"] + " " + part, style("cyan")))
    return cell


def state_cell(entry):
    cell = []
    if entry.get("main"):
        cell.append((ICONS["main"] + " main checkout", style("blue")))
    if entry.get("open") is True:
        cell.append((ICONS["tab"] + " tab open", style("green")))
    if entry.get("dirty") is True:
        cell.append((ICONS["dirty"] + " uncommitted", style("yellow")))
    if "locked" in entry:
        cell.append((ICONS["locked"] + " locked", style("red")))
    if missing(entry):
        cell.append((ICONS["missing"] + " folder missing", style("red")))
    if PENDING in (entry.get("dirty"), entry.get("open")):
        cell.append(("…", style("dim")))
    return cell


def badges(entry):
    """Every status label for a worktree, in column order."""
    return pr_cell(entry) + sync_cell(entry) + state_cell(entry)


def cell_width(cell):
    return sum(len(text) for text, _ in cell) + 3 * max(0, len(cell) - 1)


def badges_width(entry):
    return cell_width(badges(entry))


CELLS = [("PR", pr_cell), ("SYNC", sync_cell), ("STATE", state_cell)]


def column_widths(entries):
    """Widths for the list's columns, sized to every worktree so they don't shift while filtering.
    A column that's empty for every worktree gets width 0 and is hidden."""
    widths = {"BRANCH": min(40, max([len(e["name"]) for e in entries] + [6]))}
    for title, cell in CELLS:
        width = max([cell_width(cell(e)) for e in entries] + [0])
        widths[title] = max(width, len(title)) if width else 0
    widths["SOURCE"] = max([len(worktree_source(e)) for e in entries] + [0])
    return widths


def header_row(widths):
    dim = style("dim") | style("bold")
    row = [("    " + "BRANCH".ljust(widths["BRANCH"] + 2), dim), ("AGE  ", dim)]
    for title, _ in CELLS:
        if widths[title]:
            row.append((title.ljust(widths[title] + 3), dim))
    row.append(("FROM", dim))
    return row


def worktree_source(entry):
    """The tool that created a worktree, judged by where it lives: herdr, t3, claude, rex, or ''."""
    path = tilde(entry["worktree"])
    for folder, name in WORKTREE_SOURCES:
        if path.startswith("~/" + folder + "/"):
            return name
    return "claude" if "/.claude/worktrees/" in path else ""


def entry_row(entry, selected, widths, match=None):
    _, name_hits, folder_hits = match or (0, set(), set())
    width = widths["BRANCH"]
    name = entry["name"] if len(entry["name"]) <= width else entry["name"][:width - 1] + "…"
    row = [("› " if selected else "  ", style("cyan") | style("bold")),
           ("● " if entry.get("current") else "  ", style("green"))]
    row += highlighted(name, {h for h in name_hits if h < len(name)}, style("bold") if selected else 0)
    row.append((" " * (width - len(name) + 2), 0))
    row.append((short_age(entry.get("time")).rjust(3) + "  ", style("dim")))
    for title, cell_for in CELLS:
        if not widths[title]:
            continue
        cell = cell_for(entry)
        for i, (text, attr) in enumerate(cell):
            row += ([(" · ", style("dim"))] if i else []) + [(text, attr)]
        row.append((" " * (widths[title] - cell_width(cell) + 3), 0))
    if widths["SOURCE"]:
        row.append((worktree_source(entry).ljust(widths["SOURCE"]) + "  ", style("blue")))
    row += highlighted(os.path.basename(entry["worktree"]), folder_hits, style("dim"))
    return Selected(row) if selected else row


ENTER = ("\n", "\r", curses.KEY_ENTER)
BACKSPACE = ("\x7f", "\b", curses.KEY_BACKSPACE)
ESCAPE = "\x1b"


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


def help_row(*items):
    row = []
    for key, label in items:
        row += [(key, style("bold")), (" " + label + "   ", style("dim"))]
    return row


class FocusLost(Exception):
    """The picker's pane lost focus to another pane."""


def set_timeout(stdscr, ms):
    STATE["timeout"] = ms
    stdscr.timeout(ms)


def picker_focused(session):
    """Whether this picker's pane is still the session's focused pane."""
    picker = os.environ.get("REX_BLOCK")
    if not picker:
        return True
    view = json.loads(run("rex", "session", "inspect", session, "--json"))
    for window in view.get("windows", []):
        if any(b.get("block_id") == picker for layer in window.get("layers", []) for b in layer.get("blocks", [])):
            return window["window_id"] == view.get("active_window_id") and window.get("focused_block_id") == picker
    return False


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
        stdscr.timeout(STATE.get("timeout", -1))
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
            if follow[1] == "O" and STATE.get("session"):
                try:
                    focused = picker_focused(STATE["session"])
                except (RuntimeError, ValueError, OSError):
                    focused = True
                if not focused:
                    raise FocusLost()
            return None
        for char in reversed(follow):
            unget(char)
        return ESCAPE
    finally:
        stdscr.timeout(STATE.get("timeout", -1))


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
    set_timeout(stdscr, -1)
    render(stdscr, rows)
    read_key(stdscr)


def confirm(stdscr, title, lines, keys, help_items=None):
    """Show lines and wait for one of keys (a dict of key -> result). Esc cancels."""
    set_timeout(stdscr, -1)
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


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Manager:
    def __init__(self, stdscr, cwd, session, client, origin=""):
        self.stdscr, self.session, self.client, self.origin = stdscr, session, client, origin
        self.origin_cwd = cwd
        self.repo = worktrees(cwd)[0]["worktree"]
        self.default = default_branch(self.repo)
        self.message, self.message_style = None, "red"
        self.commits = {}
        self.prs, self.prs_loading = {}, False
        self.show_prs = pull_requests_enabled(self.repo)
        self.reload()
        if self.show_prs:
            self.load_prs()

    def load_prs(self):
        """Fetch pull requests on a daemon thread; badges appear when they arrive."""
        def work():
            try:
                self.prs = pull_requests(self.repo)
                self.apply_prs()
            finally:
                self.prs_loading = False
        self.prs_loading = True
        threading.Thread(target=work, daemon=True).start()

    def apply_prs(self):
        for entry in self.entries:
            if not entry.get("main") and "branch" in entry:
                entry["pr"] = self.prs.get(entry["name"])

    def busy(self, label, work):
        """Run work on a thread while showing a spinner. Keys pressed meanwhile are ignored."""
        result = {}

        def target():
            try:
                result["value"] = work()
            except BaseException as error:  # Re-raised on this thread below.
                result["error"] = error

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(0.15)  # Quick work finishes without a spinner flashing up.
        previous, frame, lost = STATE.get("timeout", -1), 0, False
        set_timeout(self.stdscr, 80)
        try:
            while thread.is_alive():
                render(self.stdscr, [[], [(SPINNER[frame % len(SPINNER)] + " " + label, style("cyan"))]])
                frame += 1
                try:
                    read_key(self.stdscr)
                except FocusLost:
                    lost = True  # Finish the work first; it mustn't be left half done.
        finally:
            set_timeout(self.stdscr, previous)
        if "error" in result:
            raise result["error"]
        if lost:
            raise FocusLost()
        return result.get("value")

    @property
    def cwd(self):
        # The original directory disappears if its worktree is removed.
        return self.origin_cwd if os.path.isdir(self.origin_cwd) else self.repo

    def reload(self):
        self.entries = base_entries(self.cwd)
        self.commits = {}
        load_status_async(self.entries, self.session)
        self.apply_prs()

    def say(self, text, tone="red"):
        self.message, self.message_style = text, tone

    def list_screen(self):
        stdscr = self.stdscr
        query, qpos, filtering, pending = "", 0, False, None
        selected = next((e for e in self.entries if e.get("current")), None)
        top = 0
        while True:
            loading = self.prs_loading or any(PENDING in (e["dirty"], e["open"]) for e in self.entries)
            set_timeout(stdscr, 150 if loading else -1)
            shown = filter_entries(self.entries, query)
            if selected not in shown:
                selected = shown[0] if shown else None
            height, _ = stdscr.getmaxyx()
            preview_rows = 7 if height >= 22 else 0
            capacity = max(1, height - 8 - preview_rows)
            index = shown.index(selected) if selected in shown else 0
            top = min(max(top, index - capacity + 1), index, max(0, len(shown) - capacity))
            widths = column_widths(self.entries)

            if not pending_input(stdscr):
                rows = [[], [("Worktrees", style("bold")), ("  " + repo_name(self.repo), style("dim")),
                             ("   " + self.summary(), style("dim"))],
                        search_row(query, filtering, "/ to filter"), [], header_row(widths)]
                for e in shown[top:top + capacity]:
                    rows.append(entry_row(e, e is selected, widths,
                                          match_entry(e, query) if query.strip() else None))
                if not shown:
                    rows.append([("No matching worktrees", style("dim"))])
                rows += [[]] * max(0, height - len(rows) - 3 - preview_rows)
                if preview_rows:
                    rows += self.preview(selected)
                if self.message:
                    icon = {"green": "ok", "red": "error"}.get(self.message_style, "info")
                    rows.append([(ICONS[icon] + " " + self.message, style(self.message_style))])
                else:
                    rows.append([(f"{len(shown)}/{len(self.entries)}", style("dim"))])
                if filtering:
                    rows.append(help_row(("enter", "done"), ("esc", "clear")))
                else:
                    rows.append(help_row(("enter", "open"), ("s", "split"), ("n", "new"), ("dd", "remove"),
                                         ("?", "help"), ("esc", "clear filter") if query else ("q", "quit")))
                render(stdscr, rows, cursor=(2, 1 + qpos) if filtering else None)

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
                    query, qpos = edit_text(query, qpos, key)
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
                filtering, qpos = True, len(query)
            elif key == "?":
                show_help(stdscr, "Worktrees", [("Move", NAVIGATION_HELP), ("Actions", [
                    ("enter / l", "open the worktree's tab"),
                    ("s", "open the worktree in a split beside your pane"),
                    ("o", "open its tab in the background and stay here"),
                    ("n", "new worktree"),
                    ("dd", "remove the worktree"),
                    ("p", "prune missing worktrees"),
                    ("r", "refresh"),
                    ("/", "filter (fuzzy)"),
                    ("esc", "clear the filter, or quit"),
                    ("q", "quit"),
                ]), ("Labels", [
                    ("●", "the worktree you opened this from"),
                    ("3d", "time since the worktree's last commit"),
                    (ICONS["main"] + " main checkout", "the original checkout; it can't be removed"),
                    (ICONS["tab"] + " tab open", "a Rex tab is in this worktree"),
                    (ICONS["dirty"] + " uncommitted", "has uncommitted changes"),
                    (ICONS["ahead"] + " ahead / " + ICONS["behind"] + " behind", "commits not yet pushed / pulled"),
                    (ICONS["deleted"] + " remote deleted", "its remote branch is gone, often after merging"),
                    (ICONS["missing"] + " folder missing", "the folder is gone; press p to prune"),
                    (ICONS["pr"] + " #12 open", "its pull request: open, draft, merged, or closed"),
                    ("herdr, t3, claude, rex", "the tool that created the worktree"),
                ])])
            elif key == ESCAPE and query:
                query = ""
            elif key in (ESCAPE, "q"):
                return
            elif key == "n":
                if self.new_screen():
                    return
                set_timeout(stdscr, -1)
            elif key == "p":
                self.prune()
                selected, query = None, ""
            elif key == "r":
                path = selected["worktree"] if selected else None
                self.reload()
                if self.show_prs:
                    self.load_prs()
                selected = next((e for e in self.entries if e["worktree"] == path), None)
                self.say("Refreshed.", "dim")
            elif key in ("s", "o") and selected:
                if missing(selected):
                    self.say("This worktree's directory is missing; press p to prune it.")
                    continue
                try:
                    if key == "s":
                        self.busy("Opening split…", lambda: open_split(self.session, selected, self.origin))
                        return
                    entry = selected
                    opened = self.busy("Opening tab…", lambda: open_tab(self.session, entry, focus=False))
                    entry["open"] = True
                    self.say(f"Opened {tab_label(entry)} in the background." if opened
                             else f"{entry['name']} already has a tab open.", "green" if opened else "dim")
                except (RuntimeError, OSError) as error:
                    self.say(str(error))
            elif key in ENTER or key == "l":
                if selected is None:
                    continue
                if missing(selected):
                    self.say("This worktree's directory is missing; press p to prune it.")
                    continue
                try:
                    entry = selected
                    self.busy("Opening…", lambda: open_tab(self.session, entry, self.client))
                    return
                except (RuntimeError, OSError) as error:
                    self.say(str(error))

    def summary(self):
        counts = [(len(self.entries), "worktree"),
                  (sum(e.get("dirty") is True for e in self.entries), "uncommitted"),
                  (sum(REMOTE_DELETED in (e.get("track") or []) for e in self.entries), "remote deleted"),
                  (sum(missing(e) for e in self.entries), "folder missing")]
        parts = [f"{counts[0][0]} worktree{'s' if counts[0][0] != 1 else ''}"]
        parts += [f"{n} {label}" for n, label in counts[1:] if n]
        return " · ".join(parts)

    def preview(self, entry):
        if entry is None:
            return [[]] * 7
        path = entry["worktree"]
        if path not in self.commits:
            self.commits[path] = recent_commits(path, base=self.default)
        description, commits = self.commits[path]
        rows = [[("─── ", style("dim")), (ICONS["folder"] + " " + tilde(path), style("dim")),
                 (("  ·  " + description) if description else "", style("dim"))]]
        if missing(entry):
            rows.append([(ICONS["missing"] + " Folder missing", style("red"))])
        elif not commits:
            rows.append([("No commits", style("dim"))])
        author_width = min(16, max([len(c[2]) for c in commits] + [6]))
        for sha, subject, author, stamp in commits[:5]:
            rows.append([(sha, style("yellow")), ("  " + short_age(stamp).rjust(3), style("dim")),
                         ("  " + author[:author_width].ljust(author_width), style("blue")), ("  " + subject, 0)])
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
        options = []  # Checkboxes, toggled with space.
        if files:
            options.append({"id": "force", "checked": False,
                            "label": [("Discard uncommitted changes", style("red"))]})
        if merged:
            options.append({"id": "delete_branch", "checked": False,
                            "label": [("Also delete branch ", 0), (entry["name"], style("bold")),
                                      ("   (no unmerged commits)", style("dim"))]})
        checked = self.removal_options(entry, files, windows, merged, options)
        if checked is None:
            return False
        delete_branch = "delete_branch" in checked
        try:
            self.busy(f"Removing {entry['name']}…", lambda: remove_worktree(
                self.repo, self.session, entry, windows, force="force" in checked, delete_branch=delete_branch))
        except (RuntimeError, OSError) as error:
            self.say(str(error))
        else:
            self.say(f"Removed {entry['name']}" + (" and its branch" if delete_branch else ""), "green")
        self.reload()
        return False

    def removal_options(self, entry, files, windows, merged, options):
        """Show the removal confirmation. Returns the set of checked option ids, or None if cancelled."""
        stdscr, cursor, message = self.stdscr, 0, None
        set_timeout(stdscr, -1)
        while True:
            rows = [[], [("Remove worktree?", style("bold"))], [],
                    [(entry["name"], style("bold"))], [(tilde(entry["worktree"]), style("dim"))], []]
            if files:
                rows.append([(f"Uncommitted changes ({len(files)}):", style("yellow"))])
                rows += [[("  " + f, 0)] for f in files[:8]]
                if len(files) > 8:
                    rows.append([(f"  …and {len(files) - 8} more", style("dim"))])
                rows.append([])
            if windows:
                rows.append([("These tabs and all their panes will close:", style("yellow"))])
                rows += [[("  " + (w.get("label") or w["window_id"]), 0)] for w in windows]
                rows.append([])
            for i, option in enumerate(options):
                box = "[x] " if option["checked"] else "[ ] "
                option_row = [pointer(i == cursor), (box, style("cyan"))] + option["label"]
                rows.append(Selected(option_row) if i == cursor else option_row)
            if not merged and "branch" in entry:
                rows.append([("The branch ", 0), (entry["name"], style("bold")), (" will be kept.", 0)])
            rows.append([])
            checked = {o["id"] for o in options if o["checked"]}
            if message:
                rows.append([(ICONS["error"] + " " + message, style("red"))])
            elif files and "force" not in checked:
                rows.append([("check “Discard uncommitted changes” to remove", style("dim"))])
            help_items = []
            if options:
                help_items.append(("space", "toggle"))
            if len(options) > 1:
                help_items.append(("j/k", "move"))
            rows.append(help_row(*help_items, ("enter", "remove"), ("esc", "cancel")))
            render(stdscr, rows)
            key = read_key(stdscr)
            message = None
            if key == ESCAPE:
                return None
            if key == " " and options:
                options[cursor]["checked"] = not options[cursor]["checked"]
            elif key in ("j", curses.KEY_DOWN, "\x0e") and options:
                cursor = min(len(options) - 1, cursor + 1)
            elif key in ("k", curses.KEY_UP, "\x10") and options:
                cursor = max(0, cursor - 1)
            elif key in ENTER:
                if files and "force" not in checked:
                    message = "Check “Discard uncommitted changes” first, or press esc to cancel."
                    continue
                return checked

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
                self.busy("Pruning…", lambda: prune_worktrees(self.repo))
                self.say(f"Pruned {len(stale)} missing worktree{'s' if len(stale) != 1 else ''}.", "green")
            except (RuntimeError, OSError) as error:
                self.say(str(error))
            self.reload()

    def new_screen(self):
        """Pick a branch for a new worktree. Returns True once one is created and opened."""
        stdscr = self.stdscr
        set_timeout(stdscr, -1)
        cwd = self.cwd
        branches = self.busy("Loading branches…", lambda: load_branches(cwd, self.entries))
        set_timeout(stdscr, -1)
        suggestions = branches["suggestions"]
        text, tpos, index, top, message, filtering, pending = "", 0, 0, 0, None, False, None
        while True:
            # items[0] creates a new branch; the rest are existing branches.
            existing = [s for s in suggestions if fuzzy_all(text, s["name"])]
            if text.strip():
                existing.sort(key=lambda s: -fuzzy_all(text, s["name"]))
            items = [None] + existing
            index = min(index, len(items) - 1)
            plan = plan_branch(cwd, branches, "", items[index]) if index else None

            height, _ = stdscr.getmaxyx()
            capacity = max(1, height - 11)
            list_index = max(0, index - 1)
            top = min(max(top, list_index - capacity + 1), list_index, max(0, len(existing) - capacity))

            if not pending_input(stdscr):
                rows = [[], [("New worktree", style("bold")), ("  " + repo_name(self.repo), style("dim"))],
                        search_row(text, filtering, "/ to filter"), [],
                        (Selected if index == 0 else list)([pointer(index == 0), ("+ Create a new branch…", style("green") | (style("bold") if index == 0 else 0))]),
                        [], [(f"EXISTING BRANCHES ({len(existing)})", style("dim") | style("bold"))]]
                ref_width = min(60, max([len(s["name"]) for s in existing[top:top + capacity]] + [10]))
                for i, item in enumerate(existing[top:top + capacity], top + 1):
                    hits = set()
                    for token in text.lower().split():
                        hits.update((fuzzy(token, item["name"]) or (0, []))[1])
                    ref = item["name"][:ref_width]
                    row = [pointer(i == index)]
                    row += highlighted(ref, hits, style("bold") if i == index else 0)
                    row += [(" " * (ref_width - len(ref) + 3), 0),
                            (ICONS[item["kind"]] + " " + source_label(item),
                             style("blue") if item["kind"] == "remote" else style("dim"))]
                    rows.append(Selected(row) if i == index else row)
                if not existing:
                    rows.append([("  No matching branches" if text.strip() else "  No other branches", style("dim"))])
                rows += [[]] * max(0, height - len(rows) - 4)
                rows.append([])
                if plan is None:
                    rows.append([("enter: ", style("dim")), ("create a new branch", 0)])
                elif plan.get("error"):
                    rows.append([(ICONS["error"] + " " + plan["error"], style("red"))])
                else:
                    rows.append([("enter: ", style("dim")), (plan["action"], 0)])
                rows.append([(ICONS["error"] + " " + message, style("red"))] if message else [])
                if filtering:
                    rows.append(help_row(("enter", "done"), ("esc", "clear")))
                else:
                    rows.append(help_row(("enter", "select"), ("/", "filter"), ("?", "help"),
                                         ("esc", "clear filter") if text else ("q", "back")))
                render(stdscr, rows, cursor=(2, 1 + tpos) if filtering else None)
            key = read_key(stdscr)
            message = None
            if key is None or key == curses.KEY_RESIZE:
                continue
            if filtering:
                if key in ENTER:
                    filtering = False
                elif key == ESCAPE:
                    filtering, text, index = False, "", 0
                elif key in BACKSPACE and not text:
                    filtering = False
                elif key in (curses.KEY_DOWN, "\x0e"):
                    index = min(len(items) - 1, index + 1)
                elif key in (curses.KEY_UP, "\x10"):
                    index = max(0, index - 1)
                else:
                    new_text, tpos = edit_text(text, tpos, key)
                    if new_text != text:
                        # Keep the first match selected while typing.
                        text, index = new_text, 1 if new_text.strip() else 0
                continue
            new_index, pending = motion(key, index, len(items), capacity, pending)
            if new_index is not None:
                index = new_index
            elif key == "/":
                filtering, tpos = True, len(text)
            elif key == "?":
                show_help(stdscr, "New worktree", [("Move", NAVIGATION_HELP), ("Actions", [
                    ("enter / l", "create a worktree for the selected branch"),
                    ("", "or, on the first row, create a new branch"),
                    ("/", "filter existing branches"),
                    ("esc", "clear the filter, or go back"),
                    ("q", "go back"),
                ])])
            elif key == ESCAPE and text:
                text, index = "", 0
            elif key in (ESCAPE, "q"):
                return False
            elif key in ENTER or key == "l":
                if plan is None:
                    if self.new_branch_screen(cwd, branches, text):
                        return True
                    set_timeout(stdscr, -1)
                    continue
                if plan.get("error"):
                    continue  # The action line already shows the error.
                try:
                    self.create_and_open(cwd, plan)
                except RuntimeError as error:
                    if not os.path.isdir(plan["path"]):
                        message = str(error)
                        continue
                    raise
                return True

    def new_branch_screen(self, cwd, branches, name=""):
        """Name a new branch and choose its base. Returns True once a worktree is created and opened."""
        stdscr = self.stdscr
        set_timeout(stdscr, -1)
        bases = branches["bases"]
        text, tpos, base, message = name, len(name), 0, None
        while True:
            plan = plan_branch(cwd, branches, text, None, bases[base])
            typed = normalize_branch(text)
            rows = [[], [("New branch", style("bold")), ("  " + repo_name(self.repo), style("dim"))], [],
                    [("Name   ", style("dim")), (text, 0)]]
            if plan.get("error") and text.strip():
                rows.append([("       ", 0), (ICONS["error"] + " " + plan["error"], style("red"))])
            elif typed and typed != text.strip():
                rows.append([("       → ", style("dim")), (typed, style("bold"))])
            else:
                rows.append([])
            rows.append([])
            for i, option in enumerate(bases):
                label = "Base   " if i == 0 else "       "
                base_row = [(label, style("dim")), pointer(i == base),
                            (option["label"], style("bold") if i == base else 0),
                            ("  current branch" if option["ref"] == "HEAD" else "  default branch", style("dim"))]
                rows.append(Selected(base_row) if i == base else base_row)
            height, _ = stdscr.getmaxyx()
            rows += [[]] * max(0, height - len(rows) - 4)
            rows.append([])
            if text.strip() and not plan.get("error"):
                rows.append([("enter: ", style("dim")), (plan["action"], 0)])
            else:
                rows.append([("Type a name for the new branch.", style("dim"))])
            rows.append([(ICONS["error"] + " " + message, style("red"))] if message else [])
            rows.append(help_row(("enter", "create"), *([("tab/↑↓", "base")] if len(bases) > 1 else []),
                                 ("esc", "back")))
            render(stdscr, rows, cursor=(3, 7 + tpos))
            key = read_key(stdscr)
            message = None
            if key is None or key == curses.KEY_RESIZE:
                continue
            if key == ESCAPE:
                return False
            if key in ("\t", curses.KEY_DOWN, "\x0e"):
                base = (base + 1) % len(bases)
            elif key in (curses.KEY_BTAB, curses.KEY_UP, "\x10"):
                base = (base - 1) % len(bases)
            elif key in ENTER:
                if not plan.get("error"):
                    try:
                        self.create_and_open(cwd, plan)
                        return True
                    except RuntimeError as error:
                        if os.path.isdir(plan["path"]):
                            raise
                        message = str(error)
                elif not text.strip():
                    message = "Type a name for the new branch."
            else:
                text, tpos = edit_text(text, tpos, key)

    def create_and_open(self, cwd, plan):
        """Create the worktree, then open its tab. Raises RuntimeError on failure."""
        try:
            self.busy(f"Creating worktree for {plan['branch']}…", lambda: create_worktree(cwd, plan))
        except OSError as error:
            raise RuntimeError(str(error)) from error
        try:
            self.busy("Opening tab…", lambda: open_tab(
                self.session, {"worktree": str(plan["path"]), "branch": plan["branch"]}, self.client,
                setup_command(cwd)))
        except (RuntimeError, OSError) as error:
            raise RuntimeError(f"Worktree created at {plan['path']}, but opening its tab failed: {error}") from error


def source_label(item):
    """'local', 'remote' for origin, or the remote's name for any other remote."""
    if item["kind"] != "remote":
        return "local"
    remote = item["ref"][:-len(item["name"]) - 1]
    return "remote" if remote == "origin" else remote


def pointer(selected):
    return ("› " if selected else "  ", style("cyan") | style("bold"))


def fuzzy_all(query, text):
    """Score text against every token in query; 0 means no match. An empty query matches everything."""
    total = 1
    for token in query.lower().split():
        match = fuzzy(token, text)
        if not match:
            return 0
        total += match[0]
    return total


def main(stdscr, mode, cwd, session, client="", origin=""):
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    init_colors()
    stdscr.keypad(True)
    STATE["session"] = session
    # Ask the terminal to report focus changes, so the picker closes when another pane is focused.
    sys.stdout.write("\x1b[?1004h")
    sys.stdout.flush()
    render(stdscr, [[], [("Loading worktrees…", style("dim"))]])
    try:
        manager = Manager(stdscr, cwd, session, client, origin)
        if mode == "new":
            manager.new_screen()
        else:
            manager.list_screen()
    except FocusLost:
        STATE["navigated"] = True  # Focus already moved where the user wanted it.
    except (RuntimeError, OSError, UnicodeError, ValueError) as error:
        set_timeout(stdscr, -1)
        render(stdscr, [[], [("Worktree operation failed", style("red") | style("bold"))], [],
                        [(str(error), 0)], [], [("Press any key to close.", style("dim"))]])
        try:
            read_key(stdscr)
        except FocusLost:
            STATE["navigated"] = True


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
        restore_focus(session, origin)
