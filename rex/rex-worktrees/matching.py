"""Filtering and sorting the worktree and branch lists."""
import os

import util
import worktrees


SORTS = ["git", "recent", "state"]


def sort_entries(entries, mode):
    """Order worktrees by Git's order, most recent commit first, or grouped by state."""
    if mode == "recent":
        return sorted(entries, key=lambda e: -(e.get("time") or 0))

    def group(entry):
        pr = (entry.get("pr") or {}).get("state")
        if entry.get("main"):
            return 0
        if entry.get("dirty") is True:
            return 1
        if pr == "OPEN":
            return 2
        if worktrees.cleanup_reason(entry):
            return 4
        return 3

    if mode == "state":
        return sorted(entries, key=group)
    return list(entries)


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
        if token not in util.tilde(entry["worktree"]).lower():
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


def fuzzy_all(query, text):
    """Score text against every token in query; 0 means no match. An empty query matches everything."""
    total = 1
    for token in query.lower().split():
        match = fuzzy(token, text)
        if not match:
            return 0
        total += match[0]
    return total
