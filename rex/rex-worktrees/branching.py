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
    exists (for example a/b and a-b both make a-b), the name plus a short hash of the branch. A
    name over 100 characters loses its middle, keeping the end, where long names usually differ."""
    folder = Path.home() / ".local/share/rex/worktrees" / worktree_root_key(cwd)
    path = folder / folder_slug(branch)
    if path.exists():
        path = folder / folder_slug(branch, True)
    return path


def folder_slug(branch, hashed=False):
    """A worktree folder's name for a branch (see new_path), with the hash when hashed."""
    slug = branch.replace("/", "-")
    if len(slug) > 100:
        slug = slug[:48] + "--" + slug[-50:]
    return slug + "-" + hashlib.sha256(branch.encode()).hexdigest()[:8] if hashed else slug


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
    return {"local": local_names, "remote": remote, "remotes": remotes, "checked_out": checked_out,
            "suggestions": suggestions, "head": head, "bases": bases}


def split_remote(text, branches):
    """For text that starts with a remote's name and a slash, as git branch -r lists branches,
    such as origin/fix/login: the remote and the rest. Else None."""
    text = text.strip()
    for remote in branches.get("remotes", ()):  # The longest first, so origin-fork/x isn't origin's.
        if text.startswith(remote + "/") and len(text) > len(remote) + 1:
            return remote, text[len(remote) + 1:]
    return None


def query_for(text, suggestion, branches):
    """What to match a suggestion's name against: for text such as origin/fix, the rest, when the
    branch is on that remote; else the text."""
    split = split_remote(text, branches)
    if split and f"{split[0]}/{suggestion['name']}" in branches["remote"].get(suggestion["name"], ()):
        return split[1]
    return text


def plan_branch(cwd, branches, text, suggestion=None, base=None):
    """Describe what creating a worktree would do: for an existing branch, or a new one named by text.
    An existing branch's action names it in full, since its row in the list may be cut short, with a
    shorter form for a small pane; a new branch's leaves its name out, which the screen shows above."""
    base = base or {"ref": "HEAD", "label": branches["head"]}
    if suggestion:
        name, ref = suggestion["name"], suggestion["ref"]
        if suggestion["kind"] == "local":
            plan = {"branch": name, "action": f"check out the existing branch {name}", "short": f"check out {name}",
                    "args": [name]}
        else:
            plan = {"branch": name, "action": f"create a local branch tracking {ref}", "short": f"track {ref}",
                    "args": ["--track", "-b", name, None, ref]}
    else:
        name = normalize_branch(text)
        if not name:
            return {"error": "Enter a branch name." if not text.strip() else "No usable branch-name characters."}
        if not util.git_ok(cwd, "check-ref-format", "--branch", name):
            return {"branch": name, "error": "Not a valid branch name."}
        existing = existing_branch(name, branches)
        if existing:
            return existing
        split = split_remote(name, branches)
        if split:
            # A local origin/x would make Git call origin/x ambiguous.
            return {"branch": name, "remote": split[0],
                    "error": f"A branch can't start with {split[0]}/, a remote's name; name it {split[1]}."}
        clash = ref_clash(name, branches["local"])
        if clash:
            return {"branch": name, "error": f"{name} can't be a branch while {clash} exists."}
        # --no-track keeps a branch started from origin/main from pushing to or tracking main.
        plan = {"branch": name, "action": f"create the branch from {base['label']}",
                "args": ["--no-track", "-b", name, None, base["ref"]]}
    path = new_path(cwd, plan["branch"])
    if path.exists():
        plan["error"] = "Destination already exists: " + util.tilde(str(path))
    plan["path"] = path
    return plan


def existing_branch(name, branches):
    """The plan for a name that's a branch already, or on a remote, such as origin/x: an error, and
    either the worktree it's checked out in ("open"), or the refs of the suggestions it means
    ("exists"). Else None."""
    split = split_remote(name, branches)
    if split and name in branches["remote"].get(split[1], ()):
        local = split[1]  # Remote branches are checked out as local ones of the same name.
    elif split:
        return None
    else:
        local = name
    if local in branches["checked_out"]:
        path = branches["checked_out"][local]
        return {"branch": local, "error": "Already checked out at " + util.tilde(path), "open": path}
    if local in branches["local"]:
        return {"branch": local, "exists": [local],
                "error": "A branch with this name exists; select it under Existing branches."}
    if split:
        return {"branch": local, "exists": [name], "error": f"{name} exists; select it under Existing branches."}
    if branches["remote"].get(name):
        return {"branch": name, "exists": branches["remote"][name],
                "error": f"{branches['remote'][name][0]} exists; select it under Existing branches."}
    return None


def ref_clash(name, local):
    """A branch that rules out the name, because one would be a folder of the other: docs/readme
    rules out docs, and docs rules out docs/readme."""
    parts = name.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in local:
            return "/".join(parts[:i])
    return next((b for b in sorted(local) if b.startswith(name + "/")), None)


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
