"""Removing worktrees: one at a time, cleaning up finished ones, and pruning."""
import actions
import keyboard
import tabs
import ui
import util
import worktrees


def where(group, session, label):
    """Which tab a group is in, when that's worth saying: the picker's own, or one in another
    session, such as the repository's."""
    if group["here"]:
        return " (this tab)"
    other = group["window"].get("session_id")
    return f" ({label} session)" if other and session and other != session else ""


def closing_rows(groups, width, session="", label=""):
    """A row per tab with panes that will close, marking the tab the picker was opened in and tabs
    in other sessions. A long tab name is cut, so what closes in it stays in view; in a narrow pane,
    what closes goes on its own row below."""
    rows = []
    for group in groups:
        name, detail = tabs.describe_closing(group)
        mark = where(group, session, label)
        room = width - 2 - len(mark) - 2 - len(detail)
        below = room < min(len(name), 12)
        if below:
            room = max(4, width - 2 - len(mark))
        name = name if len(name) <= room else name[:room - 1] + "…"
        row = [("  " + name, ui.style("bold"))] + ([(mark, ui.style("cyan"))] if mark else [])
        if below:
            rows += [row, [("    " + detail, ui.style("dim"))]]
        else:
            rows.append(row + [("  " + detail, ui.style("dim"))])
    return rows


def closing_heading(groups):
    count = sum(len(g["panes"]) for g in groups)
    return ui.section(f"Panes that will close ({count})", tone="yellow")


LIST_HINT = " (tab lists all)"


def summary_row(text):
    """A list's place when there's no room for any of its rows, such as "16 uncommitted files"."""
    return [("  " + text, ui.style("yellow") | ui.style("bold")), ("  (tab lists them)", ui.style("dim"), 1)]


def plural(count, word):
    return f"{count} {word}{'s' if count != 1 else ''}"


def closing_summary(groups, session="", label=""):
    """What closes, in a sentence, such as "Closes 1 of 2 panes in Shell (this tab)"."""
    parts = []
    for group in groups:
        name, _ = tabs.describe_closing(group)
        name += where(group, session, label)
        if group["whole"]:
            parts.append(f"the tab {name}")
        else:
            parts.append(f"{len(group['panes'])} of {group['total']} panes in {name}")
    return "Closes " + ", ".join(parts) + "." if parts else ""


def cleanup_reason_text(entry):
    """Why a worktree is offered for cleanup, as a sentence."""
    reason = worktrees.cleanup_reason(entry) or ""
    if reason.endswith(" merged"):
        return f"Its pull request {reason}."
    return "Its remote branch was deleted." if reason == worktrees.REMOTE_DELETED else ""


DIRTY_NOTE = "Has uncommitted changes, so it can't be cleaned up here; remove it on its own with dd."


