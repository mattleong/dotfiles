"""Operations that combine Git and Rex: loading status, removing worktrees, and copying."""
import os
import queue
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import tabs
import util
import worktrees


def fill_status(entries, session):
    """Fill in the slower fields: uncommitted changes and open tabs."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        for entry, state in zip(entries, pool.map(worktrees.is_dirty, entries)):
            entry["dirty"] = state
    terminals = tabs.terminal_cwds(session)
    for entry in entries:
        entry["open"] = tabs.open_in(terminals, entry["worktree"])


def load_entries(cwd, session):
    entries = worktrees.base_entries(cwd)
    fill_status(entries, session)
    return entries


def load_status_async(entries, session, scan=True):
    """Fill status on daemon threads, so the list appears at once and exiting never waits.
    With scan=False, the caller looks up open tabs itself."""
    work = queue.Queue()
    for entry in entries:
        work.put(entry)

    def dirty_worker():
        while True:
            try:
                entry = work.get_nowait()
            except queue.Empty:
                return
            entry["dirty"] = worktrees.is_dirty(entry)

    def open_worker():
        try:
            terminals = tabs.terminal_cwds(session)
        except (RuntimeError, ValueError, OSError):
            terminals = []
        for entry in entries:
            entry["open"] = tabs.open_in(terminals, entry["worktree"])

    for target in [dirty_worker] * min(6, max(1, len(entries))) + ([open_worker] if scan else []):
        threading.Thread(target=target, daemon=True).start()


def copy_to_clipboard(text):
    """Copy with pbcopy, wl-copy, or xclip, whichever exists."""
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            subprocess.run(command, input=text.encode(), check=True)
            return
    raise RuntimeError("No clipboard command found (pbcopy, wl-copy, or xclip).")


def prune_worktree(repo, entry):
    """Drop Git's record of a worktree whose folder is missing, and nothing else: its branch, and
    the other missing worktrees, stay. git worktree remove does just that for a missing folder,
    without --force, where git worktree prune would drop every missing worktree at once. A folder
    that's back at the path is refused, since removing the worktree would delete it."""
    if os.path.lexists(entry["worktree"]):
        raise RuntimeError("its folder is back; press r to refresh the list.")
    util.git(repo, "worktree", "remove", "--", entry["worktree"])


def take_out(repo, entry, force=False):
    """Have Git remove a worktree, or prune it when its folder is missing (see prune_worktree).
    Raises RuntimeError when Git still lists it, so nothing was done. Returns what went wrong when
    Git dropped it but couldn't delete all of its folder, else ""."""
    if worktrees.missing(entry):
        prune_worktree(repo, entry)
        return ""
    try:
        util.git(repo, "worktree", "remove", *(["--force"] if force else []), "--", entry["worktree"])
    except RuntimeError as error:
        # Git can drop a worktree from its list and still fail to delete its folder.
        if registered(repo, entry["worktree"]):
            raise
        return (f"its folder couldn't be deleted entirely: {error}" if os.path.lexists(entry["worktree"])
                else str(error))
    return ""


def remove_worktree(repo, session, entry, closing, force=False, delete_branch=False):
    """Remove a worktree from the main worktree's repository (see take_out), delete its branch
    when asked, then close the panes inside it (closing, from tabs.panes_to_close). Raises
    RuntimeError when Git still lists the worktree, so nothing was done. Otherwise returns whether
    the branch was deleted, and what else went wrong, as clauses such as "its branch wasn't
    deleted: …", so a message can say what did and didn't happen."""
    problem = take_out(repo, entry, force)
    problems, deleted = [problem] if problem else [], False
    if delete_branch:
        try:
            # Callers only offer this after merged_into() confirmed the branch is merged.
            util.git(repo, "branch", "-D", "--", entry["name"])
            deleted = True
        except RuntimeError as error:
            problems.append(f"its branch wasn't deleted: {error}")
    try:
        if closing:
            tabs.close_panes(session, closing)
    except (RuntimeError, OSError) as error:
        problems.append(f"a pane couldn't be closed: {error}")
    return deleted, problems


def registered(repo, path):
    """Whether Git still lists a worktree at path."""
    try:
        listed = worktrees.worktrees(repo)
    except (RuntimeError, OSError):
        return os.path.isdir(path)
    real = os.path.realpath(path)
    return any(os.path.realpath(e["worktree"]) == real for e in listed)


def remove_many(repo, session, items, terminals=None):
    """Remove several worktrees, and prune the ones whose folder is missing, keeping their branches
    (see prune_worktree). The panes inside the removed ones close at the end, so the picker's own
    tab closes last. items holds (entry, delete_branch) pairs. Returns (the names removed, the
    names pruned, how many branches were deleted, sentences saying what didn't happen). A worktree
    counts as removed once Git no longer lists it, even if some of its folder is left; a sentence
    says so."""
    removed, pruned, deleted, failures, paths = [], [], 0, [], []
    if terminals is None:
        try:
            terminals = tabs.terminal_cwds(session)
        except (RuntimeError, ValueError, OSError):
            terminals = []
    for entry, delete_branch in items:
        prune = worktrees.missing(entry)
        try:
            gone, problems = remove_worktree(repo, session, entry, [], delete_branch=delete_branch and not prune)
        except (RuntimeError, OSError) as error:
            failures.append(f"{entry['name']} wasn't {'pruned' if prune else 'removed'}: {error}")
            continue
        (pruned if prune else removed).append(entry["name"])
        if not prune:
            paths.append(entry["worktree"])
        deleted += gone
        failures += [f"{entry['name']} was removed, but {problem}" for problem in problems]
    try:
        tabs.close_panes(session, tabs.panes_to_close(terminals, paths))
    except (RuntimeError, OSError) as error:
        failures.append(f"a pane couldn't be closed: {error}")
    return removed, pruned, deleted, failures
