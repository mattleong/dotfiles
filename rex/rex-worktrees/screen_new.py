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


def plan_lines(lines, width, limit, short=None):
    """Fit sentences, wrapped, in at most limit rows. The first, which names the branch, keeps
    the most: the others shorten to a row each first, last first: a folder (a sentence given as
    (text, "path"), which wraps after its slashes) cut in the middle, so it keeps its name, and a
    command (given as (text, "end")) cut at its end; then the first turns into short, if given,
    and then loses its middle too; then the last sentences drop. Returns (the lines, whether
    anything was cut)."""
    limit = max(1, limit)
    kinds = [line[1] if isinstance(line, tuple) else "" for line in lines]
    lines = [line[0] if isinstance(line, tuple) else line for line in lines]
    ends = [kind == "end" for kind in kinds]
    wraps = [ui.wrap_path if kind == "path" else ui.wrap for kind in kinds]
    original = list(lines)
    wrapped = [wrap(text, width) for wrap, text in zip(wraps, lines)]

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
        wrapped[0] = ui.wrap(short, width)
    if len(wrapped[0]) > keep:
        pos = 0
        for line in wrapped[0][:keep - 1]:
            found = lines[0].find(line, pos)  # Not found only where wrapping turned a tab into spaces.
            pos = (found if found >= 0 else pos) + len(line)
        wrapped[0] = wrapped[0][:keep - 1] + [ui.middle(lines[0][pos:].lstrip(), width)[0]]
    out = [line for w in wrapped for line in w]
    return out, out != [line for wrap, text in zip(wraps, original) for line in wrap(text, width)]


TAB_CUE = ui.TAB_CUE
MIN_BRANCH = 20  # The branch column keeps this much before the rows turn compact (see branch_rows).
MIN_SOURCE = 10  # A remote's name in the FROM column keeps this much, with its icon.


def plan_rows(lines, width, limit, short=None, cue=False):
    """What enter does, beside "enter:", in at most limit rows (see plan_lines). With cue, a plan
    cut to fit ends in a dim hint that tab shows it all, for when the footer has no room to say so.
    Returns (the rows, whether anything was cut)."""
    fitted, cut = plan_lines(lines, width - len(PREFIX), limit, short)
    rows = [[(PREFIX if i == 0 else " " * len(PREFIX), ui.style("dim")), (line, 0)] for i, line in enumerate(fitted)]
    if cut and cue:
        room = width - len(PREFIX) - len(TAB_CUE)
        last = fitted[-1] if len(fitted[-1]) <= room else fitted[-1][:room - 1].rstrip() + "…"
        rows[-1] = rows[-1][:1] + [(last, 0), (TAB_CUE, ui.style("dim"))]
    return rows, cut


def note_rows(text, icon, tone, width, nudge=False, cue=False, limit=2):
    """A note below the create row, in up to limit rows. nudge, when enter was pressed but can't
    go anywhere, draws attention to it: a dim note turns yellow, and the note bold. With cue, a
    note cut to fit ends in a dim hint that tab shows it all, for when the footer has no room to
    say so."""
    tone = "yellow" if nudge and tone == "dim" else tone
    attr = ui.style(tone) | (ui.style("bold") if nudge else 0)
    lines = ui.clip(ui.hang(util.ICONS[icon], text, width - 2), limit, width - 2)
    rows = [[("  " + line, attr)] for line in lines]
    if cue and note_cut(text, icon, width):
        last = lines[-1][:width - 2 - len(TAB_CUE) - 1].rstrip() + "…"
        rows[-1] = [("  " + last, attr), (TAB_CUE, ui.style("dim"))]
    return rows


def note_cut(text, icon, width):
    """Whether note_rows cuts the note short."""
    return len(ui.hang(util.ICONS[icon], text, width - 2)) > 2


def error_rows(error, width, limit, nudge=False):
    attr = ui.style("red") | (ui.style("bold") if nudge else 0)
    return [[(line, attr)] for line in ui.clip(ui.hang(util.ICONS["error"], error, width), limit, width)]


def destination(plan, setup):
    """Where the worktree goes, and the setup command that runs in its new tab, if any."""
    return [("in " + util.tilde(str(plan["path"])), "path")] + ([("then runs " + setup, "end")] if setup else [])


def tracks_note(tracks):
    """For text such as fork/x that means the local branch x, at another commit than fork's: that
    they differ first, so it shows however long the remotes' names are, then what x tracks instead
    (see branching.other_upstream)."""
    upstream, remote = tracks
    return (f"{remote}'s version differs; the local branch tracks {upstream}." if upstream
            else f"{remote}'s version differs from the local branch.")


