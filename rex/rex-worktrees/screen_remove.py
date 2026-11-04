"""Removing worktrees: one at a time, cleaning up finished ones, and pruning."""
import actions
import keyboard
import tabs
import ui
import util
import worktrees


def closing_rows(groups):
    """A row per tab with panes that will close, marking the tab the picker was opened in."""
    rows = []
    for group in groups:
        label, detail = tabs.describe_closing(group)
        rows.append([("  " + label, ui.style("bold"))]
                    + ([(" (this tab)", ui.style("cyan"))] if group["here"] else [])
                    + [("  " + detail, ui.style("dim"))])
    return rows


def closing_heading(groups):
    count = sum(len(g["panes"]) for g in groups)
    return ui.section(f"Panes that will close ({count})", tone="yellow")


def closing_summary(groups):
    """What closes, in a sentence, such as "Closes 1 of 2 panes in Shell (this tab)"."""
    parts = []
    for group in groups:
        label, _ = tabs.describe_closing(group)
        label += " (this tab)" if group["here"] else ""
        count = len(group["panes"])
        if group["whole"]:
            parts.append(f"its tab {label}")
        else:
            parts.append(f"{count} of {group['total']} panes in {label}")
    return "Closes " + ", ".join(parts) + "." if parts else ""


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
            parts = [ui.fixed(*[[(line, ui.style("bold"))] for line in ui.wrap(entry["name"], width)]),
                     ui.extra(*[[(line, ui.style("dim"))] for line in ui.wrap_path(util.tilde(entry["worktree"]), width)]),
                     ui.GAP]
            if files:
                parts += [ui.fixed(ui.section(f"Uncommitted changes ({len(files)})", tone="yellow")),
                          ui.items([[("  " + f, 0)] for f in files]), ui.GAP]
            if closing:
                parts += [ui.fixed(closing_heading(closing)), ui.items(closing_rows(closing)), ui.GAP]
            option_rows = []
            for i, option in enumerate(options):
                box = "[x] " if option["checked"] else "[ ] "
                row = [ui.pointer(i == cursor), (box, ui.style("cyan"))] + option["label"]
                option_rows.append(ui.Selected(row) if i == cursor else row)
            if not merged and "branch" in entry:
                option_rows += [[(line, 0)] for line in ui.wrap(f"The branch {entry['name']} will be kept.", width)]
            parts.append(ui.fixed(*option_rows))
            status = flash.rows(width)
            if not status and files and "force" not in checked:
                status = [[(line, ui.style("dim"))] for line in
                          ui.wrap("Check “Discard uncommitted changes” to remove.", width)]
            top = [ui.header("Remove worktree?", self.label), []]
            bottom = status + [ui.footer(enter="remove", move=len(options) > 1, toggle=bool(options), back="cancel")]
            room = height - ui.margin(height) - len(top) - len(bottom)
            ui.render(stdscr, ui.compose(height, top, ui.fit_parts(parts, room), bottom))
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
            elif key == "?":
                ui.show_help(stdscr, "Remove worktree", [("Keys", [
                    ("j / k", "move between the options"),
                    ("space", "check or uncheck the option"),
                    ("enter", "remove the worktree, and close the panes inside it"),
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
            status = flash.rows(width) or self.cleanup_detail(items, cursor, closes, width)
            count = sum(i["checked"] for i in items)
            title = f"Clean up {len(candidates)} finished worktree{'s' if len(candidates) != 1 else ''}"
            heading = [ui.header(title, self.label)]
            intro = "Merged pull requests or deleted remote branches."
            intro = [(intro if len(intro) + 8 <= width else "Merged PRs or deleted remote branches.", ui.style("dim"))]
            bottom = status + [ui.footer(enter=f"remove {count}", move=True, toggle=True, back="cancel")]
            # Below the list: a blank row, the branch option, and a blank row above the status.
            capacity = height - ui.margin(height) - len(heading) - 2 - 3 - len(bottom)
            gaps = capacity >= min(3, len(items))
            if not gaps:  # A short pane: the list keeps the rows.
                intro, capacity = None, height - ui.margin(height) - len(heading) - 1 - len(bottom)
            capacity = max(1, capacity)
            top = ui.scroll_top(top, min(cursor, len(items) - 1), len(items), capacity)
            rows = [self.cleanup_row(item, i == cursor, items, closes, width) for i, item in enumerate(items)]
            body = rows[top:top + capacity] + ([[]] if gaps else [])
            body.append(ui.Selected(branch_row) if cursor == index else branch_row)
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
                    flash.set(f"{items[cursor]['entry']['name']} has uncommitted changes, so it can't be cleaned up "
                              "here. Remove it on its own with dd.", "yellow")
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
            self.say(f"Removed {len(removed)}; " + "; ".join(failures))
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
    def cleanup_detail(items, cursor, closes, width):
        """The selected row in full, wrapped: its branch, its reason, and what removing it does."""
        if cursor >= len(items):
            text, tone = "Deletes each removed worktree's branch when it has no unmerged commits.", ui.style("dim")
        else:
            entry = items[cursor]["entry"]
            text = f"{entry['name']}: {worktrees.cleanup_reason(entry)}."
            if entry.get("dirty") is True:
                text += " It has uncommitted changes, so it can't be cleaned up here; remove it on its own with dd."
                tone = ui.style("yellow")
            else:
                closing = closing_summary(closes.get(entry["worktree"]) or [])
                text += " " + closing if closing else ""
                tone = ui.style("dim")
        lines = ui.wrap(text, width)
        if len(lines) > ui.Flash.LINES:
            lines = lines[:ui.Flash.LINES]
            lines[-1] = lines[-1][:width - 1] + "…"
        return [[(line, tone)] for line in lines]

    def prune(self):
        stale = [e for e in self.entries if "prunable" in e]
        if not stale:
            self.say("No missing worktrees to prune.", "dim")
            return

        def parts(width):
            rows = [[("  " + e["name"], ui.style("bold")), ("  " + util.tilde(e["worktree"]), ui.style("dim"))] for e in stale]
            note = ui.wrap("Only Git's records are removed; branches are kept.", width)
            return [ui.fixed(ui.section("Missing folders", tone="yellow")), ui.items(rows), ui.GAP,
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