class RemovalScreens:
    def remove(self, entry):
        """Confirm and remove a worktree. Returns True if the picker should exit."""
        problem = worktrees.removal_problem(entry)
        if problem:
            self.say(problem, "yellow")
            return False
        try:
            files = worktrees.changed_files(entry)
            closing = tabs.panes_to_close(self.terminals_now(), [entry["worktree"]])
            merged = worktrees.merged_into(self.repo, entry)
        except (RuntimeError, OSError, ValueError) as error:
            self.say(str(error))
            return False
        options = []  # Checkboxes, toggled with space.
        if files:
            options.append({"id": "force", "checked": False,
                            "label": [("Discard uncommitted changes", ui.style("red"))]})
        if merged:
            options.append({"id": "delete_branch", "checked": False,
                            "label": [("Also delete the branch", 0), ("   (no unmerged commits)", ui.style("dim"), 1)]})
        checked = self.removal_options(entry, files, closing, merged, options)
        if checked is None:
            return False
        delete_branch = "delete_branch" in checked
        try:
            self.busy(f"Removing {entry['name']}…", lambda: actions.remove_worktree(
                self.repo, self.session, entry, closing, force="force" in checked, delete_branch=delete_branch))
        except (RuntimeError, OSError) as error:
            self.say(str(error))
        else:
            self.say(f"Removed {entry['name']}" + (" and its branch." if delete_branch else "."), "green")
        self.reload()
        return False

    def removal_options(self, entry, files, closing, merged, options):
        """Show the removal confirmation. Returns the set of checked option ids, or None if cancelled.
        The options and key hints always show; the lists of files and panes shorten to fit."""
        stdscr, cursor, pending, flash = self.stdscr, 0, None, ui.Flash()
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            height, _ = stdscr.getmaxyx()
            width = ui.space(stdscr)
            checked = {o["id"] for o in options if o["checked"]}

            def parts(width):
                """The worktree, then what removing it loses and closes, then the options. When
                they don't all fit, each list keeps at least a line saying how many, and tab shows
                them all."""
                body = [ui.fixed(*[[(line, ui.style("bold"))] for line in ui.wrap_path(entry["name"], width)]),
                        ui.extra(*[[(line, ui.style("dim"))] for line in ui.wrap_path(util.tilde(entry["worktree"]), width)]),
                        ui.GAP]
                if files:
                    body += [ui.items([[("  " + f, 0)] for f in files], heading=ui.section(
                        f"Uncommitted changes ({len(files)})", tone="yellow"),
                        summary=summary_row(plural(len(files), "uncommitted file")), hint=LIST_HINT), ui.GAP]
                if closing:
                    count = sum(len(g["panes"]) for g in closing)
                    body += [ui.items(closing_rows(closing, width, self.session, self.label),
                                      heading=closing_heading(closing),
                                      summary=summary_row(plural(count, "pane") + " will close"), hint=LIST_HINT),
                             ui.GAP]
                option_rows = []
                for i, option in enumerate(options):
                    box = "[x] " if option["checked"] else "[ ] "
                    row = [ui.pointer(i == cursor), (box, ui.style("cyan"))] + option["label"]
                    option_rows.append(ui.Selected(row) if i == cursor else row)
                if not merged and "branch" in entry:
                    option_rows.append([("The branch will be kept.", 0)])
                return body + [ui.fixed(*option_rows)]

            status = flash.rows(width)
            if not status and files and "force" not in checked:
                status = [[(line, ui.style("dim"))] for line in
                          ui.wrap("Check “Discard uncommitted changes” to remove.", width)]
            top = [ui.header("Remove worktree?", self.label), []]
            room = height - ui.margin(height) - len(top) - len(status) - 1
            body = parts(width)
            bottom = status + [ui.footer(enter="remove", move=len(options) > 1, toggle=bool(options), back="cancel",
                                         details=ui.cut(body, room))]
            ui.render(stdscr, ui.compose(height, top, ui.fit_parts(body, room), bottom))
            key = keyboard.read_key(stdscr)
            if key is None:
                continue
            flash.clear()
            if key in keyboard.CLOSE:
                return None
            new_cursor, pending = keyboard.motion(key, cursor, len(options), len(options), pending)
            if new_cursor is not None:
                cursor = new_cursor
            elif key == " " and options:
                options[cursor]["checked"] = not options[cursor]["checked"]
            elif key == "\t":
                ui.show_details(stdscr, "Remove worktree", parts)
            elif key == "?":
                ui.show_help(stdscr, "Remove worktree", [("Keys", [
                    ("j / k", "move between the options"),
                    ("space", "check or uncheck the option"),
                    ("enter", "remove the worktree, and close the panes inside it"),
                    ui.DETAILS_HELP,
                    ui.CLOSE_HELP,
                ])])
            elif key in keyboard.ENTER:
                if files and "force" not in checked:
                    flash.set("Check “Discard uncommitted changes” first, or press q to cancel.", "yellow")
                    continue
                return checked

    def cleanup(self):
        """Offer to remove every finished worktree at once. Returns True if the picker should exit."""
        candidates = [e for e in self.entries if worktrees.cleanup_reason(e)]
        if not candidates:
            note = " (pull requests are still loading)" if self.prs_loading else ""
            self.say("No worktrees with merged pull requests or deleted remote branches" + note + ".", "dim")
            return False
        try:
            terminals = self.terminals_now()
        except (RuntimeError, OSError, ValueError):
            terminals = []
        closes = {e["worktree"]: tabs.panes_to_close(terminals, [e["worktree"]]) for e in candidates}
        stdscr, cursor, top, pending, flash = self.stdscr, 0, 0, None, ui.Flash()
        # Worktrees with uncommitted changes start unchecked, and are never force-removed here.
        items = [{"entry": e, "checked": e.get("dirty") is not True} for e in candidates]
        delete_branches = {"checked": False}
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            height, _ = stdscr.getmaxyx()
            width = ui.space(stdscr)
            index = len(items)  # The cursor's row for "Also delete their branches".
            branch_row = [ui.pointer(cursor == index), ("[x] " if delete_branches["checked"] else "[ ] ", ui.style("cyan")),
                          ("Also delete their branches", 0), ("   (only ones with no unmerged commits)", ui.style("dim"), 1)]
            # The selected row's detail, in the same number of rows for every row, so the list
            # doesn't jump as the cursor moves.
            lines = max(1, min(4, (height - 8) // 2))
            details = [self.cleanup_detail(items, i, closes, width, lines, self.session, self.label)
                       for i in range(len(items) + 1)]
            # Under the options, or a message in its place.
            detail = flash.rows(width) or details[cursor]
            detail += [[]] * (max(len(d) for d in details) - len(detail))
            count = sum(i["checked"] for i in items)
            title = f"Clean up {len(candidates)} finished worktree{'s' if len(candidates) != 1 else ''}"
            heading = [ui.header(title, self.label)]
            intro = "Merged pull requests or deleted remote branches."
            intro = [(intro if len(intro) + 8 <= width else "Merged PRs or deleted remote branches.", ui.style("dim"))]
            bottom = [ui.footer(enter=f"remove {count}", move=True, toggle=True, back="cancel")]
            # Below the list: a blank row, the branch option, a blank row, and the detail.
            capacity = height - ui.margin(height) - len(heading) - 2 - 3 - len(detail) - len(bottom)
            gaps = capacity >= min(3, len(items))
            if not gaps:  # A short pane: the list keeps the rows.
                intro, capacity = None, height - ui.margin(height) - len(heading) - 1 - len(detail) - len(bottom)
            capacity = max(1, capacity)
            top = ui.scroll_top(top, min(cursor, len(items) - 1), len(items), capacity)
            rows = [self.cleanup_row(item, i == cursor, items, closes, width) for i, item in enumerate(items)]
            body = rows[top:top + capacity] + ([[]] if gaps else [])
            body.append(ui.Selected(branch_row) if cursor == index else branch_row)
            body += ([[]] if gaps else []) + detail
            first = ui.with_position(intro or heading[0], min(cursor, len(items) - 1), len(items), capacity, width)
            head = ([heading[0], first, []] if intro else [first])
            ui.render(stdscr, ui.compose(height, head, body, bottom))
            key = keyboard.read_key(stdscr)
            if key is None:
                continue
            flash.clear()
            if key in keyboard.CLOSE:
                return False
            # The last row is the "Also delete their branches" option.
            new_cursor, pending = keyboard.motion(key, cursor, len(items) + 1, capacity, pending)
            if new_cursor is not None:
                cursor = new_cursor
            elif key == "?":
                ui.show_help(stdscr, "Clean up", [("Move", ui.NAVIGATION_HELP), ("Actions", [
                    ("space", "check or uncheck the worktree or option"),
                    ("enter", "remove the checked worktrees, and close the panes inside them"),
                    ui.CLOSE_HELP,
                ]), ("Notes", [
                    (util.ICONS["dirty"], "uncommitted changes: it can't be cleaned up here; remove it with dd"),
                    (util.ICONS["tab"], "panes inside it will close"),
                ])])
            elif key == " ":
                if cursor == index:
                    delete_branches["checked"] = not delete_branches["checked"]
                elif items[cursor]["entry"].get("dirty") is True:
                    flash.set("Can't check it: it has uncommitted changes. Remove it on its own with dd.", "yellow")
                else:
                    items[cursor]["checked"] = not items[cursor]["checked"]
            elif key in keyboard.ENTER:
                chosen = [i["entry"] for i in items if i["checked"]]
                if not chosen:
                    flash.set("Nothing is checked. Check a worktree with space, or press q to cancel.", "yellow")
                    continue
                break

        def work():
            pairs = [(e, delete_branches["checked"] and bool(worktrees.merged_into(self.repo, e))) for e in chosen]
            return actions.remove_many(self.repo, self.session, pairs, terminals)

        removed, failures = self.busy(f"Removing {len(chosen)} worktree{'s' if len(chosen) != 1 else ''}…", work)
        if failures:
            done = f"Removed {len(removed)}" + (f" of {len(chosen)}" if len(removed) != len(chosen) else "")
            self.say(done + ", but " + "; ".join(failures).rstrip(".") + ".")
        else:
            self.say(f"Removed {len(removed)} worktree{'s' if len(removed) != 1 else ''}.", "green")
        self.reload()
        return False

    @staticmethod
    def cleanup_row(item, selected, items, closes, width):
        """A candidate: its checkbox, branch, and reason, then a note that shortens to its icon in a
        narrow pane: uncommitted changes, or panes that will close."""
        entry = item["entry"]
        reasons = max(len(worktrees.cleanup_reason(i["entry"])) for i in items)
        longest = max(len(i["entry"]["name"]) for i in items)
        dirty = entry.get("dirty") is True
        groups = closes.get(entry["worktree"]) or []
        panes = sum(len(g["panes"]) for g in groups)
        if dirty:
            notes = ["uncommitted, can't be cleaned up", "uncommitted"]
            icon, tone = util.ICONS["dirty"], ui.style("yellow")
        elif groups:
            whole = len(groups) == 1 and groups[0]["whole"]
            notes = ["closes its tab" if whole else f"closes {panes} pane{'s' if panes != 1 else ''}"]
            icon, tone = util.ICONS["tab"], ui.style("dim")
        else:
            notes, icon, tone = [], "", 0
        # Leave room for the reason and at least a note's icon beside the branch.
        name_width = max(6, min(longest, width - 6 - 2 - reasons - 3))
        name = entry["name"] if len(entry["name"]) <= name_width else entry["name"][:name_width - 1] + "…"
        reason = worktrees.cleanup_reason(entry)
        box = ("[ ] ", ui.style("dim")) if dirty else ("[x] " if item["checked"] else "[ ] ", ui.style("cyan"))
        row = [ui.pointer(selected), box, (name.ljust(name_width), ui.style("bold") if selected else 0),
               ("  " + reason.ljust(reasons), ui.style("magenta") if "merged" in reason else ui.style("red"))]
        if icon:
            room = width - 6 - name_width - 2 - reasons - 2
            note = next((n for n in notes if len(icon) + 1 + len(n) <= room), "")
            row.append(("  " + icon + (" " + note if note else ""), tone))
        return ui.Selected(row) if selected else row

    @staticmethod
    def cleanup_detail(items, cursor, closes, width, lines=3, session="", label=""):
        """The selected row explained in up to lines rows: why it can't be cleaned up, or why it's
        offered and what removing it closes; then its branch in full, or cut in the middle."""
        if cursor >= len(items):
            text = "Deletes each removed worktree's branch when it has no unmerged commits."
            return [[(line, ui.style("dim"))] for line in ui.wrap(text, width)[:lines]]
        entry = items[cursor]["entry"]
        if entry.get("dirty") is True:
            text, tone = DIRTY_NOTE, ui.style("yellow")
        else:
            closing = closing_summary(closes.get(entry["worktree"]) or [], session, label)
            text, tone = cleanup_reason_text(entry) + (" " + closing if closing else ""), ui.style("dim")
        reason = ui.wrap(text, width)
        name = ui.wrap_path(entry["name"], width)
        if len(reason) + len(name) > lines:
            name = [ui.middle(entry["name"], width)[0]] if lines > 1 else []
        if len(reason) + len(name) > lines:
            reason = reason[:max(1, lines - len(name))]
            reason[-1] = reason[-1][:width - 1] + "…"
        return [[(line, tone)] for line in reason] + [[(line, ui.style("bold"))] for line in name]

    def prune(self):
        stale = [e for e in self.entries if "prunable" in e]
        if not stale:
            self.say("No missing worktrees to prune.", "dim")
            return

        def parts(width):
            rows = []
            for e in stale:
                # Each name, then its path cut in the middle, keeping the folder's name at the end.
                path = util.tilde(e["worktree"])
                room = max(16, width - 4 - len(e["name"]))
                name = ui.middle(e["name"], max(4, width - 4 - min(room, len(path))))[0]
                rows.append([("  " + name, ui.style("bold")),
                             ("  " + ui.middle(path, max(1, width - 4 - len(name)))[0], ui.style("dim"))])
            note = ui.wrap("Only Git's records are removed; branches are kept.", width)
            return [ui.items(rows, heading=ui.section(f"Missing folders ({len(stale)})", tone="yellow"),
                             summary=summary_row(plural(len(stale), "missing folder")), hint=LIST_HINT), ui.GAP,
                    ui.fixed(*[[(line, ui.style("dim"))] for line in note])]

        title = f"Prune {len(stale)} missing worktree{'s' if len(stale) != 1 else ''}?"
        if ui.confirm(self.stdscr, title, self.label, parts, {"\n": lambda: True}, "prune",
                      [("enter", "prune: forget the missing worktrees")]):
            try:
                self.busy("Pruning…", lambda: worktrees.prune_worktrees(self.repo))
                self.say(f"Pruned {len(stale)} missing worktree{'s' if len(stale) != 1 else ''}.", "green")
            except (RuntimeError, OSError) as error:
                self.say(str(error))
            self.reload()