def exists_note(create):
    """Below the create row, when the text names the selected branch."""
    if not create.get("tracks"):
        return "This branch exists; enter checks it out."
    upstream, remote = create["tracks"]
    return f"{remote}'s version differs; enter checks out the local branch" + (
        f", which tracks {upstream}." if upstream else ".")


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
        moved = False
        plans = {}  # New branches' plans by name, since checking a name runs Git.
        back = "quit" if root else "back"
        flash = ui.Flash()
        nudge = False  # Enter was pressed where it can't go anywhere: the reason stands out.
        named = branching.named_remotes(suggestions)
        preferred = branches.get("preferred", "origin")
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            # items[0] creates a new branch; the rest are existing branches. Text such as origin/fix
            # matches the branches on origin by the rest, fix, as git branch -r lists them.
            # Other remotes' branches never match it, even ones with the same name.
            queries = {id(s): branching.query_for(text, s, branches) for s in suggestions}
            scores = {id(s): matching.fuzzy_all(q, s["name"]) if q is not None else 0
                      for s, q in ((s, queries[id(s)]) for s in suggestions)}
            existing = [s for s in suggestions if scores[id(s)]]
            if text.strip():
                # A branch the text names in full goes first, above longer names that start with it;
                # fix/a just as typed goes above fix-a. Of the same name on several remotes, the one
                # Git's checkout picks goes first (see branching.load_branches).
                existing.sort(key=lambda s: (not matching.exact(queries[id(s)], s["name"]),
                                             queries[id(s)].strip() != s["name"], -scores[id(s)],
                                             s["kind"] == "remote" and branching.remote_of(s) != preferred))
            items = [None] + existing
            typed = branching.normalize_branch(text)
            create = None  # The plan for a new branch named by the text.
            if typed:
                key = (typed, text.rstrip().endswith("/"))  # origin/ lists origin's branches; origin doesn't.
                if key not in plans:
                    plans[key] = branching.plan_branch(cwd, branches, text)
                create = plans[key]
            if index is None:
                # A branch that's checked out isn't listed: the create row, which opens it, stays
                # selected, rather than a longer name that starts with it.
                checked_out = bool(create and (create.get("open") or create.get("missing")))
                index = 0 if checked_out else next(
                    (i for i, s in enumerate(existing, 1) if matching.strong(queries[id(s)], s["name"])), 0)
                moved = bool(index)  # Off the create row, by what was typed.
            index = min(index, len(items) - 1)
            plan = branching.plan_branch(cwd, branches, "", items[index]) if index else create

            height, _ = stdscr.getmaxyx()
            space = ui.space(stdscr)
            status = flash.rows(space)
            # Why the typed name can't be a new branch, right below the row that would create it,
            # whichever row is selected: in red, unless enter goes to the branch it names instead.
            # Text such as origin/fi, partway to a remote branch, needs no note once one is selected.
            # Enter can't go anywhere from a row whose plan has an error, except to open the worktree
            # a branch is checked out in.
            stuck = bool(plan and plan.get("error")) and not (not index and plan.get("open"))
            said = None  # The note: (its text, icon, tone, and whether enter was pressed to no avail).
            qualifies = False  # Whether the note is about another remote's version, which qualifies the plan.
            if create and create.get("error") and not (index and create.get("remote")):
                if index and items[index]["ref"] in create.get("exists", ()):
                    said, qualifies = (exists_note(create), "info", "dim", False), bool(create.get("tracks"))
                elif create.get("open"):
                    # Selected, the row and the plan say it; where it is goes in the plan, which tab
                    # shows in full.
                    if index:
                        said = (create["error"] + ".", "info", "dim", False)
                    elif create.get("tracks"):
                        said, qualifies = (tracks_note(create["tracks"]), "info", "dim", False), True
                elif create.get("hint"):  # A remote's name, on the way to origin/name.
                    said = (create["error"], "info", "dim", nudge and not index)
                else:
                    said = (create["error"], "error", "red", nudge and not index)
            note = note_rows(*said[:3], space, said[3]) if said else []
            # Tab shows a note cut to fit in full, and always the one about another remote's
            # version, which qualifies what enter does.
            note_hidden = bool(said) and note_cut(said[0], said[1], space)
            noted = note_hidden or qualifies
            search, column = ui.search_row(text, tpos, True, space, "filter, or name a new branch")
            heading = [ui.header("New worktree", self.label), search, []]
            room = height - ui.margin(height) - len(heading) - len(status) - 1
            # Above the list: the create row and its note, a blank row, the heading, and the column titles.
            fixed = 4 + len(note)
            # What enter does, wrapped, below the list, with at least two of the list's rows left.
            limit = max(1, room - fixed - 1 - min(2, max(1, len(existing))))
            if plan is None:
                lines = ["create a new branch; name it next"]
            elif not index and plan.get("open"):
                lines = [f"open {plan['branch']}'s worktree", ("at " + util.tilde(plan["open"]), "path")]
            elif plan.get("error"):
                lines = []  # Said below the create row, or for an existing branch, just below.
            elif not index:
                lines = [f"create branch {typed}; choose what it starts from next"]
            else:
                lines = [plan["action"], *destination(plan, setup)]
            short = plan.get("short") if plan else None
            if index and plan.get("error"):
                action, cut = error_rows(plan["error"], space, limit, nudge), False
            else:
                action, cut = plan_rows(lines, space, limit, short) if lines else ([], False)
            # Once typing selected a branch, ↑ is the way back to the create row, so it outlasts tab.
            moved = moved and bool(index)
            footer = ui.help_row(*([] if stuck else [("enter", "open" if not index and plan and plan.get("open")
                                                      else "select")]),
                                 *([("↑/↓", "move", 2 if moved else 3)] if existing else []),
                                 *([("tab", "details", 3 if moved else 2)] if cut or note_hidden else []),
                                 ("?", "help", 1), ("esc", "clear" if text else back))
            if (cut or note_hidden) and "tab" not in [t for t, _ in ui.fit(footer, space)]:
                # The footer had no room for tab: what's cut says so itself.
                if cut:
                    action = plan_rows(lines, space, limit, short, cue=True)[0]
                else:
                    note = note_rows(*said[:3], space, said[3], cue=True)
            # The list's rows, sized to every branch so the plan below stays put while filtering.
            # The blank row above the plan goes first when they're short of rows.
            capacity = room - fixed - 1 - len(action)
            gap = capacity >= min(3, max(1, len(suggestions)))
            capacity = max(1, min(max(1, len(suggestions)), capacity + (0 if gap else 1)))
            list_index = max(0, index - 1)
            top = ui.scroll_top(top, list_index, len(existing), capacity)

            if not keyboard.pending_input(stdscr):
                # Dim, with no “…” for a next step, when the text can't be a new branch; for a
                # branch that's checked out, it opens the worktree it's in.
                possible = not (create and create.get("error"))
                if create and create.get("open"):
                    label, possible = f"→ Open the worktree for “{create['branch']}”", True
                elif create and (create.get("hint") or create.get("missing")
                                 or (not possible and branching.split_remote(text, branches))):
                    # A remote's name, text such as origin/x, or a branch checked out in a missing
                    # folder: no branch to name.
                    label = "+ Create a new branch"
                else:
                    label = f"+ Create branch “{typed}”" if typed else "+ Create a new branch"
                    label += "…" if possible else ""
                row = [ui.pointer(index == 0), (label, (ui.style("green") if possible else ui.style("dim"))
                                                | (ui.style("bold") if index == 0 else 0))]
                body = [ui.Selected(row) if index == 0 else row] + note + [[]]
                visible = existing[top:top + capacity]
                body += self.branch_rows(visible, top, index, [queries[id(s)] for s in visible], space, len(existing),
                                         named, existing)
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
            nudge = False
            new_index = keyboard.typing_motion(key, index, len(items), capacity)
            if new_index is not None:
                index, moved = new_index, False
            elif key == keyboard.ESCAPE:
                if not text:
                    return False
                text, tpos, index = "", 0, 0
            elif key == "\t":  # Never part of a branch name.
                if lines or noted:
                    ui.page(stdscr, "New worktree — details", lambda width: [[]] + (
                        plan_rows(lines, width, 1000)[0] if lines else []) + (
                        ([[]] if lines else []) + note_rows(*said[:3], width, limit=1000)
                        if noted else []))
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
                if not index and plan.get("open"):
                    if self.open_checked_out(plan, flash):
                        return True
                    continue
                if plan.get("error"):
                    nudge = True  # The note or the plan's rows say why; they stand out.
                    continue
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
            ("typing", "filter the existing branches, or name a new branch"),
            ("first row", "stays selected while typing, unless the text is a branch's name or the "
                          "start of one of its words"),
            ("whole name", "wins over longer names that start with it"),
            ("origin/name", "finds name among origin's branches only"),
            ("↑ / ↓", "move; also ctrl+p / ctrl+n"),
            ("pgup / pgdn", "a page up / down"),
            ("home / end", "the first / last row"),
            ("enter", "create a worktree for the selected branch; on the first row, name a new "
                      "branch and choose what it starts from, or for a branch that's checked out "
                      "already, open its worktree"),
            ("tab", "what enter does, in full: the branch, its folder, and the setup command, and "
                    "a note that's cut short"),
            ("ctrl+g", "open the selected branch's pull request in the browser"),
            ("esc", "clear the text; with none, " + ("quit" if root else "go back")),
            ("ctrl+c", "quit"),
        ]), ("Editing", TYPING_HELP), ("Labels", [
            ("3d", "time since the branch's last commit"),
            (util.ICONS["pr"] + " #12 open", "its pull request: open, draft, merged, or closed"),
            (util.ICONS["local"] + " local", "a branch in this repository"),
            (util.ICONS["remote"] + " remote", "only on a remote; enter creates a local branch tracking it"),
            (util.ICONS["remote"] + " fork", "the remote's name, when it's not origin or there's more than one; "
                                            "fork/ lists only its branches"),
            ("narrow panes", "pull requests show just their number, then only the icons show, and the "
                             "branch is cut short; the plan below the "
                             "list names the selected branch in full"),
        ])])

    def branch_rows(self, visible, top, index, queries, space, count, named=False, measured=None):
        """The existing branches' heading, column titles, and rows: the same columns as the
        worktree list: branch, age, pull request, then where it is. queries holds the text each
        visible branch matched, to highlight; named labels every remote by its name (see
        branching.named_remotes). The columns are sized to measured, every branch that matches,
        so they stay put while the list scrolls. In a narrow pane, the branch column narrows, the
        pull requests' labels shorten, then lose their state, then a long remote's name is cut
        short, where the rows would otherwise turn compact: the pull request's number and the
        source's icon. The branch narrows further here than in the worktree list, since the plan
        below names the selected one in full."""
        measured = visible if measured is None else measured
        longest = max([len(s["name"]) for s in measured] + [10])
        sources = [util.ICONS[s["kind"]] + " " + branching.source_label(s, named) for s in measured]
        source_width = max([len(s) for s in sources] + [4])
        least = min(MIN_BRANCH, longest)  # The branch keeps this much before rows turn compact.

        def layout(short):
            prs = [ui.pr_cell({"pr": self.prs.get(s["name"])}, short > 0) for s in measured]
            if short > 1:  # Just the number, such as #12.
                prs = [ui.short_cell(c) for c in prs]
            pr_width = max([ui.cell_width(c) for c in prs] + [0])
            pr_width = max(pr_width, 2) if pr_width else 0
            return prs, pr_width, 2 + 2 + 5 + (pr_width + 3 if pr_width else 0) + source_width

        prs, pr_width, rest = layout(0)
        if rest + longest > space:
            prs, pr_width, rest = layout(1)
        if space - rest < least:  # Rather than turn compact, losing the source.
            prs, pr_width, rest = layout(2)
        if space - rest < least and source_width > MIN_SOURCE:
            # A long remote's name is cut short, keeping its start, where names differ most, rather
            # than every row losing its source.
            source_width = max(MIN_SOURCE, source_width - (least - (space - rest)))
            sources = [s if len(s) <= source_width else s[:source_width - 1] + "…" for s in sources]
            prs, pr_width, rest = layout(2)
        ref_width = min(longest, space - rest)
        compact = ref_width < least
        if compact:
            prs = [ui.short_cell(ui.pr_cell({"pr": self.prs.get(s["name"])})) for s in measured]
            sources = [s[:1] for s in sources]
            pr_width = max([ui.cell_width(c, 1) for c in prs] + [0])
            ref_width = max(6, min(longest, space - 2 - 2 - 5 - (pr_width + 2 if pr_width else 0) - 1))
        shown = {id(s): i for i, s in enumerate(measured)}
        prs = [prs[shown[id(s)]] for s in visible]
        sources = [sources[shown[id(s)]] for s in visible]
        gap = 2 if compact else 3
        dim = ui.style("dim") | ui.style("bold")
        rows = [ui.section(f"Existing branches ({count})")]
        header = [("  " + "BRANCH".ljust(ref_width + 2), dim), ("AGE  ", dim)]
        if not compact:
            header += ([("PR".ljust(pr_width + 3), dim)] if pr_width else []) + [("FROM", dim)]
        if visible:  # No column titles over an empty list.
            rows.append(header)
        for i, (item, pr, source, query) in enumerate(zip(visible, prs, sources, queries), top + 1):
            hits = set()
            for token in query.lower().split():
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

    def open_checked_out(self, plan, flash):
        """Open the worktree a typed branch is checked out in. Returns True once it's open."""
        path = plan["open"]
        entry = next((e for e in self.entries if e["worktree"] == path),
                     {"worktree": path, "name": plan["branch"], "branch": "refs/heads/" + plan["branch"]})
        if worktrees.missing(entry):
            flash.set("That worktree's folder is missing; prune it with p in the list.", "yellow")
            return False
        try:
            self.busy("Opening…", lambda: tabs.open_tab(self.session, entry, self.client, terminals=self.terminals))
        except (RuntimeError, OSError) as error:
            self.say(str(error), flash=flash)
            return False
        return True

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
