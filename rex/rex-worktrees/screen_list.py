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
            preview_rows = 7 if height >= 22 else 0
            capacity = max(1, height - 8 - preview_rows)
            index = shown.index(selected) if selected in shown else 0
            top = min(max(top, index - capacity + 1), index, max(0, len(shown) - capacity))
            widths = ui.column_widths(self.entries)

            if not keyboard.pending_input(stdscr):
                sort_note = "" if self.sort == "git" else f" · sorted by {self.sort}"
                rows = [[], ui.header("Worktrees", self.label, self.summary() + sort_note),
                        ui.search_row(query, filtering, "/ to filter"), [], ui.header_row(widths)]
                for e in shown[top:top + capacity]:
                    rows.append(ui.entry_row(e, e is selected, widths,
                                          matching.match_entry(e, query) if query.strip() else None))
                if not shown:
                    rows.append([("No matching worktrees", ui.style("dim"))])
                rows += [[]] * max(0, height - len(rows) - 3 - preview_rows)
                if preview_rows:
                    rows += self.preview(selected)  # Pinned to the bottom of the pane.
                if self.flash.row():
                    rows.append(self.flash.row())
                elif query.strip():
                    rows.append([(f"{len(shown)} of {len(self.entries)} match", ui.style("dim"))])
                else:
                    rows.append([])
                if filtering:
                    rows.append(ui.help_row(*ui.TYPING_FOOTER))
                else:
                    finished = sum(1 for e in self.entries if worktrees.cleanup_reason(e))
                    rows.append(ui.footer(enter="open", move=True, slash="filter", back="quit", clear_filter=bool(query),
                                          actions=[("s", "split"), ("n", "new"), ("dd", "remove")]
                                          + ([("c", f"clean up ({finished})")] if finished else [])))
                ui.render(stdscr, rows, cursor=(2, qpos) if filtering else None)

            key = keyboard.read_key(stdscr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            self.flash.clear()
            if filtering:
                if key in keyboard.ENTER:
                    filtering = False
                elif key == keyboard.ESCAPE:
                    filtering, query = False, ""
                elif key in keyboard.BACKSPACE and not query:
                    filtering = False
                elif key in (curses.KEY_DOWN, "\x0e", curses.KEY_UP, "\x10"):
                    new_index, _ = keyboard.motion(key, index, len(shown), capacity, None)
                    if new_index is not None:
                        selected = shown[new_index]
                else:
                    query, qpos = keyboard.edit_text(query, qpos, key)
                continue
            if key == "d":
                if pending == "d":
                    pending = None
                    if selected and self.remove(selected):
                        return
                    selected, query = None, ""
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
                ui.show_help(stdscr, "Worktrees", [("Move", ui.NAVIGATION_HELP), ("Actions", [
                    ("enter / l", "open the worktree's tab"),
                    ("s", "open the worktree in a split beside your pane"),
                    ("o", "open its tab in the background and stay here"),
                    ("n", "new worktree"),
                    ("dd", "remove the worktree"),
                    ("c", "clean up worktrees with merged PRs or deleted remote branches"),
                    ("p", "prune missing worktrees"),
                    ("e", "open the worktree in your editor, in a new tab"),
                    ("gp", "open its pull request in the browser"),
                    ("y / Y", "copy its path / branch name"),
                    ("S", "sort: Git order, most recent, or by state"),
                    ("r", "refresh"),
                    ("/", "filter (fuzzy)"),
                    ("esc", "clear the filter, or quit"),
                    ("q", "quit"),
                ]), ("Labels", [
                    ("●", "the worktree you opened this from"),
                    ("3d", "time since the worktree's last commit"),
                    (util.ICONS["main"] + " main checkout", "the original checkout; it can't be removed"),
                    (util.ICONS["tab"] + " tab open", "a Rex tab is in this worktree"),
                    (util.ICONS["dirty"] + " uncommitted", "has uncommitted changes"),
                    (util.ICONS["ahead"] + " ahead / " + util.ICONS["behind"] + " behind", "commits not yet pushed / pulled"),
                    (util.ICONS["deleted"] + " remote deleted", "its remote branch is gone, often after merging"),
                    (util.ICONS["missing"] + " folder missing", "the folder is gone; press p to prune"),
                    (util.ICONS["pr"] + " #12 open", "its pull request: open, draft, merged, or closed"),
                    ("herdr, t3, claude, rex", "the tool that created the worktree"),
                ])])
            elif key == keyboard.ESCAPE and query:
                query = ""
            elif key in keyboard.CLOSE:
                return
            elif key == "n":
                if self.new_screen():
                    return
                keyboard.set_timeout(stdscr, -1)
            elif key == "p":
                self.prune()
                selected, query = None, ""
            elif key == "c":
                if self.cleanup():
                    return
                selected, query = None, ""
            elif key == "S":
                self.sort = matching.SORTS[(matching.SORTS.index(self.sort) + 1) % len(matching.SORTS)]
                self.say(f"Sorted by {self.sort}" + (" order" if self.sort == "git" else ""), "dim")
            elif key in ("y", "Y") and selected:
                value = selected["worktree"] if key == "y" else selected["name"]
                try:
                    actions.copy_to_clipboard(value)
                    self.say("Copied " + ("path" if key == "y" else "branch") + ": " + value, "green")
                except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
                    self.say(str(error))
            elif key == "e" and selected:
                if worktrees.missing(selected):
                    self.say("This worktree's directory is missing; press p to prune it.")
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
                    self.say("This worktree's directory is missing; press p to prune it.")
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
                    self.say(f"Opened {tabs.tab_label(entry)} in the background." if opened
                             else f"{entry['name']} already has a tab open.", "green" if opened else "dim")
                except (RuntimeError, OSError) as error:
                    self.say(str(error))
            elif key in keyboard.SELECT:
                if selected is None:
                    continue
                if worktrees.missing(selected):
                    self.say("This worktree's directory is missing; press p to prune it.")
                    continue
                try:
                    entry = selected
                    self.busy("Opening…", lambda: tabs.open_tab(self.session, entry, self.client,
                                                          terminals=self.terminals))
                    return
                except (RuntimeError, OSError) as error:
                    self.say(str(error))

    def open_pr(self, entry):
        pr = (entry or {}).get("pr")
        if not pr:
            self.say("No pull request found for this worktree." if self.show_prs
                     else "Pull requests need the gh command.", "dim")
            return
        try:
            self.busy(f"Opening #{pr['number']}…", lambda: github.open_pull_request(self.repo, pr["number"]))
            self.say(f"Opened #{pr['number']} in the browser.", "green")
        except (RuntimeError, OSError) as error:
            self.say(str(error))

    def summary(self):
        counts = [(len(self.entries), "worktree"),
                  (sum(e.get("dirty") is True for e in self.entries), "uncommitted"),
                  (sum(worktrees.REMOTE_DELETED in (e.get("track") or []) for e in self.entries), "remote deleted"),
                  (sum(worktrees.missing(e) for e in self.entries), "folder missing")]
        parts = [f"{counts[0][0]} worktree{'s' if counts[0][0] != 1 else ''}"]
        parts += [f"{n} {label}" for n, label in counts[1:] if n]
        return " · ".join(parts)

    def preview(self, entry):
        if entry is None:
            return [[]] * 7
        path, key = entry["worktree"], (entry["worktree"], self.default)
        if key not in self.commits:
            self.commits[key] = worktrees.recent_commits(path, base=self.default)
        description, commits = self.commits[key]
        # "Recent commits", or "Commits" with how many aren't in the default branch yet.
        if description and description != "Recent commits":
            rows = [ui.section("Commits", description + " · " + util.tilde(path))]
        else:
            rows = [ui.section("Recent commits", util.tilde(path))]
        if worktrees.missing(entry):
            rows.append([(util.ICONS["missing"] + " Folder missing", ui.style("red"))])
        elif not commits:
            rows.append([("No commits", ui.style("dim"))])
        author_width = min(16, max([len(c[2]) for c in commits] + [6]))
        for sha, subject, author, stamp in commits[:5]:
            rows.append([(sha, ui.style("yellow")), ("  " + ui.short_age(stamp).rjust(3), ui.style("dim")),
                         ("  " + author[:author_width].ljust(author_width), ui.style("blue")), ("  " + subject, 0)])
        return rows + [[]] * (7 - len(rows))
