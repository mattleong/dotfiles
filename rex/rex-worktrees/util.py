"""Shared helpers: running commands, Git calls, paths, and picker-wide state."""
import os
import subprocess
from pathlib import Path


TERMINAL = "com.superlogical.terminal"


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


def inside(path, root):
    try:
        root = os.path.realpath(root)
        return os.path.commonpath((root, os.path.realpath(path))) == root
    except ValueError:
        return False


def tilde(path):
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path
