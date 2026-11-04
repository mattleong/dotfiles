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


PREFIX = "enter: "


def wrap_words(text, width):
    """Wrap at spaces when every word fits, so a branch's name stays whole; else at slashes too."""
    return ui.wrap(text, width) if all(len(w) <= width for w in text.split()) else ui.wrap_path(text, width)


def plan_lines(lines, width, limit, short=None):
    """Fit sentences, wrapped, in at most limit rows. The first, which names the branch, keeps
    the most: the others shorten to a row each first, last first: a folder cut in the middle, so
    it keeps its name, and a command (a sentence given as (text, "end")) cut at its end; then the
    first turns into short, if given, and then loses its middle too; then the last sentences drop.
    Returns (the lines, whether anything was cut)."""
    limit = max(1, limit)
    ends = [isinstance(line, tuple) for line in lines]
    lines = [line[0] if end else line for line, end in zip(lines, ends)]
    original = list(lines)
    wrapped = [wrap_words(text, width) for text in lines]

    def total():
        return sum(len(w) for w in wrapped)

    for i in reversed(range(1, len(wrapped))):
        if total() > limit and len(wrapped[i]) > 1:
            text = lines[i]
            wrapped[i] = [text[:width - 1] + "…" if ends[i] else ui.middle(text, width)[0]]
    while total() > limit and len(wrapped) > 1 and len(wrapped) > limit:
        wrapped.pop()
    keep = limit - (total() - len(wrapped[0]))
    if len(wrapped[0]) > keep and short:
        lines[0] = short
        wrapped[0] = wrap_words(short, width)
    if len(wrapped[0]) > keep:
        pos = 0
        for line in wrapped[0][:keep - 1]:
            pos = lines[0].index(line, pos) + len(line)
        wrapped[0] = wrapped[0][:keep - 1] + [ui.middle(lines[0][pos:].lstrip(), width)[0]]
    out = [line for w in wrapped for line in w]
    return out, out != [line for text in original for line in wrap_words(text, width)]


def plan_rows(lines, width, limit, short=None):
    """What enter does, beside "enter:", in at most limit rows (see plan_lines). Returns (the rows,
    whether anything was cut)."""
    fitted, cut = plan_lines(lines, width - len(PREFIX), limit, short)
    return [[(PREFIX if i == 0 else " " * len(PREFIX), ui.style("dim")), (line, 0)]
            for i, line in enumerate(fitted)], cut


def error_rows(error, width, limit):
    lines = ui.wrap(util.ICONS["error"] + " " + error, width)
    if len(lines) > limit:
        lines = lines[:max(1, limit)]
        lines[-1] = lines[-1][:width - 1] + "…"
    return [[(line, ui.style("red"))] for line in lines]


def destination(plan, setup):
    """Where the worktree goes, and the setup command that runs in its new tab, if any."""
    return ["in " + util.tilde(str(plan["path"]))] + ([("then runs " + setup, "end")] if setup else [])


TYPING_HELP = [
    ("ctrl+a / ctrl+e", "the start / end of the text; ← / → move the cursor"),
    ("ctrl+u / ctrl+w", "delete to the start / the word before the cursor"),
]


