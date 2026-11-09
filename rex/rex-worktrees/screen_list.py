"""The worktree list: browsing, opening, and the commit preview."""
import curses
import subprocess

import actions
import github
import keyboard
import matching
import screen_remove
import tabs
import ui
import util
import worktrees


PREVIEW_ROWS, PREVIEW_MIN = 9, 4  # The commit preview's rows, including the blank row above it.
PREVIEW_COMMITS = 40  # The most commits a preview lists, in a tall pane.


def list_layout(room, count):
    """Split room rows between the list and the preview below it. The list gets the rows it needs
    while the preview keeps about half, up to PREVIEW_ROWS; then the preview gets every row left.
    It's left out when fewer than PREVIEW_MIN rows would be left for it, or when the list would
    show fewer than 3 rows."""
    count = max(1, count)
    preview = min(PREVIEW_ROWS, max(PREVIEW_MIN, room // 2))
    rows = min(count, room - preview)
    if room - rows < PREVIEW_MIN or rows < min(count, 3):
        return max(1, room), 0
    return rows, room - rows


MISSING = "This worktree's folder is missing; press p to prune it."
NONE_SELECTED = "No worktree selected."
# Keys that act on the selected worktree, by the names the list's Keys give them.
ROW_KEYS = {"s": "s", "o": "o", "e": "e", "y": "y", "Y": "Y", "d": "dd"}


def commit_rows(commits, space):
    """A row per commit: its hash, age, and subject, with its author when there's room for them and
    a readable subject. The subject stands out; the rest is metadata, dim."""
    dim = ui.style("dim")
    author_width = min(16, max([len(c[2]) for c in commits] + [6])) if space >= 72 else 0
    return [[(sha, dim), ("  " + ui.short_age(stamp).rjust(3), dim)]
            + ([("  " + author[:author_width].ljust(author_width), dim)] if author_width else [])
            + [("  " + subject, 0)] for sha, subject, author, stamp in commits]


class ListScreen:
    def list_screen(self):
        stdscr = self.stdscr
        query, qpos, filtering, pending = "", 0, False, None
        selected = next((e for e in self.entries if e.get("current")), None)
        top = 0
        while True:
            loading = self.prs_loading or self.details_loading or any(
                util.PENDING in (e["dirty"], e["open"]) for e in self.entries)
            keyboard.set_timeout(stdscr, self.flash.wait(150 if loading else -1))
            shown = matching.filter_entries(matching.sort_entries(self.entries, self.sort), query)
            if selected not in shown:
                selected = shown[0] if shown else None
            height, _ = stdscr.getmaxyx()
            space = ui.space(stdscr)
            widths = ui.column_widths(self.entries, space)
            index = shown.index(selected) if selected in shown else 0

            # The status line: a message, or the second key a half-typed dd, gg, or gp needs.
            note = ""
            if pending == "d" and selected:
                note = "d… press d again to remove " + selected["name"]
            elif pending == "g":
                note = "g… then g for the first row, or p to open the pull request"
            status = self.flash.rows(space) or [[(note, ui.style("dim"))] if note else []]
            # With nothing matching, enter (while typing) or n starts a new worktree for the filter.
            missing = query.strip() if not shown else ""
            # The main checkout alone: a first run, so a line under it says how to make a worktree.
            first_run = len(self.entries) == 1 and bool(shown)
            keys = self.list_keys(selected, shown, query, filtering, missing)
            footer = keys.row()
            heading = [ui.header("Worktrees", self.label, self.summary(),
                                 "" if self.sort == matching.SORTS[0] else f"sorted by {self.sort}"), [], []]
            bottom = status + [footer]
            room = height - ui.margin(height) - len(heading) - 1 - len(bottom)  # Below the column headings.
            # Sized to every worktree, so the preview stays put while filtering.
            capacity, preview_rows = list_layout(room, len(self.entries) + first_run)
            top = ui.scroll_top(top, index, len(shown), capacity)
            # Beside the filter: the selected row's place when the list scrolls, and how many match.
            marks = ([f"{index + 1}/{len(shown)}"] if len(shown) > capacity else []) + (
                [f"{len(shown)} of {len(self.entries)}"] if query.strip() else [])
            heading[1], column = ui.search_row(query, qpos, filtering, space, mark=" · ".join(marks))

            if not keyboard.pending_input(stdscr):
                heading.append(ui.header_row(widths) if shown else [])  # No titles over an empty list.
                body = [ui.entry_row(e, e is selected, widths, matching.match_entry(e, query) if query.strip() else None)
                        for e in shown[top:top + capacity]]
                if first_run:
                    body.append([("    No other worktrees yet: ", ui.style("dim")), ("n", ui.style("bold")),
                                 (" creates one", ui.style("dim"))])
                if not shown:
                    body.append([("    No matching worktrees", ui.style("dim"))])
                    if missing:
                        body.append([("    " + ("enter" if filtering else "n"), ui.style("bold")),
                                     (f" new worktree for “{missing}”", ui.style("dim"))])
                if preview_rows:  # Right below the list's rows.
                    body += [[]] * (capacity - len(body))
                    body += self.preview(selected, space, widths["compact"], preview_rows,
                                         said=self.flash.left() is not None and self.flash.text == MISSING)
                cursor = (ui.margin(height) + 1, column) if column is not None else None
                ui.render(stdscr, ui.compose(height, heading, body, bottom), cursor=cursor)

            key = keyboard.read_key(stdscr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            self.flash.clear()
            if filtering:
                if key in keyboard.ENTER:
                    filtering = False  # And open the selected worktree, below.
                elif key == keyboard.ESCAPE:
                    filtering = False  # Keeping the filter, so the list's keys act on what it found.
                    continue
                elif key in keyboard.BACKSPACE and not query:
                    filtering = False
                    continue
                elif key in keyboard.TYPING_MOVES:
                    new_index = keyboard.typing_motion(key, index, len(shown), capacity)
                    if new_index is not None:
                        selected = shown[new_index]
                    continue
                else:
                    new_query, qpos = keyboard.edit_text(query, qpos, key)
                    if new_query != query:
                        query, selected = new_query, None  # The best match, so enter opens it.
                    continue
            # Where the selection goes after an action: the same worktree, or the next one if it's gone.
            paths = [e["worktree"] for e in shown]
            if pending and key not in (pending, "p" if pending == "g" else pending):
                pending = None  # An unfinished dd, gg, or gp: the next key only cancels it.
                continue
            if selected is None and (key in ROW_KEYS or (pending == "g" and key == "p")):
                pending = None
                keys.refused(ROW_KEYS.get(key, "gp"), self.refuse)  # No worktree selected.
                continue
            if key == "d":
                if pending == "d":
                    pending = None
                    if keys.refused("dd", self.refuse):
                        continue
                    if self.remove(selected):
                        return
                    selected = self.reselect(paths, index)
                    if self.emptied(shown, query):
                        query, qpos = "", 0
                else:
                    pending = "d"
                continue
            if pending == "g" and key == "p":
                pending = None
                self.open_pr(selected)
                continue
            new_index, pending = keyboard.motion(key, index, len(shown), capacity, pending)
            if new_index is not None:
                selected = shown[new_index]
            elif key == "/":
                filtering, qpos = True, len(query)
            elif key == "?":
                self.list_help()
            elif key == keyboard.ESCAPE and query:
                query, qpos = "", 0
            elif key in keyboard.CLOSE:
                return
            elif key == "n":
                if self.new_screen(text=missing):
                    return
                keyboard.set_timeout(stdscr, -1)
            elif key == "p":
                self.prune()
                selected = self.reselect(paths, index)
                if self.emptied(shown, query):
                    query, qpos = "", 0
            elif key == "c":
                if self.cleanup():
                    return
                selected = self.reselect(paths, index)
                if self.emptied(shown, query):
                    query, qpos = "", 0
                if self.reveal in self.entries:  # The worktree cleanup's message is about.
                    if self.reveal not in matching.filter_entries(self.entries, query):
                        query, qpos = "", 0
                    selected = self.reveal
                self.reveal = None
            elif key == "S":
                self.sort = matching.SORTS[(matching.SORTS.index(self.sort) + 1) % len(matching.SORTS)]
                util.save_settings(sort=self.sort)  # Remembered for the next time the picker opens.
                self.say(f"Sorted by {self.sort}.", "dim")
            elif key in ("y", "Y") and selected:
                value = selected["worktree"] if key == "y" else selected["name"]
                try:
                    actions.copy_to_clipboard(value)
                    self.say("Copied " + ("path" if key == "y" else "branch") + ": " + value, "live")
                except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
                    self.say(str(error))
            elif key == "e" and selected:
                if keys.refused("e", self.refuse):
                    continue
                try:
                    entry = selected
                    self.busy("Opening editor…", lambda: tabs.open_editor(self.session, entry, self.client))
                    return
                except (RuntimeError, OSError) as error:
                    self.say(str(error))
            elif key == "r":
                path = selected["worktree"] if selected else None
                self.reload()
                if self.show_prs:
                    self.load_prs()
                selected = next((e for e in self.entries if e["worktree"] == path), None)
                if self.emptied(shown, query):
                    query, qpos = "", 0
                self.say("Refreshed.", "dim")
            elif key in ("s", "o") and selected:
                if keys.refused(key, self.refuse):
                    continue
                try:
                    if key == "s":
                        self.busy("Opening split…", lambda: tabs.open_split(self.session, selected, self.origin))
                        return
                    entry = selected
                    opened = self.busy("Opening tab…", lambda: tabs.open_tab(self.session, entry, focus=False,
                                                                       terminals=self.terminals))
                    entry["open"] = True
                    self.terminals = None  # A tab was added; scan again next time.
                    self.say(f"Opened {tabs.tab_label(entry)} in a background tab in the {self.label} session."
                             if opened else f"{entry['name']} already has a tab open.", "live" if opened else "dim")
                except (RuntimeError, OSError) as error:
                    self.say(str(error))
            elif key in keyboard.ENTER:
                if selected is None:
                    if missing and self.new_screen(text=missing):
                        return
                    keyboard.set_timeout(stdscr, -1)
                    continue
                if keys.refused("enter", self.refuse):
                    continue
                try:
                    entry = selected
                    self.busy("Opening…", lambda: tabs.open_tab(self.session, entry, self.client,
                                                          terminals=self.terminals))
                    return
                except (RuntimeError, OSError) as error:
                    self.say(str(error))
            elif isinstance(key, str) and key.isprintable() and not key.isspace():
                self.say("Press / to filter; ? lists every key.", "dim")

    def list_keys(self, selected, shown, query, filtering, missing):
        """The list's keys (see ui.Keys), and whether each can act on the selected worktree now.
        While typing in the filter, only the keys that can't be text. missing is the filter's
        text when nothing matches it, so enter and n start a new worktree from it."""
        keys = ui.Keys()
        row = NONE_SELECTED if selected is None else MISSING if worktrees.missing(selected) else ""
        # With nothing selected, enter starts a new worktree for the filter that matched nothing.
        enter = row if selected else ""
        if filtering:
            keys.add("enter", "new" if missing else "open", why=enter)
            keys.add("↑/↓", "move", rank=2, can=bool(shown))
            keys.add("esc", "done", rank=1)  # Stops typing and keeps the filter.
            return keys
        keys.add("j/k", "move", rank=8, can=bool(shown))
        keys.add("/", "filter")
        # With nothing matching, n, which the list names, starts the new worktree; enter does too.
        keys.add("enter", "" if missing else "open", why=enter)
        keys.add("s", "split", rank=3, why=row)
        keys.add("n", "new", rank=4)
        keys.add("dd", "remove", rank=5, why=(worktrees.removal_problem(selected) or "") if selected else row)
        # c and p are refused by the same tests cleanup and prune use to say they have nothing to do.
        finished = len(screen_remove.cleanable(self.entries))
        keys.add("c", f"clean up {finished}", rank=6, can=bool(finished))
        stale = len(screen_remove.prunable(self.entries))
        keys.add("p", f"prune {stale}", rank=7, can=bool(stale))
        keys.add("?", "help")
        keys.add("esc", "clear filter", rank=2, can=bool(query))
        keys.add("q", "quit", rank=1)
        for key in ("o", "e"):  # Keys only help lists.
            keys.add(key, why=row)
        for key in ("y", "Y", "gp"):
            keys.add(key, why="" if selected else row)
        return keys

    def emptied(self, shown, query):
        """Whether an action took away every worktree the filter showed, such as by removing the
        only one it matched, so the filter clears rather than leaving an empty list."""
        return bool(shown and query.strip()) and not matching.filter_entries(self.entries, query)

    def reselect(self, paths, index):
        """The worktree at paths[index] after an action, or when it's gone, the next one still
        listed, else the one before it."""
        by_path = {e["worktree"]: e for e in self.entries}
        return next((by_path[p] for p in paths[index:] + paths[:index][::-1] if p in by_path), None)

    def list_help(self):
        ui.show_help(self.stdscr, "Worktrees", [("Move", ui.NAVIGATION_HELP), ("Actions", [
            ("enter", "open the worktree's tab"),
            ("s", "open the worktree in a split beside your pane"),
            ("o", "open its tab in the background and stay here"),
            ("n", "new worktree; when nothing matches the filter, starting from its text"),
            ("dd", "remove the worktree, and close the panes inside it"),
            ("c", "clean up worktrees whose pull request merged or whose remote branch is gone"),
            ("p", "prune missing worktrees"),
            ("e", "open the worktree in your editor, in a new tab"),
            ("gp", "open its pull request in the browser"),
            ("y / Y", "copy its path / branch name"),
            ("S", "sort: most recent, by state, or by name, with the main checkout first; "
                  "remembered for next time"),
            ("r", "refresh"),
            ("q", "quit"),
        ]), ("Filter", [
            ("/", "start typing a filter; while typing, every letter goes into it"),
            ("matching", "a word matches a branch that contains it, or that has its letters in order, "
                         "each right after the one before or at the start of a word: flf finds "
                         "feat/login-flow; a word no branch matches can match the worktree's path"),
            ("enter", "open the selected worktree; when nothing matches, start a new worktree from the text"),
            ("↑ / ↓", "move while typing; also ctrl+p / ctrl+n"),
            ("home / end", "the first / last row while typing"),
            ("esc", "stop typing and keep the filter, to use the keys above; then clear the filter; "
                    "with no filter, quit"),
        ]), ("Labels", [
            (("●", ui.style("live")), "the worktree you opened this from"),
            ("3d", "time since the worktree's last commit"),
            (ui.pr_cell({"pr": {"number": 12, "state": "OPEN"}})[0],
             "its pull request: open, draft, merged, or closed"),
            (util.ICONS["ahead"] + "2 " + util.ICONS["behind"] + "3", "commits not pushed yet / not pulled yet"),
            (ui.label("deleted"), "its remote branch was deleted, often after merging"),
            (ui.label("main"), "the main checkout, the repository's own folder; it can't be removed"),
            (ui.label("tab"), "a Rex pane is open in the worktree"),
            (ui.label("dirty"), "the worktree has uncommitted changes"),
            (ui.label("locked"), "the worktree is locked (git worktree lock), so it can't be removed"),
            (ui.label("missing"), "the worktree's folder is gone; press p to prune it"),
            (("herdr, t3, claude, rex", ui.style("source")), "before the folder: the tool that created the worktree"),
            ("narrow panes", "only the labels' icons show, and the preview spells out the selected worktree's"),
        ])])

    def open_pr(self, entry, flash=None):
        """Open the pull request for a worktree or branch in the browser."""
        flash = flash or self.flash
        pr = (entry or {}).get("pr")
        if not pr:
            flash.set("No pull request found for this branch." if self.show_prs
                      else "Pull requests need the gh command.", "dim")
            return
        try:
            self.busy(f"Opening #{pr['number']}…", lambda: github.open_pull_request(self.repo, pr["number"]))
            flash.set(f"Opened #{pr['number']} in the browser.", "live")
        except (RuntimeError, OSError) as error:
            flash.set(str(error))

    def summary(self):
        """How many worktrees, and how many have each problem, in the labels' words, such as
        "11 worktrees · 2 with changes · 1 gone · 1 missing"."""
        counts = [(sum(e.get("dirty") is True for e in self.entries), "with " + ui.word("dirty")),
                  (sum(worktrees.REMOTE_DELETED in (e.get("track") or []) for e in self.entries), ui.word("deleted")),
                  (sum(worktrees.missing(e) for e in self.entries), ui.word("missing"))]
        parts = [f"{len(self.entries)} worktree{'s' if len(self.entries) != 1 else ''}"]
        parts += [f"{n} {words}" for n, words in counts if n]
        return " · ".join(parts)

    def preview(self, entry, space, compact, size, said=False):
        """The selected worktree in size rows: a blank row, its full branch, wrapped, then its path
        and as many of its commits as fit. The branch comes first so long ones that look alike in
        the list can be told apart. Compact rows leave out the labels' words, so the preview gives
        them in full, wrapped between labels. A missing folder is said, wrapped, unless the status
        line said it (said)."""
        if entry is None or size < 2:
            return []
        bold, dim = ui.style("bold"), ui.style("dim")
        name, path = entry["name"], util.tilde(entry["worktree"])
        rows = [[]]
        if len(name) + 2 + len(path) <= space:
            rows.append([(name, bold), ("  " + path, dim)])
            path_rows = []
        else:
            rows += [[(line, bold)] for line in ui.wrap_path(name, space)]
            path_rows = [[(line, dim)] for line in ui.wrap_path(path, space)]
        if compact:
            labels = [c for c in ui.badges(entry) if c[0] != ui.label("missing")[0]]  # Said below.
            rows += ui.joined_rows(labels, space)
        if path_rows:
            # Wrapped when there's still room for a commit below it, else on one row.
            if len(rows) + len(path_rows) + 2 <= size or len(path_rows) == 1:
                rows += path_rows
            else:
                rows.append([(ui.middle(path, space)[0], dim)])
        if worktrees.missing(entry):
            if not said and len(rows) < size:
                note = ui.clip(ui.hang(util.ICONS["missing"], MISSING, space), size - len(rows), space)
                rows += [[(line, ui.style("danger"))] for line in note]
            return rows[:size]
        if len(rows) >= size:
            return rows[:size]
        key = (entry["worktree"], self.default)
        if key not in self.commits:
            self.commits[key] = worktrees.recent_commits(entry["worktree"], PREVIEW_COMMITS, self.default)
        description, commits = self.commits[key]
        if len(rows) + 2 > size:
            return rows + commit_rows(commits[:1], space)  # Room for a commit, but not its heading.
        # "Recent commits", or "Commits" with how many aren't in the default branch yet.
        if description and description != "Recent commits":
            rows.append(ui.section("Commits", description))
        else:
            rows.append(ui.section("Recent commits"))
        if not commits:
            rows.append([("No commits", dim)])
        return (rows + commit_rows(commits[:max(0, size - len(rows))], space))[:size]
