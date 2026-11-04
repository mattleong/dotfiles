"""The worktree list: browsing, opening, and the commit preview."""
import curses
import subprocess

import actions
import github
import keyboard
import matching
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

            # The status line: a message, the second key a half-typed dd, gg, or gp needs, or matches.
            if pending == "d" and selected:
                note = "d… press d again to remove " + selected["name"]
            elif pending == "g":
                note = "g… then g for the first row, or p to open the pull request"
            else:
                note = f"{len(shown)} of {len(self.entries)} match" if query.strip() else ""
            status = self.flash.rows(space) or [[(note, ui.style("dim"))] if note else []]
            # With nothing matching, enter (while typing) or n starts a new worktree for the filter.
            missing = query.strip() if not shown else ""
            if filtering:
                footer = ui.typing_footer("new" if missing else "open")
            else:
                finished = sum(1 for e in self.entries if worktrees.cleanup_reason(e))
                stale = sum(1 for e in self.entries if "prunable" in e)
                footer = ui.footer(enter="new" if missing else "open", move=True, slash="filter", back="quit", clear_filter=bool(query),
                                   actions=[("s", "split"), ("n", "new"), ("dd", "remove")]
                                   + ([("c", f"clean up ({finished})")] if finished else [])
                                   + ([("p", f"prune ({stale})")] if stale else []))
            search, column = ui.search_row(query, qpos, filtering, space)
            heading = [ui.header("Worktrees", self.label, self.summary(),
                                 "" if self.sort == "git" else f"sorted by {self.sort}"), search, []]
            bottom = status + [footer]
            room = height - ui.margin(height) - len(heading) - 1 - len(bottom)  # Below the column headings.
            # Sized to every worktree, so the preview stays put while filtering.
            capacity, preview_rows = list_layout(room, len(self.entries))
            top = ui.scroll_top(top, index, len(shown), capacity)

            if not keyboard.pending_input(stdscr):
                heading[1] = ui.with_position(search, index, len(shown), capacity, space)
                heading.append(ui.header_row(widths) if shown else [])  # No titles over an empty list.
                body = [ui.entry_row(e, e is selected, widths, matching.match_entry(e, query) if query.strip() else None)
                        for e in shown[top:top + capacity]]
                if not shown:
                    body.append([("    No matching worktrees", ui.style("dim"))])
                    if missing:
                        body.append([("    " + ("enter" if filtering else "n"), ui.style("bold")),
                                     (f" new worktree for “{missing}”", ui.style("dim"))])
                if preview_rows:  # Right below the list's rows.
                    body += [[]] * (capacity - len(body))
                    body += self.preview(selected, space, widths["compact"], preview_rows)
                cursor = (ui.margin(height) + 1, column) if column is not None else None
                ui.render(stdscr, ui.compose(height, heading, body, bottom), cursor=cursor)

            key = keyboard.read_key(stdscr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            self.flash.clear()
            if filtering:
                if key in keyboard.ENTER:
                    filtering = False  # And open the selected worktree, below.
                elif key == "\t":
                    filtering = False
                    continue
                elif key == keyboard.ESCAPE:
                    filtering, query, qpos = False, "", 0
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
            if key == "d":
                if pending == "d":
                    pending = None
                    if selected and self.remove(selected):
                        return
                    selected = self.reselect(paths, index)
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
            elif key == "c":
                if self.cleanup():
                    return
                selected = self.reselect(paths, index)
            elif key == "S":
                self.sort = matching.SORTS[(matching.SORTS.index(self.sort) + 1) % len(matching.SORTS)]
                self.say(f"Sorted by {self.sort}" + (" order" if self.sort == "git" else "") + ".", "dim")
            elif key in ("y", "Y") and selected:
                value = selected["worktree"] if key == "y" else selected["name"]
                try:
                    actions.copy_to_clipboard(value)
                    self.say("Copied " + ("path" if key == "y" else "branch") + ": " + value, "green")
                except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
                    self.say(str(error))
            elif key == "e" and selected:
                if worktrees.missing(selected):
                    self.say(MISSING, "yellow")
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
                self.say("Refreshed.", "dim")
            elif key in ("s", "o") and selected:
                if worktrees.missing(selected):
                    self.say(MISSING, "yellow")
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
                             if opened else f"{entry['name']} already has a tab open.", "green" if opened else "dim")
                except (RuntimeError, OSError) as error:
                    self.say(str(error))
            elif key in keyboard.ENTER:
                if selected is None:
                    if missing and self.new_screen(text=missing):
                        return
                    keyboard.set_timeout(stdscr, -1)
                    continue
                if worktrees.missing(selected):
                    self.say(MISSING, "yellow")
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
            ("c", "clean up worktrees with merged PRs or deleted remote branches"),
            ("p", "prune missing worktrees"),
            ("e", "open the worktree in your editor, in a new tab"),
            ("gp", "open its pull request in the browser"),
            ("y / Y", "copy its path / branch name"),
            ("S", "sort: Git order, most recent, or by state"),
            ("r", "refresh"),
            ("q", "quit"),
        ]), ("Filter", [
            ("/", "start typing a filter (fuzzy); while typing, every letter goes into it"),
            ("enter", "open the selected worktree; when nothing matches, start a new worktree from the text"),
            ("↑ / ↓", "move while typing; also ctrl+p / ctrl+n"),
            ("home / end", "the first / last row while typing"),
            ("tab", "stop typing and keep the filter, to use the keys above"),
            ("esc", "clear the filter; with no filter, quit"),
        ]), ("Labels", [
            ("●", "the worktree you opened this from"),
            ("3d", "time since the worktree's last commit"),
            (util.ICONS["main"] + " main checkout", "the original checkout; it can't be removed"),
            (util.ICONS["tab"] + " tab open", "a Rex pane is open in this worktree"),
            (util.ICONS["dirty"] + " uncommitted", "has uncommitted changes"),
            (util.ICONS["ahead"] + " ahead / " + util.ICONS["behind"] + " behind", "commits not yet pushed / pulled"),
            (util.ICONS["deleted"] + " remote deleted", "its remote branch is gone, often after merging"),
            (util.ICONS["missing"] + " folder missing", "the folder is gone; press p to prune"),
            (util.ICONS["pr"] + " #12 open", "its pull request: open, draft, merged, or closed"),
            ("herdr, t3, claude, rex", "before the folder: the tool that created the worktree"),
            ("main, tab, gone, ↑2", "the same labels, shortened to fit a narrower pane; in the "
             "narrowest, only their icons show, and the preview spells out the selected worktree's"),
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
            flash.set(f"Opened #{pr['number']} in the browser.", "green")
        except (RuntimeError, OSError) as error:
            flash.set(str(error))

    def summary(self):
        counts = [(len(self.entries), "worktree"),
                  (sum(e.get("dirty") is True for e in self.entries), "uncommitted"),
                  (sum(worktrees.REMOTE_DELETED in (e.get("track") or []) for e in self.entries), "remote deleted"),
                  (sum(worktrees.missing(e) for e in self.entries), "folder missing")]
        parts = [f"{counts[0][0]} worktree{'s' if counts[0][0] != 1 else ''}"]
        parts += [f"{n} {label}" for n, label in counts[1:] if n]
        return " · ".join(parts)

    def preview(self, entry, space, compact, size):
        """The selected worktree in size rows: a blank row, its full branch, wrapped, then its path
        and as many of its commits as fit. The branch comes first so long ones that look alike in
        the list can be told apart. Compact rows leave out the labels' words, so the preview gives
        them in full."""
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
            labels = [c for c in ui.badges(entry) if not c[0].endswith("folder missing")]  # Said below.
            rows += [ui.joined(labels)] if labels else []
        if path_rows:
            # Wrapped when there's still room for a commit below it, else on one row.
            if len(rows) + len(path_rows) + 2 <= size or len(path_rows) == 1:
                rows += path_rows
            else:
                rows.append([(ui.middle(path, space)[0], dim)])
        if worktrees.missing(entry):
            rows.append([(util.ICONS["missing"] + " " + MISSING, ui.style("yellow"))])
            return rows[:size]
        if len(rows) + 2 > size:
            return rows[:size]  # No room for a commit, so no heading for them either.
        key = (entry["worktree"], self.default)
        if key not in self.commits:
            self.commits[key] = worktrees.recent_commits(entry["worktree"], PREVIEW_COMMITS, self.default)
        description, commits = self.commits[key]
        # "Recent commits", or "Commits" with how many aren't in the default branch yet.
        if description and description != "Recent commits":
            rows.append(ui.section("Commits", description))
        else:
            rows.append(ui.section("Recent commits"))
        if not commits:
            rows.append([("No commits", dim)])
        # Authors only when there's room for them and a readable subject.
        author_width = min(16, max([len(c[2]) for c in commits] + [6])) if space >= 72 else 0
        for sha, subject, author, stamp in commits[:max(0, size - len(rows))]:
            rows.append([(sha, ui.style("yellow")), ("  " + ui.short_age(stamp).rjust(3), dim)]
                        + ([("  " + author[:author_width].ljust(author_width), ui.style("blue"))] if author_width else [])
                        + [("  " + subject, 0)])
        return rows[:size]
