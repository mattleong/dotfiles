"""Pull request status from the gh command, with a cache so it shows at once."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import branching
import util


def cache_path(repo, name):
    root = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(root) / "rex" / name / (branching.worktree_root_key(repo) + ".json")


def load_cached_prs(repo):
    try:
        with open(cache_path(repo, "worktree-prs")) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RuntimeError):
        return {}


def save_cached_prs(repo, prs):
    try:
        path = cache_path(repo, "worktree-prs")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(prs))
        temporary.replace(path)
    except (OSError, RuntimeError):
        pass


def open_pull_request(repo, number):
    util.run("gh", "pr", "view", str(number), "--web", cwd=repo)


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
            out = util.run("gh", "pr", "list", "--state", "all", "--limit", "200", "--json", fields, *extra, cwd=repo)
            prs = json.loads(out)
        except (RuntimeError, OSError, ValueError):
            continue
        for pr in prs:
            branch = pr.get("headRefName")
            if branch and (branch not in found or pr["number"] > found[branch]["number"]):
                found[branch] = pr
    return found
