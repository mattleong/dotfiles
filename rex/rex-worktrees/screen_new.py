"""Creating worktrees: picking a branch, or naming a new one."""
import curses
import os

import branching
import keyboard
import matching
import tabs
import ui
import util
import worktrees


class NewScreens:
    def new_screen(self):
        """Pick a branch for a new worktree. Returns True once one is created and opened."""
        stdscr = self.stdscr
        keyboard.set_timeout(stdscr, -1)
        cwd = self.cwd
        branches = self.branches()
        keyboard.set_timeout(stdscr, -1)
        suggestions = branches["suggestions"]
        text, tpos, index, top, message, filtering, pending = "", 0, 0, 0, None, False, None
        while True:
            # items[0] creates a new branch; the rest are existing branches.
            existing = [s for s in suggestions if matching.fuzzy_all(text, s["name"])]
            if text.strip():
                existing.sort(key=lambda s: -matching.fuzzy_all(text, s["name"]))
            items = [None] + existing
            index = min(index, len(items) - 1)
            plan = branching.plan_branch(cwd, branches, "", items[index]) if index else None

            height, _ = stdscr.getmaxyx()
            capacity = max(1, height - 12)
            list_index = max(0, index - 1)
            top = min(max(top, list_index - capacity + 1), list_index, max(0, len(existing) - capacity))

            if not keyboard.pending_input(stdscr):
                rows = [[], ui.header("New worktree", self.label),
                        ui.search_row(text, filtering, "/ to filter"), [],
                        (ui.Selected if index == 0 else list)([ui.pointer(index == 0), ("+ Create a new branch…", ui.style("green") | (ui.style("bold") if index == 0 else 0))]),
                        []]
                # The same columns as the worktree list: branch, age, pull request, then where it is.
                visible = existing[top:top + capacity]
                ref_width = min(40, max([len(s["name"]) for s in visible] + [10]))
                prs = [ui.pr_cell({"pr": self.prs.get(s["name"])}) for s in visible]
                pr_width = max([ui.cell_width(c) for c in prs] + [0])
                pr_width = max(pr_width, 2) if pr_width else 0
                dim = ui.style("dim") | ui.style("bold")
                rows.append([(f"EXISTING BRANCHES ({len(existing)})", dim)])
                rows.append([("  " + "BRANCH".ljust(ref_width + 2), dim), ("AGE  ", dim)]
                            + ([("PR".ljust(pr_width + 3), dim)] if pr_width else []) + [("FROM", dim)])
                for i, (item, pr) in enumerate(zip(visible, prs), top + 1):
                    hits = set()
                    for token in text.lower().split():
                        hits.update((matching.fuzzy(token, item["name"]) or (0, []))[1])
                    name = item["name"] if len(item["name"]) <= ref_width else item["name"][:ref_width - 1] + "…"
                    row = [ui.pointer(i == index)]
                    row += ui.highlighted(name, {h for h in hits if h < len(name)}, ui.style("bold") if i == index else 0)
                    row += [(" " * (ref_width - len(name) + 2), 0),
                            (ui.short_age(item.get("time")).rjust(3) + "  ", ui.style("dim"))]
                    if pr_width:
                        for j, (label, attr) in enumerate(pr):
                            row += ([(" · ", ui.style("dim"))] if j else []) + [(label, attr)]
                        row.append((" " * (pr_width - ui.cell_width(pr) + 3), 0))
                    row.append((util.ICONS[item["kind"]] + " " + branching.source_label(item),
                                ui.style("blue") if item["kind"] == "remote" else ui.style("dim")))
                    rows.append(ui.Selected(row) if i == index else row)
                if not existing:
                    rows.append([("  No matching branches" if text.strip() else "  No other branches", ui.style("dim"))])
                rows += [[]] * max(0, height - len(rows) - 4)
                rows.append([])
                if plan is None:
                    rows.append([("enter: ", ui.style("dim")), ("create a new branch", 0)])
                elif plan.get("error"):
                    rows.append([(util.ICONS["error"] + " " + plan["error"], ui.style("red"))])
                else:
                    rows.append([("enter: ", ui.style("dim")), (plan["action"], 0)])
                rows.append([(util.ICONS["error"] + " " + message, ui.style("red"))] if message else [])
                if filtering:
                    rows.append(ui.help_row(*ui.TYPING_FOOTER))
                else:
                    rows.append(ui.footer(enter="select", move=True, slash="filter", clear_filter=bool(text)))
                ui.render(stdscr, rows, cursor=(2, tpos) if filtering else None)
            key = keyboard.read_key(stdscr)
            message = None
            if key is None or key == curses.KEY_RESIZE:
                continue
            if filtering:
                if key in keyboard.ENTER:
                    filtering = False
                elif key == keyboard.ESCAPE:
                    filtering, text, index = False, "", 0
                elif key in keyboard.BACKSPACE and not text:
                    filtering = False
                elif key in (curses.KEY_DOWN, "\x0e"):
                    index = min(len(items) - 1, index + 1)
                elif key in (curses.KEY_UP, "\x10"):
                    index = max(0, index - 1)
                else:
                    new_text, tpos = keyboard.edit_text(text, tpos, key)
                    if new_text != text:
                        # Keep the first match selected while typing.
                        text, index = new_text, 1 if new_text.strip() else 0
                continue
            new_index, pending = keyboard.motion(key, index, len(items), capacity, pending)
            if new_index is not None:
                index = new_index
            elif key == "/":
                filtering, tpos = True, len(text)
            elif key == "?":
                ui.show_help(stdscr, "New worktree", [("Move", ui.NAVIGATION_HELP), ("Actions", [
                    ("enter / l", "create a worktree for the selected branch"),
                    ("", "or, on the first row, create a new branch"),
                    ("/", "filter existing branches"),
                    ("esc", "clear the filter, or go back"),
                    ("q", "go back"),
                ])])
            elif key == keyboard.ESCAPE and text:
                text, index = "", 0
            elif key in keyboard.CLOSE:
                return False
            elif key in keyboard.SELECT:
                if plan is None:
                    if self.new_branch_screen(cwd, branches, text):
                        return True
                    keyboard.set_timeout(stdscr, -1)
                    continue
                if plan.get("error"):
                    continue  # The action line already shows the error.
                try:
                    self.create_and_open(cwd, plan)
                except RuntimeError as error:
                    if not os.path.isdir(plan["path"]):
                        message = str(error)
                        continue
                    raise
                return True

    def new_branch_screen(self, cwd, branches, name=""):
        """Name a new branch and choose its base. Returns True once a worktree is created and opened."""
        stdscr = self.stdscr
        keyboard.set_timeout(stdscr, -1)
        bases = branches["bases"]
        # Works like the other screens: j/k choose the base, and / types the name (enter done, esc clear).
        text, tpos, base, message, editing, pending = name, len(name), 0, None, False, None
        while True:
            plan = branching.plan_branch(cwd, branches, text, None, bases[base])
            typed = branching.normalize_branch(text)
            name_cell = (text, 0) if text or editing else ("/ to type a name", ui.style("dim"))
            rows = [[], ui.header("New branch", self.label), [],
                    [("Name   ", ui.style("dim")), name_cell]]
            if plan.get("error") and text.strip():
                rows.append([("       ", 0), (util.ICONS["error"] + " " + plan["error"], ui.style("red"))])
            elif typed and typed != text.strip():
                rows.append([("       → ", ui.style("dim")), (typed, ui.style("bold"))])
            else:
                rows.append([])
            rows.append([])
            for i, option in enumerate(bases):
                label = "Base   " if i == 0 else "       "
                base_row = [(label, ui.style("dim")), ui.pointer(i == base),
                            (option["label"], ui.style("bold") if i == base else 0),
                            ("  current branch" if option["ref"] == "HEAD" else "  default branch", ui.style("dim"))]
                rows.append(ui.Selected(base_row) if i == base else base_row)
            height, _ = stdscr.getmaxyx()
            rows += [[]] * max(0, height - len(rows) - 4)
            rows.append([])
            if text.strip() and not plan.get("error"):
                rows.append([("enter: ", ui.style("dim")), (plan["action"], 0)])
            else:
                rows.append([("Type a name for the new branch.", ui.style("dim"))])
            rows.append([(util.ICONS["error"] + " " + message, ui.style("red"))] if message else [])
            if editing:
                rows.append(ui.help_row(*ui.TYPING_FOOTER))
            else:
                rows.append(ui.footer(enter="create", move=len(bases) > 1, slash="name"))
            ui.render(stdscr, rows, cursor=(3, 7 + tpos) if editing else None)
            key = keyboard.read_key(stdscr)
            message = None
            if key is None or key == curses.KEY_RESIZE:
                continue
            if editing:
                # The same keys as the filters on the other screens.
                if key in keyboard.ENTER:
                    editing = False
                elif key == keyboard.ESCAPE:
                    editing, text, tpos = False, "", 0
                elif key in keyboard.BACKSPACE and not text:
                    editing = False
                elif key in (curses.KEY_DOWN, "\x0e"):
                    base = min(len(bases) - 1, base + 1)
                elif key in (curses.KEY_UP, "\x10"):
                    base = max(0, base - 1)
                else:
                    text, tpos = keyboard.edit_text(text, tpos, key)
                continue
            new_base, pending = keyboard.motion(key, base, len(bases), len(bases), pending)
            if new_base is not None:
                base = new_base
            elif key == "/":
                editing, tpos = True, len(text)
            elif key in keyboard.CLOSE:
                return False
            elif key == "?":
                ui.show_help(stdscr, "New branch", [("Name", [
                    ("/", "type the name; spaces become dashes"),
                    ("enter / esc", "finish typing / clear the name"),
                ]), ("Base", [
                    ("j / k, gg / G", "choose what the branch starts from"),
                ]), ("Actions", [
                    ("enter / l", "create the worktree and open its tab"),
                    ui.CLOSE_HELP,
                ])])
            elif key in keyboard.SELECT:
                if not plan.get("error"):
                    try:
                        self.create_and_open(cwd, plan)
                        return True
                    except RuntimeError as error:
                        if os.path.isdir(plan["path"]):
                            raise
                        message = str(error)
                elif not text.strip():
                    editing, tpos = True, len(text)  # Nothing to create yet; start typing the name.

    def create_and_open(self, cwd, plan):
        """Create the worktree, then open its tab. Raises RuntimeError on failure."""
        try:
            self.busy(f"Creating worktree for {plan['branch']}…", lambda: branching.create_worktree(cwd, plan))
        except OSError as error:
            raise RuntimeError(str(error)) from error
        try:
            self.busy("Opening tab…", lambda: tabs.open_tab(
                self.session, {"worktree": str(plan["path"]), "branch": plan["branch"]}, self.client,
                worktrees.setup_command(cwd)))
        except (RuntimeError, OSError) as error:
            raise RuntimeError(f"Worktree created at {plan['path']}, but opening its tab failed: {error}") from error
