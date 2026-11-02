"""Removing worktrees: one at a time, cleaning up finished ones, and pruning."""
import actions
import keyboard
import ui
import util
import worktrees


class RemovalScreens:
    def remove(self, entry):
        """Confirm and remove a worktree. Returns True if the picker should exit."""
        problem = worktrees.removal_problem(entry)
        if problem:
            self.say(problem)
            return False
        try:
            files = worktrees.changed_files(entry)
            windows = self.windows_for(entry)
            merged = worktrees.merged_into(self.repo, entry)
        except (RuntimeError, OSError) as error:
            self.say(str(error))
            return False
        options = []  # Checkboxes, toggled with space.
        if files:
            options.append({"id": "force", "checked": False,
                            "label": [("Discard uncommitted changes", ui.style("red"))]})
        if merged:
            options.append({"id": "delete_branch", "checked": False,
                            "label": [("Also delete branch ", 0), (entry["name"], ui.style("bold")),
                                      ("   (no unmerged commits)", ui.style("dim"))]})
        checked = self.removal_options(entry, files, windows, merged, options)
        if checked is None:
            return False
        delete_branch = "delete_branch" in checked
        try:
            self.busy(f"Removing {entry['name']}…", lambda: actions.remove_worktree(
                self.repo, self.session, entry, windows, force="force" in checked, delete_branch=delete_branch))
        except (RuntimeError, OSError) as error:
            self.say(str(error))
        else:
            self.say(f"Removed {entry['name']}" + (" and its branch" if delete_branch else ""), "green")
        self.reload()
        return False

    def removal_options(self, entry, files, windows, merged, options):
        """Show the removal confirmation. Returns the set of checked option ids, or None if cancelled."""
        stdscr, cursor, message, pending = self.stdscr, 0, None, None
        keyboard.set_timeout(stdscr, -1)
        while True:
            rows = [[], ui.header("Remove worktree?", self.label), [],
                    [(entry["name"], ui.style("bold"))], [(util.tilde(entry["worktree"]), ui.style("dim"))], []]
            if files:
                rows.append([(f"Uncommitted changes ({len(files)}):", ui.style("yellow"))])
                rows += [[("  " + f, 0)] for f in files[:8]]
                if len(files) > 8:
                    rows.append([(f"  …and {len(files) - 8} more", ui.style("dim"))])
                rows.append([])
            if windows:
                rows.append([("These tabs and all their panes will close:", ui.style("yellow"))])
                rows += [[("  " + (w.get("label") or w["window_id"]), 0)] for w in windows]
                rows.append([])
            for i, option in enumerate(options):
                box = "[x] " if option["checked"] else "[ ] "
                option_row = [ui.pointer(i == cursor), (box, ui.style("cyan"))] + option["label"]
                rows.append(ui.Selected(option_row) if i == cursor else option_row)
            if not merged and "branch" in entry:
                rows.append([("The branch ", 0), (entry["name"], ui.style("bold")), (" will be kept.", 0)])
            rows.append([])
            checked = {o["id"] for o in options if o["checked"]}
            if message:
                rows.append([(util.ICONS["error"] + " " + message, ui.style("red"))])
            elif files and "force" not in checked:
                rows.append([("check “Discard uncommitted changes” to remove", ui.style("dim"))])
            rows.append(ui.footer(enter="remove", move=len(options) > 1, toggle=bool(options), back="cancel"))
            ui.render(stdscr, rows)
            key = keyboard.read_key(stdscr)
            message = None
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
                    ("enter", "remove the worktree"),
                    ui.CLOSE_HELP,
                ])])
            elif key in keyboard.ENTER:
                if files and "force" not in checked:
                    message = "Check “Discard uncommitted changes” first, or press esc to cancel."
                    continue
                return checked

    def cleanup(self):
        """Offer to remove every finished worktree at once. Returns True if the picker should exit."""
        candidates = [e for e in self.entries if worktrees.cleanup_reason(e)]
        if not candidates:
            note = " (pull requests are still loading)" if self.prs_loading else ""
            self.say("No worktrees with merged pull requests or deleted remote branches" + note + ".", "dim")
            return False
        stdscr, cursor, pending = self.stdscr, 0, None
        # Worktrees with uncommitted changes start unchecked, and are never force-removed here.
        items = [{"entry": e, "checked": e.get("dirty") is not True} for e in candidates]
        delete_branches = {"checked": False}
        keyboard.set_timeout(stdscr, -1)
        name_width = min(40, max(len(e["name"]) for e in candidates))
        while True:
            title = f"Clean up {len(candidates)} finished worktree{'s' if len(candidates) != 1 else ''}"
            rows = [[], ui.header(title, self.label),
                    [("Merged pull requests or deleted remote branches.", ui.style("dim"))], []]
            for i, item in enumerate(items):
                entry = item["entry"]
                name = entry["name"] if len(entry["name"]) <= name_width else entry["name"][:name_width - 1] + "…"
                row = [ui.pointer(i == cursor), ("[x] " if item["checked"] else "[ ] ", ui.style("cyan")),
                       (name.ljust(name_width), ui.style("bold") if i == cursor else 0),
                       ("  " + worktrees.cleanup_reason(entry), ui.style("magenta") if "merged" in worktrees.cleanup_reason(entry) else ui.style("red"))]
                if entry.get("dirty") is True:
                    row.append(("  " + util.ICONS["dirty"] + " uncommitted, can't be cleaned up", ui.style("yellow")))
                elif entry.get("open") is True:
                    row.append(("  " + util.ICONS["tab"] + " its tab will close", ui.style("dim")))
                rows.append(ui.Selected(row) if i == cursor else row)
            rows.append([])
            index = len(items)
            branch_row = [ui.pointer(cursor == index), ("[x] " if delete_branches["checked"] else "[ ] ", ui.style("cyan")),
                          ("Also delete their branches", 0), ("   (only ones with no unmerged commits)", ui.style("dim"))]
            rows.append(ui.Selected(branch_row) if cursor == index else branch_row)
            count = sum(i["checked"] for i in items)
            rows += [[], ui.footer(enter=f"remove {count}", move=True, toggle=True, back="cancel")]
            ui.render(stdscr, rows)
            key = keyboard.read_key(stdscr)
            if key in keyboard.CLOSE:
                return False
            # The last row is the "Also delete their branches" option.
            new_cursor, pending = keyboard.motion(key, cursor, len(items) + 1, len(items) + 1, pending)
            if new_cursor is not None:
                cursor = new_cursor
            elif key == "?":
                ui.show_help(stdscr, "Clean up", [("Move", ui.NAVIGATION_HELP), ("Actions", [
                    ("space", "check or uncheck the worktree or option"),
                    ("enter", "remove the checked worktrees"),
                    ui.CLOSE_HELP,
                ])])
            elif key == " ":
                if cursor == len(items):
                    delete_branches["checked"] = not delete_branches["checked"]
                elif items[cursor]["entry"].get("dirty") is not True:
                    items[cursor]["checked"] = not items[cursor]["checked"]
            elif key in keyboard.ENTER:
                chosen = [i["entry"] for i in items if i["checked"]]
                if not chosen:
                    return False
                break

        def work():
            pairs = [(e, delete_branches["checked"] and bool(worktrees.merged_into(self.repo, e))) for e in chosen]
            return actions.remove_many(self.repo, self.session, pairs, self.terminals)

        removed, failures = self.busy(f"Removing {len(chosen)} worktree{'s' if len(chosen) != 1 else ''}…", work)
        if failures:
            self.say(f"Removed {len(removed)}; " + "; ".join(failures))
        else:
            self.say(f"Removed {len(removed)} worktree{'s' if len(removed) != 1 else ''}.", "green")
        self.reload()
        return False

    def prune(self):
        stale = [e for e in self.entries if "prunable" in e]
        if not stale:
            self.say("No missing worktrees to prune.", "dim")
            return

        def lines():
            out = [[("These worktrees' directories no longer exist:", 0)]]
            out += [[("  " + e["name"], ui.style("bold")), ("  " + util.tilde(e["worktree"]), ui.style("dim"))] for e in stale]
            return out + [[], [("Only Git's records are removed; branches are kept.", ui.style("dim"))]]

        title = f"Prune {len(stale)} missing worktree{'s' if len(stale) != 1 else ''}?"
        if ui.confirm(self.stdscr, title, self.label, lines, {"\n": lambda: True}, "prune",
                      [("enter", "prune: forget the missing worktrees")]):
            try:
                self.busy("Pruning…", lambda: worktrees.prune_worktrees(self.repo))
                self.say(f"Pruned {len(stale)} missing worktree{'s' if len(stale) != 1 else ''}.", "green")
            except (RuntimeError, OSError) as error:
                self.say(str(error))
            self.reload()
