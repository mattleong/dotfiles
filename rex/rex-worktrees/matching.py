"""Filtering and sorting the worktree and branch lists."""
import os

import util
import worktrees


# How the list can be ordered, in the order S cycles through them; the first is the default.
SORTS = ["recent", "state", "name"]


def sort_entries(entries, mode):
    """Order worktrees with the main checkout first, then the rest by their latest commit, newest
    first; by state: uncommitted changes, open pull requests, the rest, finished ones, then
    missing and broken ones, each newest first; or by branch name. An unknown mode means the
    default."""
    def recent(entry):
        return -(entry.get("time") or 0)

    def group(entry):
        pr = (entry.get("pr") or {}).get("state")
        if entry.get("main"):
            return 0
        if worktrees.missing(entry) or worktrees.broken(entry):
            return 5
        if entry.get("dirty") is True:
            return 1
        if pr == "OPEN":
            return 2
        if worktrees.cleanup_reason(entry):
            return 4
        return 3

    if mode == "state":
        key = lambda e: (group(e), recent(e))
    elif mode == "name":
        key = lambda e: (not e.get("main"), e["name"].lower(), e["name"])
    else:
        key = lambda e: (not e.get("main"), recent(e))
    return sorted(entries, key=key)


SEPARATORS = "/-_. "


def word_starts(text):
    """Whether each position in text starts a word: the first letter, one after a separator, or a
    capital after a lowercase letter, as in fixLogin."""
    return [p == 0 or text[p - 1] in SEPARATORS or (text[p].isupper() and text[p - 1].islower())
            for p in range(len(text))]


def fuzzy(token, text):
    """Match token in text: as a substring, or with each letter either right after the one before
    it or at the start of a word (see word_starts), so flf finds feat/login-flow but dark doesn't
    find dependabot/npm_and_yarn/webpack. Of every way it matches, the best scores: each letter
    scores 1, plus 3 when it follows the letter before it and 2 at the start of a word, and a match
    with no gaps scores 2 more. So "old" finds the word in chore/old-thing rather than letters
    inside other words. Returns (score, positions) or None."""
    lower, size = text.lower(), len(text)
    if not token:
        return 0, []
    starts = word_starts(text)
    gains = [1 + 2 * start for start in starts]
    # best[i][p]: the best (score, previous position) with token[i] matched at position p. A
    # letter can come after a gap only at the start of a word, and so can the first.
    previous = [(gains[p], -1) if lower[p] == token[0] and starts[p] else None for p in range(size)]
    best = [previous]
    for char in token[1:]:
        row, before, at = [None] * size, None, -1  # The best match of the letters so far, ending before p - 1.
        for p in range(size):
            if p >= 2 and previous[p - 2] and (before is None or previous[p - 2][0] > before):
                before, at = previous[p - 2][0], p - 2
            if lower[p] != char:
                continue
            if before is not None and starts[p]:
                row[p] = (before + gains[p], at)
            if p and previous[p - 1] and (row[p] is None or previous[p - 1][0] + gains[p] + 3 >= row[p][0]):
                row[p] = (previous[p - 1][0] + gains[p] + 3, p - 1)
        best.append(row)
        previous = row
    found = None
    ends = [p for p in range(size) if previous[p]]
    if ends:
        pos = max(ends, key=lambda p: previous[p][0])
        score, positions = previous[pos][0], []
        for row in reversed(best):
            positions.append(pos)
            pos = row[pos][1]
        positions.reverse()
        if positions[-1] - positions[0] == len(positions) - 1 > 0:
            score += 2
        found = score, positions
    # A substring that starts inside a word, such as ogin in login, matches too.
    at = lower.find(token)
    while at >= 0:
        span = range(at, at + len(token))
        score = sum(gains[p] for p in span) + 3 * (len(token) - 1) + (2 if len(token) > 1 else 0)
        if found is None or score > found[0]:
            found = score, list(span)
        at = lower.find(token, at + 1)
    return found


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
