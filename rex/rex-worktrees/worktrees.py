"""Git worktree state: listing worktrees and reading their status and history."""
import functools
import os
import re
import subprocess
from pathlib import Path

import util


def worktrees(cwd):
    raw = util.git(cwd, "worktree", "list", "--porcelain", "-z")
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


def branch_name(entry):
    if "branch" in entry:
        return entry["branch"].removeprefix("refs/heads/")
    return "detached " + entry.get("HEAD", "")[:7]


@functools.lru_cache(maxsize=None)
def repo_name(cwd):
    """Name the repository after its main worktree, or its bare directory."""
    return repo_label(worktrees(cwd)[0]["worktree"])


def repo_label(main_path):
    name = Path(main_path).name
    return name[:-4] if name.endswith(".git") and len(name) > 4 else name


def setup_command(cwd):
    """The repository's opt-in setup command for new worktrees: git config rex.worktreeSetup."""
    result = subprocess.run(["git", "-C", cwd, "config", "--get", "rex.worktreeSetup"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return os.fsdecode(result.stdout).strip() if result.returncode == 0 else ""


REMOTE_DELETED = "remote deleted"


def tracking(cwd):
    out = util.git_text(cwd, "for-each-ref", "--format=%(refname)%00%(upstream:track)", "refs/heads")
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
        return bool(util.git(entry["worktree"], "status", "--porcelain", "--untracked-files=normal"))
    except (RuntimeError, OSError):
        return None


def current_entry(entries, cwd):
    matches = [e for e in entries if util.inside(cwd, e["worktree"])]
    return max(matches, key=lambda e: len(os.path.realpath(e["worktree"])), default=None)


def base_entries(cwd, raw=None, details=True):
    """List worktrees quickly, with their ages, which the list is sorted by, so it doesn't reorder
    once it shows; status fields start as PENDING. With details=False, ahead/behind is left for
    fill_details, so the list can appear sooner."""
    raw = worktrees(cwd) if raw is None else raw
    if not raw:
        raise RuntimeError("No worktrees found")
    raw[0]["main"] = True
    entries = [e for e in raw if "bare" not in e]
    current = current_entry(entries, cwd)
    times = commit_times(cwd, [e["HEAD"] for e in entries if e.get("HEAD")])  # One quick Git call.
    for entry in entries:
        entry.update(name=branch_name(entry), current=entry is current, dirty=util.PENDING, open=util.PENDING,
                     track=[], time=times.get(entry.get("HEAD")))
    if details:
        fill_details(cwd, entries)
    return entries


def fill_details(cwd, entries):
    """Ahead/behind, from one for-each-ref."""
    tracks = tracking(cwd)
    for entry in entries:
        entry["track"] = tracks.get(entry.get("branch"), [])


def commit_times(cwd, shas):
    """Commit times for a list of commits, in one Git call."""
    if not shas:
        return {}
    try:
        out = util.git_text(cwd, "show", "-s", "--format=%H %ct", *sorted(set(shas)))
    except RuntimeError:
        return {}
    return {sha: int(stamp) for sha, _, stamp in (line.partition(" ") for line in out.splitlines()) if stamp}


def recent_commits(path, count=5, base=None):
    """Commits for the preview: those not in base when there are any, else the latest ones.
    Returns (description, [(sha, subject, author, unix time)])."""
    if not os.path.isdir(path):
        return "", []
    since = 0
    if base:
        try:
            since = int(util.git_text(path, "rev-list", "--count", f"{base}..HEAD") or 0)
        except (RuntimeError, ValueError):
            since = 0
    try:
        out = util.git_text(path, "log", f"-n{count}", "--format=%h%x00%s%x00%an%x00%ct",
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


def removal_problem(entry):
    """Reasons a worktree can't be removed or pruned at all, even with force. One whose folder is
    missing can be: dd prunes it."""
    if entry.get("main"):
        return "The main worktree can't be removed."
    if "locked" in entry:
        if missing(entry):
            # As for a folder on a drive that isn't mounted: the lock is what keeps Git from pruning it.
            return "This worktree is locked, so Git keeps it though its folder is missing; unlock it to prune it."
        return "This worktree is locked."
    return None


def changed_files(entry):
    out = util.git(entry["worktree"], "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")
    return [line for line in os.fsdecode(out).splitlines() if line]


def default_branch(cwd):
    """The remote's default branch, such as origin/main or upstream/master.

    Uses each remote's recorded HEAD, trying origin first, then common names on each remote, then
    a local main or master when there's no remote."""
    remotes = util.git_text(cwd, "remote").splitlines()
    remotes.sort(key=lambda name: name != "origin")
    for remote in remotes:
        result = subprocess.run(["git", "-C", cwd, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if result.returncode == 0 and result.stdout.strip():
            return os.fsdecode(result.stdout).strip()
    for ref in [f"{r}/{name}" for r in remotes for name in ("main", "master")] + ["main", "master"]:
        if util.git_ok(cwd, "rev-parse", "--verify", "--quiet", ref + "^{commit}"):
            return ref
    return None


def merged_into(cwd, entry):
    """Return the default branch a worktree's branch is fully merged into, or None."""
    if "branch" not in entry:
        return None
    target = default_branch(cwd)
    if not target or target in (entry["name"], "origin/" + entry["name"]):
        return None
    return target if util.git_ok(cwd, "merge-base", "--is-ancestor", entry["branch"], target) else None


def cleanup_reason(entry):
    """Why a worktree looks finished: its pull request merged or its remote branch was deleted."""
    if entry.get("main") or "locked" in entry or missing(entry):
        return None
    pr = entry.get("pr")
    if pr and pr.get("state") == "MERGED":
        return f"#{pr['number']} merged"
    if REMOTE_DELETED in (entry.get("track") or []):
        return REMOTE_DELETED
    return None


def worktree_source(entry):
    """The tool that created a worktree, judged by where it lives: herdr, t3, claude, rex, or ''."""
    path = util.tilde(entry["worktree"])
    for folder, name in util.WORKTREE_SOURCES:
        if path.startswith("~/" + folder + "/"):
            return name
    return "claude" if "/.claude/worktrees/" in path else ""
