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


# Keys the branch list acts on; any other letter starts the filter with it.
NEW_KEYS = set("jkgGq?/")
PREFIX = "enter: "


def plan_rows(lines, width, limit, tone=0):
    """What enter does, wrapped beside "enter:", in at most limit rows. lines are the sentences to
    wrap: the action, then where the worktree goes, and so on."""
    rows = []
    for text in lines:
        for line in ui.wrap_path(text, width - len(PREFIX)):
            rows.append([(PREFIX if not rows else " " * len(PREFIX), ui.style("dim")), (line, tone)])
    if len(rows) > limit:
        rows = rows[:max(1, limit)]
        prefix, (line, attr) = rows[-1][0], rows[-1][1]
        rows[-1] = [prefix, (line[:width - len(PREFIX) - 1] + "…", attr)]
    return rows


def error_rows(error, width, limit):
    lines = ui.wrap(util.ICONS["error"] + " " + error, width)
    if len(lines) > limit:
        lines = lines[:max(1, limit)]
        lines[-1] = lines[-1][:width - 1] + "…"
    return [[(line, ui.style("red"))] for line in lines]


def destination(plan, setup):
    """Where the worktree goes, and the setup command that runs in its new tab, if any."""
    return ["in " + util.tilde(str(plan["path"]))] + (["then runs " + setup] if setup else [])


