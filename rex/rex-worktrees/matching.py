"""Filtering and sorting the worktree and branch lists."""
import os

import util
import worktrees


SORTS = ["git", "recent", "state"]


def sort_entries(entries, mode):
    """Order worktrees by Git's order, most recent commit first, or grouped by state: the main
    checkout, uncommitted changes, open pull requests, the rest, finished ones, then missing ones."""
    if mode == "recent":
        return sorted(entries, key=lambda e: -(e.get("time") or 0))

    def group(entry):
        pr = (entry.get("pr") or {}).get("state")
        if entry.get("main"):
            return 0
        if worktrees.missing(entry):
            return 5
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


SEPARATORS = "/-_. "


def fuzzy(token, text):
    """Match token as a subsequence of text, scoring the best of every way it matches: each
    letter scores 1, plus 3 when it follows the letter before it and 2 at the start of a word, and
    a match with no gaps scores 2 more. So "old" finds the word in chore/old-thing rather than the
    o in chore. Returns (score, positions) or None."""
    lower, size = text.lower(), len(text)
    if not token:
        return 0, []
    starts = [p == 0 or text[p - 1] in SEPARATORS for p in range(size)]
    # best[i][p]: the best (score, previous position) with token[i] matched at position p.
    previous = [(1 + 2 * starts[p], -1) if lower[p] == token[0] else None for p in range(size)]
    best = [previous]
    for char in token[1:]:
        row, before, at = [None] * size, None, -1  # The best match of the letters so far, ending before p - 1.
        for p in range(size):
            if p >= 2 and previous[p - 2] and (before is None or previous[p - 2][0] > before):
                before, at = previous[p - 2][0], p - 2
            if lower[p] != char:
                continue
            gain = 1 + 2 * starts[p]
            if before is not None:
                row[p] = (before + gain, at)
            if p and previous[p - 1] and (row[p] is None or previous[p - 1][0] + gain + 3 >= row[p][0]):
                row[p] = (previous[p - 1][0] + gain + 3, p - 1)
        best.append(row)
        previous = row
    ends = [p for p in range(size) if previous[p]]
    if not ends:
        return None
    pos = max(ends, key=lambda p: previous[p][0])
    score, positions = previous[pos][0], []
    for row in reversed(best):
        positions.append(pos)
        pos = row[pos][1]
    positions.reverse()
    if positions[-1] - positions[0] == len(positions) - 1 > 0:
        score += 2
    return score, positions


def plain(text):
    """A name with slashes, dashes, underscores, and spaces the same, for comparing names."""
    return "".join("-" if c in "/_ " else c for c in text.strip().lower())


def exact(typed, name):
    """Whether typed is the branch's whole name (see plain), not just the start of a longer one."""
    return bool(typed.strip()) and plain(typed) == plain(name)


def strong(typed, name):
    """Whether a typed name surely means an existing branch: it's the branch's name, or it
    appears unbroken in it from the start of a word (so main doesn't mean domain), at least two
    characters long. Slashes, dashes, underscores, and spaces count as the same."""
    typed, name = plain(typed), plain(name)
    if not typed:
        return False
    if typed == name:
        return True
    if len(typed) < 2:
        return False
    found = name.find(typed)
    while found >= 0:
        if found == 0 or name[found - 1] in SEPARATORS:
            return True
        found = name.find(typed, found + 1)
    return False


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
