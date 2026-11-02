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
        entry["open"] = any(util.inside(path, entry["worktree"]) for _, path in terminals)


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
            entry["open"] = any(util.inside(path, entry["worktree"]) for _, path in terminals)

    for target in [dirty_worker] * min(6, max(1, len(entries))) + ([open_worker] if scan else []):
        threading.Thread(target=target, daemon=True).start()


def copy_to_clipboard(text):
    """Copy with pbcopy, wl-copy, or xclip, whichever exists."""
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            subprocess.run(command, input=text.encode(), check=True)
            return
    raise RuntimeError("No clipboard command found (pbcopy, wl-copy, or xclip).")


def remove_worktree(repo, session, entry, windows, force=False, delete_branch=False):
    """Remove a worktree from the main worktree's repository, then close its tabs."""
    util.git(repo, "worktree", "remove", *(["--force"] if force else []), "--", entry["worktree"])
    problems = []
    if delete_branch:
        try:
            # Callers only offer this after merged_into() confirmed the branch is merged.
            util.git(repo, "branch", "-D", "--", entry["name"])
        except RuntimeError as error:
            problems.append(f"branch not deleted: {error}")
    try:
        if windows:
            tabs.close_worktree_tabs(session, windows)
    except (RuntimeError, OSError) as error:
        problems.append(f"a tab could not be closed: {error}")
    if problems:
        raise RuntimeError("Worktree removed, but " + "; ".join(problems))


def remove_many(repo, session, items, terminals=None):
    """Remove several worktrees, closing their tabs at the end so the picker's own tab closes last.
    items holds (entry, delete_branch) pairs. Returns (removed names, failure messages)."""
    removed, failures, windows = [], [], {}
    for entry, delete_branch in items:
        try:
            for window in tabs.windows_for_path(session, entry["worktree"], terminals):
                windows[window["window_id"]] = window
            remove_worktree(repo, session, entry, [], delete_branch=delete_branch)
            removed.append(entry["name"])
        except (RuntimeError, OSError) as error:
            if os.path.isdir(entry["worktree"]):
                failures.append(f"{entry['name']}: {error}")
            else:
                removed.append(entry["name"])
                failures.append(f"{entry['name']}: {error}")
    try:
        tabs.close_worktree_tabs(session, list(windows.values()))
    except (RuntimeError, OSError) as error:
        failures.append(f"a tab could not be closed: {error}")
    return removed, failures
