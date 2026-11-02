"""Branches for new worktrees: listing them, naming new ones, and creating worktrees."""
import functools
import hashlib
import os
import re
import time
from pathlib import Path

import util
import worktrees


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
    common = util.git_text(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    # Hash the common Git directory to distinguish repositories with the same name.
    return worktrees.repo_name(cwd) + "-" + hashlib.sha256(os.fsencode(os.path.realpath(common))).hexdigest()[:10]


def new_path(cwd, branch):
    """Where a new worktree goes: a folder named after the branch, or, when that folder already
    exists (for example a/b and a-b both make a-b), the name plus a short hash of the branch."""
    folder = Path.home() / ".local/share/rex/worktrees" / worktree_root_key(cwd)
    slug = branch.replace("/", "-")[:100]
    path = folder / slug
    if path.exists():
        path = folder / (slug + "-" + hashlib.sha256(branch.encode()).hexdigest()[:8])
    return path


RECENT_DAYS = 30


def load_branches(cwd, entries, now=None):
    """Collect branches without worktrees: recent local ones, then remote-only ones, then older local ones."""
    checked_out = {e["branch"].removeprefix("refs/heads/"): e["worktree"] for e in entries if "branch" in e}

    def refs(namespace):
        out = util.git_text(cwd, "for-each-ref", "--sort=-committerdate",
                       "--format=%(refname:short)%00%(committerdate:unix)", namespace)
        return [(ref, int(stamp or 0)) for ref, _, stamp in (line.partition("\0") for line in out.splitlines())]

    local = refs("refs/heads")
    local_names = {name for name, _ in local}
    remotes = sorted(util.git_text(cwd, "remote").splitlines(), key=len, reverse=True)
    remote = {}
    remote_refs = []
    for ref, stamp in refs("refs/remotes"):
        for name in remotes:
            if ref.startswith(name + "/") and ref != name + "/HEAD":
                remote.setdefault(ref[len(name) + 1:], []).append(ref)
                remote_refs.append((ref[len(name) + 1:], ref, stamp))
                break
    head = util.git_text(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if head == "HEAD":
        head = util.git_text(cwd, "rev-parse", "--short", "HEAD")
    # The default branch comes first, so new branches start from it unless you choose otherwise.
    bases = [{"ref": "HEAD", "label": head}]
    default = worktrees.default_branch(cwd)
    if default and default != head:
        bases.insert(0, {"ref": default, "label": default})
    cutoff = (time.time() if now is None else now) - RECENT_DAYS * 86400
    available = [(name, stamp) for name, stamp in local if name not in checked_out]
    suggestions = [{"name": n, "ref": n, "kind": "local", "time": stamp} for n, stamp in available if stamp >= cutoff]
    suggestions += [{"name": name, "ref": ref, "kind": "remote", "time": stamp} for name, ref, stamp in remote_refs
                    if name not in local_names and name not in checked_out]
    suggestions += [{"name": n, "ref": n, "kind": "local", "time": stamp} for n, stamp in available if stamp < cutoff]
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
        if not util.git_ok(cwd, "check-ref-format", "--branch", name):
            return {"branch": name, "error": "Not a valid branch name."}
        if name in branches["checked_out"]:
            return {"branch": name, "error": "Already checked out at " + util.tilde(branches["checked_out"][name])}
        if name in branches["local"]:
            return {"branch": name, "error": "A branch with this name exists; select it under Existing branches."}
        if branches["remote"].get(name):
            return {"branch": name, "error": f"{branches['remote'][name][0]} exists; select it under Existing branches."}
        # --no-track keeps a branch started from origin/main from pushing to or tracking main.
        plan = {"branch": name, "action": f"create new branch {name} from {base['label']}",
                "args": ["--no-track", "-b", name, None, base["ref"]]}
    path = new_path(cwd, plan["branch"])
    if path.exists():
        plan["error"] = "Destination already exists: " + util.tilde(str(path))
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
    util.git(cwd, "worktree", "add", *args)


def source_label(item):
    """'local', 'remote' for origin, or the remote's name for any other remote."""
    if item["kind"] != "remote":
        return "local"
    remote = item["ref"][:-len(item["name"]) - 1]
    return "remote" if remote == "origin" else remote