class NewScreens:
    def new_screen(self, root=False):
        """Pick a branch for a new worktree. Returns True once one is created and opened. root is
        True when the picker opened straight into this screen, so q quits."""
        stdscr = self.stdscr
        keyboard.set_timeout(stdscr, -1)
        cwd = self.cwd
        branches = self.branches()
        setup = worktrees.setup_command(cwd)
        keyboard.set_timeout(stdscr, -1)
        suggestions = branches["suggestions"]
        text, tpos, index, top, filtering, pending = "", 0, 0, 0, False, None
        flash = ui.Flash()
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            # items[0] creates a new branch; the rest are existing branches.
            existing = [s for s in suggestions if matching.fuzzy_all(text, s["name"])]
            if text.strip():
                existing.sort(key=lambda s: -matching.fuzzy_all(text, s["name"]))
            items = [None] + existing
            index = min(index, len(items) - 1)
            plan = branching.plan_branch(cwd, branches, "", items[index]) if index else None
            typed = branching.normalize_branch(text)

            height, _ = stdscr.getmaxyx()
            space = ui.space(stdscr)
            status = flash.rows(space)
            if filtering:
                footer = ui.typing_footer("select")
            else:
                footer = ui.footer(enter="select", move=True, slash="filter", clear_filter=bool(text),
                                   back="quit" if root else "back")
            search, column = ui.search_row(text, tpos, filtering, space)
            heading = [ui.header("New worktree", self.label), search, []]
            room = height - ui.margin(height) - len(heading) - len(status) - 1
            # What enter does, wrapped, with at least three rows left for the list.
            limit = max(1, room - 5 - min(3, max(1, len(existing))))
            if plan is None:
                what = (f"create branch {typed}; choose what it starts from next" if typed
                        else "create a new branch; name it next")
                action = plan_rows([what], space, limit)
            elif plan.get("error"):
                action = error_rows(plan["error"], space, limit)
            else:
                action = plan_rows([plan["action"]] + destination(plan, setup), space, limit)
            capacity = max(1, room - 4 - 1 - len(action))
            list_index = max(0, index - 1)
            top = ui.scroll_top(top, list_index, len(existing), capacity)

            if not keyboard.pending_input(stdscr):
                label = f"+ Create branch “{typed}”…" if typed else "+ Create a new branch…"
                create = [ui.pointer(index == 0), (label, ui.style("green") | (ui.style("bold") if index == 0 else 0))]
                body = [ui.Selected(create) if index == 0 else create, []]
                # The same columns as the worktree list: branch, age, pull request, then where it is.
                # In a narrow pane, the branch column narrows, then rows turn compact: the pull
                # request's number and the source's icon.
                visible = existing[top:top + capacity]
                longest = max([len(s["name"]) for s in visible] + [10])
                ref_width = longest
                prs = [ui.pr_cell({"pr": self.prs.get(s["name"])}) for s in visible]
                sources = [util.ICONS[s["kind"]] + " " + branching.source_label(s) for s in visible]
                pr_width = max([ui.cell_width(c) for c in prs] + [0])
                pr_width = max(pr_width, 2) if pr_width else 0
                rest = 2 + 2 + 5 + (pr_width + 3 if pr_width else 0) + max([len(s) for s in sources] + [4])
                compact = rest + ref_width > space and space - rest < min(ui.MIN_BRANCH, longest)
                if compact:
                    prs = [ui.short_cell(c) for c in prs]
                    sources = [s[:1] for s in sources]
                    pr_width = max([ui.cell_width(c, 1) for c in prs] + [0])
                    ref_width = max(6, min(longest, space - 2 - 2 - 5 - (pr_width + 2 if pr_width else 0) - 1))
                elif rest + ref_width > space:
                    ref_width = space - rest
                gap = 2 if compact else 3
                dim = ui.style("dim") | ui.style("bold")
                heading[1] = ui.with_position(search, list_index, len(existing), capacity, space)
                body.append(ui.section(f"Existing branches ({len(existing)})"))
                header = [("  " + "BRANCH".ljust(ref_width + 2), dim), ("AGE  ", dim)]
                if not compact:
                    header += ([("PR".ljust(pr_width + 3), dim)] if pr_width else []) + [("FROM", dim)]
                body.append(header)
                for i, (item, pr, source) in enumerate(zip(visible, prs, sources), top + 1):
                    hits = set()
                    for token in text.lower().split():
                        hits.update((matching.fuzzy(token, item["name"]) or (0, []))[1])
                    name = item["name"] if len(item["name"]) <= ref_width else item["name"][:ref_width - 1] + "…"
                    row = [ui.pointer(i == index)]
                    row += ui.highlighted(name, {h for h in hits if h < len(name)}, ui.style("bold") if i == index else 0)
                    row += [(" " * (ref_width - len(name) + 2), 0),
                            (ui.short_age(item.get("time")).rjust(3) + "  ", ui.style("dim"))]
                    if pr_width:
                        row += ui.joined(pr, " " if compact else " · ")
                        row.append((" " * (pr_width - ui.cell_width(pr, 1 if compact else 3) + gap), 0))
                    row.append((source, ui.style("blue") if item["kind"] == "remote" else ui.style("dim")))
                    body.append(ui.Selected(row) if i == index else row)
                if not existing:
                    body.append([("  No matching branches" if text.strip() else "  No other branches", ui.style("dim"))])
                body += [[]] * (4 + capacity - len(body))
                bottom = [[]] + action + status + [footer]
                cursor = (ui.margin(height) + 1, column) if column is not None else None
                ui.render(stdscr, ui.compose(height, heading, body, bottom), cursor=cursor)
            key = keyboard.read_key(stdscr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            flash.clear()
            if filtering:
                if key in keyboard.ENTER:
                    filtering = False  # And select the row, below.
                elif key == "\t":
                    filtering = False
                    continue
                elif key == keyboard.ESCAPE:
                    filtering, text, tpos, index = False, "", 0, 0
                    continue
                elif key in keyboard.BACKSPACE and not text:
                    filtering = False
                    continue
                elif key in (curses.KEY_DOWN, "\x0e"):
                    index = min(len(items) - 1, index + 1)
                    continue
                elif key in (curses.KEY_UP, "\x10"):
                    index = max(0, index - 1)
                    continue
                else:
                    new_text, tpos = keyboard.edit_text(text, tpos, key)
                    if new_text != text:
                        # Keep the first match selected while typing.
                        text, index = new_text, 1 if new_text.strip() else 0
                    continue
            if pending == "g" and key not in ("g", "p"):
                pending = None  # An unfinished gg or gp: the next key only cancels it.
                continue
            if pending == "g" and key == "p":
                pending = None
                if index:
                    self.open_pr({"pr": self.prs.get(items[index]["name"])}, flash)
                keyboard.set_timeout(stdscr, flash.wait(-1))
                continue
            new_index, pending = keyboard.motion(key, index, len(items), capacity, pending)
            if new_index is not None:
                index = new_index
            elif key == "/":
                filtering, tpos = True, len(text)
            elif key == "?":
                ui.show_help(stdscr, "New worktree", [("Move", ui.NAVIGATION_HELP), ("Actions", [
                    ("enter", "create a worktree for the selected branch"),
                    ("", "or, on the first row, create a new branch"),
                    ("gp", "open the branch's pull request in the browser"),
                    ("q", "quit" if root else "go back"),
                ]), ("Filter", [
                    ("/", "filter existing branches; typing a letter no key uses starts it too"),
                    ("enter", "select the highlighted row"),
                    ("↑ / ↓", "move while typing"),
                    ("tab", "stop typing and keep the filter"),
                    ("esc", "clear the filter; with no filter, " + ("quit" if root else "go back")),
                ])])
            elif key == keyboard.ESCAPE and text:
                text, tpos, index = "", 0, 0
            elif key in keyboard.CLOSE:
                return False
            elif key in keyboard.ENTER:
                if plan is None:
                    if self.new_branch_screen(cwd, branches, text, setup):
                        return True
                    keyboard.set_timeout(stdscr, -1)
                    continue
                if plan.get("error"):
                    continue  # The action line already shows the error.
                try:
                    self.create_and_open(cwd, plan)
                except RuntimeError as error:
                    if not os.path.isdir(plan["path"]):
                        self.say(str(error), flash=flash)
                        continue
                    raise
                return True
            elif isinstance(key, str) and key.isprintable() and not key.isspace() and key not in NEW_KEYS:
                filtering = True
                text, tpos = keyboard.edit_text(text, len(text), key)
                index = 1 if text.strip() else 0

    def new_branch_screen(self, cwd, branches, name="", setup=""):
        """Name a new branch and choose its base. Returns True once a worktree is created and opened.
        The name is the only input, so typing always goes into it; the arrows choose the base."""
        stdscr = self.stdscr
        keyboard.set_timeout(stdscr, -1)
        bases = branches["bases"]
        text, tpos, base = name, len(name), 0
        flash = ui.Flash()
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            plan = branching.plan_branch(cwd, branches, text, None, bases[base])
            typed = branching.normalize_branch(text)
            height, _ = stdscr.getmaxyx()
            space = ui.space(stdscr)
            visible, column = ui.input_view(text, tpos, space - 7)
            name_row = [("Name   ", ui.style("dim")), (visible, 0) if text else ("type a name", ui.style("dim"))]
            normalized = []
            if typed and typed != text.strip():
                normalized = [[("       → " if i == 0 else "         ", ui.style("dim")), (line, ui.style("bold"))]
                              for i, line in enumerate(ui.wrap(typed, space - 9))]
            base_rows = []
            for i, option in enumerate(bases):
                row = [("Base   " if i == 0 else "       ", ui.style("dim")), ui.pointer(i == base),
                       (option["label"], ui.style("bold") if i == base else 0),
                       ("  current branch" if option["ref"] == "HEAD" else "  default branch", ui.style("dim"), 1)]
                base_rows.append(ui.Selected(row) if i == base else row)
            status = flash.rows(space)
            footer = ui.help_row(*([("↑/↓", "base", 2)] if len(bases) > 1 else []), ("enter", "create"),
                                 ("?", "help", 1), ("esc", "clear" if text else "back"))
            heading = [ui.header("New branch", self.label), []]
            room = height - ui.margin(height) - len(heading) - len(status) - 1
            limit = max(2, room - 1 - len(normalized) - 1 - len(bases) - 1)
            if not text.strip():
                action = [[("Type a name for the new branch; spaces become dashes.", ui.style("dim"))]]
            elif plan.get("error"):
                action = error_rows(plan["error"], space, limit)
            else:
                action = plan_rows([plan["action"]] + destination(plan, setup), space, limit)
            parts = [ui.fixed(name_row), ui.extra(*normalized), ui.GAP, ui.fixed(*base_rows), ui.GAP]
            body = ui.fit_parts(parts, room - len(action))
            rows = ui.compose(height, heading, body, action + status + [footer])
            ui.render(stdscr, rows, cursor=(ui.margin(height) + len(heading), 7 + column))
            key = keyboard.read_key(stdscr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            flash.clear()
            if key in (curses.KEY_DOWN, "\x0e"):
                base = min(len(bases) - 1, base + 1)
            elif key in (curses.KEY_UP, "\x10"):
                base = max(0, base - 1)
            elif key == keyboard.ESCAPE:
                if not text:
                    return False
                text, tpos = "", 0
            elif key == "?":  # Never part of a branch name.
                ui.show_help(stdscr, "New branch", [("Name", [
                    ("typing", "the branch's name; spaces become dashes"),
                    ("ctrl+u / ctrl+w", "delete to the start / the word before the cursor"),
                ]), ("Base", [
                    ("↑ / ↓", "choose what the branch starts from"),
                    ("ctrl+p / ctrl+n", "the same"),
                ]), ("Actions", [
                    ("enter", "create the worktree and open its tab"),
                    ("esc", "clear the name; with no name, go back"),
                ])])
            elif key in keyboard.ENTER:
                if not text.strip():
                    flash.set("Type a name for the new branch first.", "yellow")
                elif not plan.get("error"):
                    try:
                        self.create_and_open(cwd, plan)
                        return True
                    except RuntimeError as error:
                        if os.path.isdir(plan["path"]):
                            raise
                        self.say(str(error), flash=flash)
            else:
                text, tpos = keyboard.edit_text(text, tpos, key)

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