class NewScreens:
    def new_screen(self, root=False, text=""):
        """Pick a branch for a new worktree, or type a new branch's name. Every printable key types
        into the filter, so typing a name never runs a command; the list moves with the arrows.
        The first row, which creates a branch, stays selected while typing unless the text surely
        means an existing branch (see matching.strong). Returns True once one is created and
        opened. root is True when the picker opened straight into this screen, so esc quits. text
        starts the filter, such as with the list's filter that found no worktrees."""
        stdscr = self.stdscr
        keyboard.set_timeout(stdscr, -1)
        cwd = self.cwd
        branches = self.branches()
        setup = worktrees.setup_command(cwd)
        suggestions = branches["suggestions"]
        tpos, index, top = len(text), None, 0  # index None: choose once the list is filtered.
        plans = {}  # New branches' plans by name, since checking a name runs Git.
        back = "quit" if root else "back"
        flash = ui.Flash()
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            # items[0] creates a new branch; the rest are existing branches.
            existing = [s for s in suggestions if matching.fuzzy_all(text, s["name"])]
            if text.strip():
                existing.sort(key=lambda s: -matching.fuzzy_all(text, s["name"]))
            items = [None] + existing
            if index is None:
                index = next((i for i, s in enumerate(existing, 1) if matching.strong(text, s["name"])), 0)
            index = min(index, len(items) - 1)
            typed = branching.normalize_branch(text)
            create = None  # The plan for a new branch named by the text.
            if typed:
                if typed not in plans:
                    plans[typed] = branching.plan_branch(cwd, branches, text)
                create = plans[typed]
            plan = branching.plan_branch(cwd, branches, "", items[index]) if index else create

            height, _ = stdscr.getmaxyx()
            space = ui.space(stdscr)
            status = flash.rows(space)
            # Why the typed name can't be a new branch, such as that it's checked out already, right
            # below the row that would create it, whichever row is selected.
            note = []
            if create and create.get("error"):
                note = [[("  " + line, ui.style("red"))]
                        for line in ui.wrap(util.ICONS["error"] + " " + create["error"], space - 2)]
                if len(note) > 2:
                    note = note[:2]
                    note[-1] = [(note[-1][0][0][:space - 1] + "…", ui.style("red"))]
            search, column = ui.search_row(text, tpos, True, space, "filter, or name a new branch")
            heading = [ui.header("New worktree", self.label), search, []]
            room = height - ui.margin(height) - len(heading) - len(status) - 1
            # Above the list: the create row and its note, a blank row, the heading, and the column titles.
            fixed = 4 + len(note)
            # What enter does, wrapped, below the list, with at least two of the list's rows left.
            limit = max(1, room - fixed - 1 - min(2, max(1, len(existing))))
            if plan is None:
                lines = ["create a new branch; name it next"]
            elif plan.get("error"):
                lines = []  # Said below the create row, or for an existing branch, just below.
            elif not index:
                lines = [f"create branch {typed}; choose what it starts from next"]
            else:
                lines = [plan["action"], *destination(plan, setup)]
            if index and plan.get("error"):
                action, cut = error_rows(plan["error"], space, limit), False
            else:
                action, cut = plan_rows(lines, space, limit, plan.get("short") if plan else None) if lines else ([], False)
            footer = ui.help_row(("enter", "select"), ("↑/↓", "move", 3), *([("tab", "details", 2)] if cut else []),
                                 ("?", "help", 1), ("esc", "clear" if text else back))
            # The list's rows, sized to every branch so the plan below stays put while filtering.
            # The blank row above the plan goes first when they're short of rows.
            capacity = room - fixed - 1 - len(action)
            gap = capacity >= min(3, max(1, len(suggestions)))
            capacity = max(1, min(max(1, len(suggestions)), capacity + (0 if gap else 1)))
            list_index = max(0, index - 1)
            top = ui.scroll_top(top, list_index, len(existing), capacity)

            if not keyboard.pending_input(stdscr):
                label = f"+ Create branch “{typed}”…" if typed else "+ Create a new branch…"
                row = [ui.pointer(index == 0), (label, ui.style("green") | (ui.style("bold") if index == 0 else 0))]
                body = [ui.Selected(row) if index == 0 else row] + note + [[]]
                body += self.branch_rows(existing[top:top + capacity], top, index, text, space, len(existing))
                if not existing:
                    body.append([("  No matching branches" if text.strip() else "  No other branches", ui.style("dim"))])
                body += [[]] * (fixed + capacity - len(body)) + ([[]] if gap else []) + action
                heading[1] = ui.with_position(search, list_index, len(existing), capacity, space)
                cursor = (ui.margin(height) + 1, column) if column is not None else None
                ui.render(stdscr, ui.compose(height, heading, body, status + [footer]), cursor=cursor)
            key = keyboard.read_key(stdscr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            flash.clear()
            new_index = keyboard.typing_motion(key, index, len(items), capacity)
            if new_index is not None:
                index = new_index
            elif key == keyboard.ESCAPE:
                if not text:
                    return False
                text, tpos, index = "", 0, 0
            elif key == "\t":  # Never part of a branch name.
                if lines:
                    ui.page(stdscr, "New worktree — details",
                            lambda width: [[]] + plan_rows(lines, width, 1000)[0])
            elif key == "?":  # Never part of a branch name.
                self.new_help(root)
            elif key == "\x07":
                if index:
                    self.open_pr({"pr": self.prs.get(items[index]["name"])}, flash)
                else:
                    flash.set("Select an existing branch to open its pull request.", "dim")
                keyboard.set_timeout(stdscr, flash.wait(-1))
            elif key in keyboard.ENTER:
                if plan is None or (not index and not plan.get("error")):
                    if self.new_branch_screen(cwd, branches, text, setup):
                        return True
                    keyboard.set_timeout(stdscr, -1)
                    continue
                if plan.get("error"):
                    continue  # The note or the plan's rows already show the error.
                try:
                    self.create_and_open(cwd, plan)
                except RuntimeError as error:
                    if not os.path.isdir(plan["path"]):
                        self.say(str(error), flash=flash)
                        continue
                    raise
                return True
            else:
                new_text, tpos = keyboard.edit_text(text, tpos, key)
                if new_text != text:
                    text, index = new_text, None

    def new_help(self, root):
        ui.show_help(self.stdscr, "New worktree", [("Keys", [
            ("typing", "filter the existing branches, or name a new branch; the first row stays "
                       "selected unless what you type is a branch's name or the start of one of its words"),
            ("↑ / ↓", "move; also ctrl+p / ctrl+n"),
            ("pgup / pgdn", "a page up / down"),
            ("home / end", "the first / last row"),
            ("enter", "create a worktree for the selected branch; on the first row, name a new "
                      "branch and choose what it starts from"),
            ("tab", "what enter does, in full: the branch, its folder, and the setup command"),
            ("ctrl+g", "open the selected branch's pull request in the browser"),
            ("esc", "clear the text; with none, " + ("quit" if root else "go back")),
            ("ctrl+c", "quit"),
        ]), ("Editing", TYPING_HELP), ("Labels", [
            ("3d", "time since the branch's last commit"),
            (util.ICONS["pr"] + " #12 open", "its pull request: open, draft, merged, or closed"),
            (util.ICONS["local"] + " local", "a branch in this repository"),
            (util.ICONS["remote"] + " remote", "only on a remote; enter creates a local branch tracking it"),
            ("narrow panes", "only the icons show, and the branch is cut short; the plan below the "
                             "list names the selected branch in full"),
        ])])

    def branch_rows(self, visible, top, index, text, space, count):
        """The existing branches' heading, column titles, and rows: the same columns as the
        worktree list: branch, age, pull request, then where it is. In a narrow pane, the branch
        column narrows, the pull requests' labels shorten, then rows turn compact: the pull
        request's number and the source's icon."""
        longest = max([len(s["name"]) for s in visible] + [10])
        sources = [util.ICONS[s["kind"]] + " " + branching.source_label(s) for s in visible]
        source_width = max([len(s) for s in sources] + [4])

        def layout(short):
            prs = [ui.pr_cell({"pr": self.prs.get(s["name"])}, short) for s in visible]
            pr_width = max([ui.cell_width(c) for c in prs] + [0])
            pr_width = max(pr_width, 2) if pr_width else 0
            return prs, pr_width, 2 + 2 + 5 + (pr_width + 3 if pr_width else 0) + source_width

        prs, pr_width, rest = layout(False)
        if rest + longest > space:
            prs, pr_width, rest = layout(True)
        ref_width = min(longest, space - rest)
        compact = ref_width < min(ui.MIN_BRANCH, longest)
        if compact:
            prs = [ui.short_cell(ui.pr_cell({"pr": self.prs.get(s["name"])})) for s in visible]
            sources = [s[:1] for s in sources]
            pr_width = max([ui.cell_width(c, 1) for c in prs] + [0])
            ref_width = max(6, min(longest, space - 2 - 2 - 5 - (pr_width + 2 if pr_width else 0) - 1))
        gap = 2 if compact else 3
        dim = ui.style("dim") | ui.style("bold")
        rows = [ui.section(f"Existing branches ({count})")]
        header = [("  " + "BRANCH".ljust(ref_width + 2), dim), ("AGE  ", dim)]
        if not compact:
            header += ([("PR".ljust(pr_width + 3), dim)] if pr_width else []) + [("FROM", dim)]
        if visible:  # No column titles over an empty list.
            rows.append(header)
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
            rows.append(ui.Selected(row) if i == index else row)
        return rows

    def new_branch_screen(self, cwd, branches, name="", setup=""):
        """Name a new branch and choose its base. Returns True once a worktree is created and opened.
        The name is the only input, so typing always goes into it; the arrows choose the base."""
        stdscr = self.stdscr
        keyboard.set_timeout(stdscr, -1)
        bases = branches["bases"]
        text, tpos, base, nudge = name, len(name), 0, False
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
                              for i, line in enumerate(ui.wrap_path(typed, space - 9))]
            base_rows = []
            for i, option in enumerate(bases):
                row = [("Base   " if i == 0 else "       ", ui.style("dim")), ui.pointer(i == base),
                       (option["label"], ui.style("bold") if i == base else 0),
                       ("  current branch" if option["ref"] == "HEAD" else "  default branch", ui.style("dim"), 1)]
                base_rows.append(ui.Selected(row) if i == base else row)
            status = flash.rows(space)
            heading = [ui.header("New branch", self.label), []]
            room = height - ui.margin(height) - len(heading) - len(status) - 1
            # The plan, right below the bases, keeps every row but the name's and the bases'.
            limit = max(2, room - 1 - len(bases))
            details, cut = [], False  # What tab shows: the plan in full, with the branch's name.
            if not text.strip():
                hint = "Type a name; spaces become dashes."
                action = [[(line, ui.style("yellow") if nudge else ui.style("dim"))] for line in ui.wrap(hint, space)]
            elif plan.get("error"):
                action = error_rows(plan["error"], space, limit)
            else:
                action, cut = plan_rows([plan["action"]] + destination(plan, setup), space, limit)
                details = [f"create the branch {plan['branch']} from {bases[base]['label']}"] + destination(plan, setup)
            parts = [ui.fixed(name_row), ui.extra(*normalized), ui.GAP, ui.fixed(*base_rows), ui.GAP,
                     ui.fixed(*action)]
            body = ui.fit_parts(parts, room)
            cut = cut or (bool(normalized) and normalized[0] not in body)
            footer = ui.help_row(*([("↑/↓", "base", 3)] if len(bases) > 1 else []), ("enter", "create"),
                                 *([("tab", "details", 2)] if cut else []), ("?", "help", 1),
                                 ("esc", "clear" if text else "back"))
            rows = ui.compose(height, heading, body, status + [footer])
            ui.render(stdscr, rows, cursor=(ui.margin(height) + len(heading), 7 + column))
            key = keyboard.read_key(stdscr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            flash.clear()
            nudge = False
            if key in (curses.KEY_DOWN, "\x0e"):
                base = min(len(bases) - 1, base + 1)
            elif key in (curses.KEY_UP, "\x10"):
                base = max(0, base - 1)
            elif key == keyboard.ESCAPE:
                if not text:
                    return False
                text, tpos = "", 0
            elif key == "\t":  # Never part of a branch name.
                if details:
                    ui.page(stdscr, "New branch — details",
                            lambda width: [[]] + plan_rows(details, width, 1000)[0])
            elif key == "?":  # Never part of a branch name.
                ui.show_help(stdscr, "New branch", [("Keys", [
                    ("typing", "the branch's name; spaces become dashes"),
                    ("↑ / ↓", "choose what the branch starts from; also ctrl+p / ctrl+n"),
                    ("enter", "create the worktree and open its tab"),
                    ("tab", "what enter does, in full: the branch, its folder, and the setup command"),
                    ("esc", "clear the name; with no name, go back"),
                    ("ctrl+c", "quit"),
                ]), ("Editing", [("home / end", "the start / end of the name")] + TYPING_HELP)])
            elif key in keyboard.ENTER:
                if not text.strip():
                    nudge = True  # The hint turns yellow.
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
