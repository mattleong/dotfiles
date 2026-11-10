"""Branches for new worktrees: listing them, naming new ones, and creating worktrees."""
import functools
import hashlib
import os
import re
import time
from pathlib import Path

import matching
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
    """Collect branches without worktrees: local ones committed to in the last RECENT_DAYS, then
    remote-only ones from then, then everything older, each newest first. And the bases a new
    branch can start from: the default branch, then the current one, each with a note saying
    which it is."""
    checked_out = {e["branch"].removeprefix("refs/heads/"): e["worktree"] for e in entries if "branch" in e}
    missing = {e["worktree"] for e in entries if worktrees.missing(e)}

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
    # Of branches with the same name on several remotes, the one Git's checkout picks comes first:
    # checkout.defaultRemote's, else origin's.
    preferred = util.run("git", "-C", cwd, "config", "--get", "checkout.defaultRemote", check=False)
    preferred = os.fsdecode(preferred).strip() or "origin"
    for branch, refs_of_name in remote.items():
        refs_of_name.sort(key=lambda ref: ref != f"{preferred}/{branch}")
    head = util.git_text(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    detached = head == "HEAD"
    if detached:
        head = util.git_text(cwd, "rev-parse", "--short", "HEAD")
    # The default branch comes first, so new branches start from it unless you choose otherwise.
    bases = [{"ref": "HEAD", "label": head, "note": "current commit" if detached else "current branch"}]
    default = worktrees.default_branch(cwd)
    if default and default != head:
        bases.insert(0, {"ref": default, "label": default, "note": "default branch"})
    elif default:
        bases[0]["note"] = "default and current branch"
    cutoff = (time.time() if now is None else now) - RECENT_DAYS * 86400
    available = [{"name": n, "ref": n, "kind": "local", "time": stamp} for n, stamp in local if n not in checked_out]
    remote_only = [{"name": name, "ref": ref, "kind": "remote", "time": stamp} for name, ref, stamp in remote_refs
                   if name not in local_names and name not in checked_out]
    # Each list is newest first already, so the older ones, merged, are sorted again.
    older = sorted([s for s in available + remote_only if s["time"] < cutoff], key=lambda s: -s["time"])
    suggestions = [s for s in available if s["time"] >= cutoff] + [s for s in remote_only if s["time"] >= cutoff]
    suggestions += older
    return {"local": local_names, "remote": remote, "remotes": remotes, "checked_out": checked_out,
            "missing": missing, "suggestions": suggestions, "head": head, "bases": bases,
            "preferred": preferred}


def split_remote(text, branches):
    """For text that starts with a remote's name and a slash, as git branch -r lists branches,
    such as origin/fix/login: the remote and the rest, which is empty for just origin/. Else None."""
    text = text.strip()
    for remote in branches.get("remotes", ()):  # The longest first, so origin-fork/x isn't origin's.
        if text.startswith(remote + "/"):
            return remote, text[len(remote) + 1:]
    return None


def query_for(text, suggestion, branches):
    """What to match a suggestion's name against, or None when it can't match. Text such as
    origin/fix names a remote, so it matches origin's branches by the rest, fix, and never another
    remote's, even one with the same name; origin/ alone matches all of origin's. A local branch
    matches by the rest when that remote has a branch of its name, since a remote branch is checked
    out as the local one of the same name."""
    split = split_remote(text, branches)
    if not split:
        return text
    remote, rest = split
    if suggestion["kind"] == "remote":
        return rest if suggestion["ref"] == f"{remote}/{suggestion['name']}" else None
    return rest if f"{remote}/{suggestion['name']}" in branches["remote"].get(suggestion["name"], ()) else text


def plan_branch(cwd, branches, text, suggestion=None, base=None):
    """Describe what creating a worktree would do: for an existing branch, or a new one named by
    text, started from base. What it does to the branch is a verb, such as "Checks out", and the
    rest of the sentence ("action"), which names the branch in full, since its row may be cut
    short, with a shorter form ("short") for a small pane where that helps. Text that can't be a
    new branch gets an "error" saying why (see existing_branch for a name that's taken)."""
    base = base or {"ref": "HEAD", "label": branches["head"]}
    if suggestion:
        name, ref = suggestion["name"], suggestion["ref"]
        if suggestion["kind"] == "local":
            plan = {"branch": name, "verb": "Checks out", "action": name, "args": [name]}
        else:
            plan = {"branch": name, "verb": "Creates", "action": f"{name} tracking {ref}",
                    "short": f"a branch tracking {ref}", "args": ["--track", "-b", name, None, ref]}
    else:
        name = normalize_branch(text)
        if not name:
            return {"error": "Enter a branch name." if not text.strip() else "No usable branch-name characters."}
        if not util.git_ok(cwd, "check-ref-format", "--branch", name):
            return {"branch": name, "error": "Not a valid branch name."}
        existing = existing_branch(name, branches)
        if existing:
            split = split_remote(name, branches)
            if split and existing["branch"] in branches["local"]:
                tracks = other_upstream(cwd, existing["branch"], name, branches)
                if tracks is not None:
                    existing["tracks"] = (tracks, split[0])
            return existing
        if name in branches.get("remotes", ()):
            # Most likely on the way to origin/name, so a hint ("hint") rather than an error.
            listed = text.strip().endswith("/")
            return {"branch": name, "remote": name, "hint": True,
                    "error": f"{name} is a remote; " + ("its branches are below." if listed
                                                         else f"type {name}/ to list its branches.")}
        split = split_remote(name, branches)
        if split:
            # A local origin/x would make Git call origin/x ambiguous.
            return {"branch": name, "remote": split[0],
                    "error": f"A branch can't start with {split[0]}/, a remote's name; name it {split[1]}."}
        end = text.strip()[-1:]
        if end in ("/", "."):
            # Half typed, such as fix/ on the way to fix/login. Git can't end a branch with either,
            # and leaving it out would make a branch, fix, that wasn't meant; so a hint, as above.
            return {"branch": name, "hint": True,
                    "error": "Type the rest of the name; a branch can't end with " + (
                        "a slash." if end == "/" else "a dot.")}
        clash = ref_clash(name, branches["local"])
        if clash:
            return {"branch": name, "error": f"{name} can't be a branch while {clash} exists."}
        plan = from_base({"branch": name}, base)
    path = new_path(cwd, plan["branch"])
    if path.exists():
        plan["error"] = "Destination already exists: " + util.tilde(str(path))
    plan["path"] = path
    return plan


def from_base(plan, base):
    """A new branch's plan (see plan_branch) started from base instead, without asking Git again
    whether its name can be a branch. A plan with an error stays as it is."""
    if plan.get("error"):
        return plan
    name = plan["branch"]
    # --no-track keeps a branch started from origin/main from pushing to or tracking main.
    return {**plan, "verb": "Creates", "action": f"{name} from {base['label']}",
            "short": f"a branch from {base['label']}", "args": ["--no-track", "-b", name, None, base["ref"]]}


def other_upstream(cwd, local, ref, branches):
    """For text such as fork/x, which means the local branch x, when x doesn't track ref and is at
    another commit: what x tracks, such as origin (a remote's branch of its name, by the remote's
    name), or "" for nothing. Else None."""
    try:
        upstream = util.git_text(cwd, "for-each-ref", "--format=%(upstream:short)", "refs/heads/" + local)
        if upstream == ref:
            return None
        shas = util.git_text(cwd, "rev-parse", "refs/heads/" + local, "refs/remotes/" + ref).split()
    except RuntimeError:
        return None
    if len(set(shas)) < 2:
        return None
    split = split_remote(upstream, branches)
    return split[0] if split and split[1] == local else upstream


def existing_branch(name, branches):
    """The plan for a name that's a branch already, or on a remote, such as origin/x: an error, and
    either the worktree it's checked out in ("open", or "missing" when its folder is gone), or the
    refs of the suggestions it means ("exists"). Else None."""
    split = split_remote(name, branches)
    if split and name in branches["remote"].get(split[1], ()):
        local = split[1]  # Remote branches are checked out as local ones of the same name.
    elif split:
        return None
    else:
        local = name
    if local in branches["checked_out"]:
        path = branches["checked_out"][local]
        if path in branches.get("missing", ()):
            return {"branch": local, "missing": path,
                    "error": "It's checked out in a worktree whose folder is missing; prune it with dd in the list."}
        return {"branch": local, "error": "Already checked out at " + util.tilde(path), "open": path,
                "short": "its worktree"}
    if local in branches["local"]:
        return {"branch": local, "exists": [local],
                "error": "A branch with this name exists; select it under Existing branches."}
    if split:
        return {"branch": local, "exists": [name], "error": f"{name} exists; select it under Existing branches."}
    if branches["remote"].get(name):
        return {"branch": name, "exists": branches["remote"][name],
                "error": f"{branches['remote'][name][0]} exists; select it under Existing branches."}
    # Typed with a space or other capitals, such as fix a or FIX/A for fix/a, a checked-out
    # branch's name still means it, unless a listed branch's name is just as close.
    loose = [b for b in branches["checked_out"] if matching.exact(name, b)]
    others = (set(branches["local"]) | set(branches["remote"])) - set(branches["checked_out"])
    listed = any(matching.exact(name, b) for b in others)
    if len(loose) == 1 and not listed:
        return existing_branch(loose[0], branches)
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
    """Run the plan's git worktree add. Raises RuntimeError when it fails."""
    path = plan["path"]
    # Checked again, since the plan was made: Git would create a new branch before finding the
    # folder taken, and leave the branch behind.
    if path.exists():
        raise RuntimeError("Destination already exists: " + util.tilde(str(path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    args = plan["args"]
    if None in args:  # -b/--track forms put the path before the start point.
        args = [str(path) if a is None else a for a in args]
    else:
        args = [str(path), *args]
    util.git(cwd, "worktree", "add", *args)


def remote_of(item):
    """The remote a remote branch's suggestion is on, such as origin."""
    return item["ref"][:-len(item["name"]) - 1]


def named_remotes(suggestions):
    """Whether more than one remote has branches listed, so each is labeled by its name."""
    return len({remote_of(s) for s in suggestions if s["kind"] == "remote"}) > 1


def source_label(item, named=False):
    """'local', 'remote' for origin, or the remote's name for any other remote, or for every
    remote when named (see named_remotes)."""
    if item["kind"] != "remote":
        return "local"
    remote = remote_of(item)
    return "remote" if remote == "origin" and not named else remote
