"""Creating worktrees: for an existing branch, or a new one named by the text typed."""
import curses

import branching
import keyboard
import matching
import tabs
import ui
import util
import worktrees


# The input's prompt. Not "/", which is the key that starts the list's filter: here every key types.
PROMPT = "Branch  "
PLACEHOLDER = "name a new branch, or filter the list"

# The plan below the list says what enter does, a sentence to a row, each after a dim label in a
# column, such as "Creates     fix-login from origin/main". The column fits the longest label
# any plan uses, so it stays put as the selection moves. A pane narrower than PLAN_INLINE puts each
# label at the start of its sentence instead, leaving the sentences room.
PLAN_LABELS = ("Creates", "Checks out", "Opens", "In", "Then runs")
PLAN_COLUMN = max(len(label) for label in PLAN_LABELS) + 2
PLAN_INLINE = 40
TAB_CUE = ui.TAB_CUE
MIN_BRANCH = 20  # The branch column keeps this much before the rows turn compact (see branch_rows).
MIN_SOURCE = 10  # A remote's name in the FROM column keeps this much, with its icon.
MIN_NAME = 8  # A new branch's name in its create row keeps this much before the row is cut at its end.


def plan_groups(lines, width, limit, short=None):
    """Fit sentences, wrapped, in at most limit rows. The first, which names the branch, keeps
    the most: the others shorten to a row each first, last first: a folder (a sentence given as
    (text, "path"), which wraps after its slashes) cut in the middle, so it keeps its name, and a
    command (given as (text, "end")) cut at its end; then the first turns into short, if given,
    and then loses its middle too; then the last sentences drop. Returns (each sentence's lines,
    whether anything was cut)."""
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
    full = [line for wrap, text in zip(wraps, original) for line in wrap(text, width)]
    return wrapped, [line for w in wrapped for line in w] != full


def plan_lines(lines, width, limit, short=None):
    """The sentences fitted as by plan_groups, as one list of lines. Returns (the lines, whether
    anything was cut)."""
    groups, cut = plan_groups(lines, width, limit, short)
    return [line for group in groups for line in group], cut


def plan_rows(lines, width, limit, short=None, cue=False):
    """What enter does, in at most limit rows (see plan_groups): lines are (label, sentence) or
    (label, sentence, kind), each sentence after its label, dim, in a column (see PLAN_COLUMN).
    short is a shorter first sentence. With cue, a plan cut to fit ends in a dim hint that tab
    shows it all, for when the footer has no room to say so. Returns (the rows, whether anything
    was cut)."""
    column = PLAN_COLUMN if width >= PLAN_INLINE else 0
    sentences = []
    for label, text, *kind in lines:
        text = text if column else f"{label} {text}"
        sentences.append((text, kind[0]) if kind else text)
    if short and not column:
        short = f"{lines[0][0]} {short}"
    groups, cut = plan_groups(sentences, width - column, limit, short)
    dim = ui.style("dim")
    rows = []
    for (label, *_), group in zip(lines, groups):
        for i, line in enumerate(group):
            if column:
                rows.append([((label if i == 0 else "").ljust(column), dim), (line, 0)])
            elif i == 0 and line.startswith(label + " "):
                rows.append([(label, dim), (line[len(label):], 0)])
            else:
                rows.append([(line, 0)])
    if cut and cue and rows:
        room = width - sum(len(text) for text, _ in rows[-1][:-1]) - len(TAB_CUE)
        last = rows[-1][-1][0]
        last = last if len(last) <= room else last[:max(0, room - 1)].rstrip() + "…"
        rows[-1] = rows[-1][:-1] + [(last, 0), (TAB_CUE, dim)]
    return rows, cut


def note_rows(text, icon, tone, width, nudge=False, cue=False, limit=2):
    """A note below the create rows, in up to limit rows, in tone, a role (see ui.ROLES). nudge,
    when enter was pressed but can't go anywhere, draws attention to it: a dim note takes the
    attention role, and the note turns bold. With cue, a note cut to fit ends in a dim hint that
    tab shows it all, for when the footer has no room to say so."""
    tone = "attention" if nudge and tone == "dim" else tone
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
    attr = ui.style("danger") | (ui.style("bold") if nudge else 0)
    return [[(line, attr)] for line in ui.clip(ui.hang(util.ICONS["error"], error, width), limit, width)]


def destination(plan, setup):
    """Where the worktree goes, and the setup command that runs in its new tab, if any."""
    return [("In", util.tilde(str(plan["path"])), "path")] + ([("Then runs", setup, "end")] if setup else [])


def plan_sentences(plan, setup):
    """What enter does, for plan_rows: open a checked-out branch's worktree, or create one."""
    if plan.get("open"):
        return [("Opens", f"the worktree for {plan['branch']}"), ("In", util.tilde(plan["open"]), "path")]
    return [(plan["verb"], plan["action"]), *destination(plan, setup)]


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


def quoted(before, name, after, width):
    """before, then name in quotes, then after, in width columns where it can: a long name loses its
    middle, down to MIN_NAME, so what comes after it, such as the base, stays in view."""
    return f"{before}“{ui.middle(name, max(MIN_NAME, width - len(before) - len(after) - 2))[0]}”{after}"


CREATE = ("+ Create ", " from {}")  # Around the name and the base in a create row.


def create_rows(tops, typed, width, index):
    """The create rows among tops (see heads), such as + Create “fix-login” from origin/main, then
    the base's note, such as "default branch", dim, which drops first in a narrow pane. A long name
    loses its middle the same in every row, so they still read alike, and the notes line up.
    Returns {each create row's place in tops: its row}, the one at index selected."""
    creates = [(i, head) for i, head in enumerate(tops) if head["kind"] == "create"]
    after = max([len(CREATE[1].format(head["base"]["label"])) for _, head in creates] + [0])
    labels = {i: quoted(CREATE[0], typed, CREATE[1].format(head["base"]["label"]), width - 2 - (
        after - len(CREATE[1].format(head["base"]["label"])))) for i, head in creates}
    align = max([len(label) for label in labels.values()] + [0])
    rows = {}
    for i, head in creates:
        bold = ui.style("bold") if i == index else 0
        row = [ui.pointer(i == index), (labels[i], ui.style("live") | bold),
               (" " * (align - len(labels[i]) + 3) + head["base"].get("note", ""), ui.style("dim"), 1)]
        rows[i] = ui.Selected(row) if i == index else row
    return rows


def head_row(head, typed, width, selected, nudge):
    """A row above the existing branches, for what the text can do (see heads), other than a create
    row (see create_rows): ask for a name, open a checked-out branch's worktree, or, dim, a branch
    the text can't be. The selected one is bold; nudge, when enter was pressed on the row asking
    for a name, makes it stand out."""
    kind, plan = head["kind"], head.get("plan") or {}
    bold = ui.style("bold") if selected else 0
    width -= 2  # The pointer.
    if kind == "type":
        attr = (ui.style("attention") | ui.style("bold")) if nudge else ui.style("dim") | bold
        row = [("+ Type a name to create a branch", attr)]
    elif kind == "open":
        row = [(quoted("Open the worktree for ", plan["branch"], "", width), ui.style("live") | bold)]
    else:  # Dim: the note below says why the text can't be a new branch.
        label = "+ Create a new branch" if head["nameless"] else quoted("+ Create ", typed, "", width)
        row = [(label, ui.style("dim") | bold)]
    row = [ui.pointer(selected)] + row
    return ui.Selected(row) if selected else row


TYPING_HELP = [
    ("ctrl+a / ctrl+e", "the start / end of the text; ← / → move the cursor"),
    ("ctrl+u / ctrl+w", "delete to the start / the word before the cursor"),
]


def heads(text, create, branches, bases, planned):
    """The rows above the existing branches, for what the text can do: with no text, one asking for
    a name; text that can be a new branch, one creating it from each base, the default branch
    first; a branch that's checked out, one opening its worktree; other text, one that can't act,
    with a note below it saying why. Each is a dict: its "kind", and its "plan" and "base" where
    it has them. create is the plan for the text from the first base, and planned(i) the plan from
    bases[i]."""
    if not text.strip():
        return [{"kind": "type"}]
    if create.get("open"):
        return [{"kind": "open", "plan": create}]
    if create.get("error"):
        # A remote's name, text such as origin/x, or a branch checked out in a missing folder has no
        # branch to name.
        nameless = bool(create.get("hint") or create.get("missing") or branching.split_remote(text, branches)
                        or not branching.normalize_branch(text))
        return [{"kind": "blocked", "plan": create, "nameless": nameless}]
    return [{"kind": "create", "plan": planned(i), "base": base} for i, base in enumerate(bases)]


class NewScreens:
    def new_screen(self, root=False, text=""):
        """Create a worktree, for an existing branch or a new one. Every printable key types, so
        typing a name never runs a command; the list moves with the arrows. Above the existing
        branches are the rows for what the text can do (see heads); enter acts on the selected
        row at once, and the plan below the list says what it will do. While typing, the first
        create row stays selected, or the one typing went on from, unless the text surely means
        an existing branch (see matching.strong). Returns True once a worktree is created or
        opened. root is True when the picker opened straight into this screen, so esc quits.
        text starts the input, such as with the list's filter that found no worktrees."""
        stdscr = self.stdscr
        keyboard.set_timeout(stdscr, -1)
        cwd = self.cwd
        branches = self.branches()
        setup = worktrees.setup_command(cwd)
        tpos, index, top = len(text), None, 0  # index None: choose once the list is filtered.
        base = 0  # The create row typing went on from, so a name keeps the base chosen for it.
        reselect = None  # A branch to select again once the branches reload, by its ref.
        moved = False
        plans = {}  # New branches' plans, since checking a name runs Git.
        back = "quit" if root else "back"
        flash = ui.Flash()
        nudge = False  # Enter was pressed where it can't go anywhere: the reason stands out.
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            suggestions = branches["suggestions"]
            bases = branches.get("bases") or [{"ref": "HEAD", "label": branches["head"], "note": "current branch"}]
            named = branching.named_remotes(suggestions)
            preferred = branches.get("preferred", "origin")
            # Text such as origin/fix matches the branches on origin by the rest, fix, as git branch
            # -r lists them. Other remotes' branches never match it, even ones with the same name.
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
            typed = branching.normalize_branch(text)

            def planned(i):
                # By name and the text's last character, since origin/ and fix/ plan differently
                # from origin and fix. Only the first base's plan asks Git about the name.
                key = (typed, text.strip()[-1:], i)
                if key not in plans:
                    plans[key] = (branching.from_base(planned(0), bases[i]) if i
                                  else branching.plan_branch(cwd, branches, text, None, bases[0]))
                return plans[key]

            create = planned(0) if text.strip() else None  # The plan for a new branch named by the text.
            tops = heads(text, create, branches, bases, planned)
            items = tops + existing
            if reselect:  # After the branches reloaded: the same branch, where it's still listed.
                index = next((i for i, s in enumerate(existing, len(tops)) if s["ref"] == reselect), None)
                reselect = None
            if index is None:
                first = min(base, len(tops) - 1) if tops[0]["kind"] == "create" else 0
                # A branch that's checked out isn't listed: the first row, which opens it, stays
                # selected, rather than a longer name that starts with it.
                checked_out = bool(create and (create.get("open") or create.get("missing")))
                index = first if checked_out else next(
                    (i for i, s in enumerate(existing, len(tops)) if matching.strong(queries[id(s)], s["name"])), first)
                moved = index >= len(tops)  # Off the create rows, by what was typed.
            index = min(index, len(items) - 1)
            on_head = index < len(tops)
            kind = tops[index]["kind"] if on_head else "branch"
            plan = tops[index].get("plan") if on_head else branching.plan_branch(cwd, branches, "", items[index])

            height, _ = stdscr.getmaxyx()
            space = ui.space(stdscr)
            status = flash.rows(space)
            # Why the text can't be a new branch, right below the rows for it, whichever row is
            # selected: in its warning's role while those rows are, but dim while an existing
            # branch is, since enter goes there instead. Text such as origin/fi, partway to a remote
            # branch, needs no note once one is selected.
            said = None  # The note: (its text, icon, tone, and whether enter was pressed to no avail).
            qualifies = False  # Whether the note is about another remote's version, which qualifies the plan.
            if create and create.get("error") and not (not on_head and create.get("remote")):
                if not on_head and items[index]["ref"] in create.get("exists", ()):
                    said, qualifies = (exists_note(create), "info", "dim", False), bool(create.get("tracks"))
                elif create.get("open"):
                    # Selected, the row and the plan say it; where it is goes in the plan, which tab
                    # shows in full.
                    if not on_head:
                        said = (create["error"] + ".", "info", "dim", False)
                    elif create.get("tracks"):
                        said, qualifies = (tracks_note(create["tracks"]), "info", "dim", False), True
                elif create.get("hint"):  # Such as a remote's name, on the way to origin/name.
                    said = (create["error"], "info", "dim", nudge and on_head)
                elif on_head:
                    said = (create["error"], "error", "danger", nudge)
                else:
                    said = (create["error"], "info", "dim", False)
            note = note_rows(*said[:3], space, said[3]) if said else []
            # Tab shows a note cut to fit in full, and always the one about another remote's
            # version, which qualifies what enter does.
            note_hidden = bool(said) and note_cut(said[0], said[1], space)
            noted = note_hidden or qualifies
            heading = [ui.header("New worktree", self.label), [], []]
            room = height - ui.margin(height) - len(heading) - len(status) - 1
            # Above the list: the rows for the text and the note, a blank row, the heading, and the
            # column titles, or the line saying nothing matches.
            fixed = len(tops) + len(note) + 3
            # What enter does, wrapped, below the list, with at least two of the list's rows left.
            limit = max(1, room - fixed - 1 - min(2, max(1, len(existing))))
            # Enter acts on a create row, the row opening a checked-out branch, or a branch whose
            # plan has no error. Elsewhere, the row, the note, or the plan's error says why, and
            # pressing enter makes it stand out.
            can_enter = kind in ("create", "open") or (kind == "branch" and not plan.get("error"))
            lines = plan_sentences(plan, setup) if can_enter else []
            short = plan.get("short") if plan else None
            if kind == "branch" and plan.get("error"):
                action, cut = error_rows(plan["error"], space, limit, nudge), False
            else:
                action, cut = plan_rows(lines, space, limit, short) if lines else ([], False)
            # Once typing selected a branch, ↑ is the way back to the create rows, so it outlasts tab.
            moved = moved and not on_head
            keys = ui.Keys()
            keys.add("enter", "open" if kind == "open" else "create", can=can_enter)
            keys.add("↑/↓", "move", rank=2 if moved else 3, can=len(items) > 1)
            # Tab shows what enter does whenever there's a plan or a note; listed when they're cut.
            keys.add("tab", "details" if cut or note_hidden else "", rank=3 if moved else 2,
                     can=bool(lines or noted))
            keys.add("?", "help", rank=1)
            keys.add("esc", "clear" if text else back)
            keys.add("ctrl+g", why="" if not on_head else "Select an existing branch to open its pull request.")
            footer = keys.row()
            if (cut or note_hidden) and not keys.fits("tab", space):
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
            list_index = index - len(tops)  # Negative on the rows for the text.
            top = ui.scroll_top(top, max(0, list_index), len(existing), capacity)
            # Beside the input: the selected branch's place when the list scrolls.
            mark = f"{list_index + 1}/{len(existing)}" if list_index >= 0 and len(existing) > capacity else ""
            heading[1], column = ui.search_row(text, tpos, True, space, PLACEHOLDER, mark, PROMPT)

            if not keyboard.pending_input(stdscr):
                creates = create_rows(tops, typed, space, index)
                body = [creates[i] if i in creates else head_row(head, typed, space, i == index, nudge and i == index)
                        for i, head in enumerate(tops)] + note + [[]]
                if existing:
                    visible = existing[top:top + capacity]
                    body += self.branch_rows(visible, top, list_index, [queries[id(s)] for s in visible], space,
                                             len(existing), named, existing)
                else:  # One line, with no heading over it.
                    body.append([("  No matching branches" if text.strip() else "  No other branches",
                                  ui.style("dim"))])
                body += [[]] * (fixed + capacity - len(body)) + ([[]] if gap else []) + action
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
                text, tpos, index, base = "", 0, None, 0
            elif key == "\t":  # Never part of a branch name.
                if not keys.refused("tab"):
                    ui.page(stdscr, "New worktree — details", lambda width: [[]] + (
                        plan_rows(lines, width, 1000)[0] if lines else []) + (
                        ([[]] if lines else []) + note_rows(*said[:3], width, limit=1000)
                        if noted else []))
            elif key == "?":  # Never part of a branch name.
                self.new_help(root)
            elif key == "\x07":
                if not keys.refused("ctrl+g", lambda why: flash.set(why, "attention")):
                    self.open_pr({"pr": self.prs.get(items[index]["name"])}, flash)
                keyboard.set_timeout(stdscr, flash.wait(-1))
            elif key in keyboard.ENTER:
                if keys.refused("enter"):
                    nudge = True  # The row, the note, or the plan's error says why; it stands out.
                    continue
                if kind == "open":
                    if self.open_checked_out(plan, flash):
                        return True
                    continue
                try:
                    self.create_and_open(cwd, plan)
                except TabFailed:
                    raise  # The worktree is there; the picker's error screen says so, and it exits.
                except RuntimeError as error:
                    # Nothing was created, but something changed since the branches were listed,
                    # such as the folder or the branch appearing: say what Git said, and list
                    # them again, so the rows and the plan are made afresh. A selected branch
                    # stays selected; otherwise the rows are chosen as for the text typed.
                    self.say(str(error), flash=flash)
                    self.reload()
                    branches, plans = self.branches(), {}
                    index, reselect = None, None if on_head else items[index]["ref"]
                    continue
                return True
            else:
                new_text, tpos = keyboard.edit_text(text, tpos, key)
                if new_text != text:
                    if kind == "create":  # Typing on keeps this base, not one passed on the way down.
                        base = index
                    text, index = new_text, None

    def new_help(self, root):
        ui.show_help(self.stdscr, "New worktree", [("Keys", [
            ("typing", "filter the existing branches, or name a new branch"),
            ("create rows", "the typed name as a new branch, from the default branch or from the "
                            "current one, as it will be: spaces become dashes"),
            ("first row", "stays selected while typing, or the create row you typed on from, unless the "
                          "text is a branch's name or the start of one of its words"),
            ("whole name", "wins over longer names that start with it"),
            ("origin/name", "finds name among origin's branches only"),
            ("↑ / ↓", "move; also ctrl+p / ctrl+n"),
            ("pgup / pgdn", "a page up / down"),
            ("home / end", "the first / last row"),
            ("enter", "act on the selected row at once: create the new branch and its worktree, create "
                      "a worktree for the branch, or open the worktree a branch is checked out in"),
            ("tab", "what enter does, in full: the branch, its folder, and the setup command, and "
                    "a note that's cut short"),
            ("ctrl+g", "open the selected branch's pull request in the browser"),
            ("esc", "clear the text; with none, " + ("quit" if root else "go back")),
            ("ctrl+c", "quit"),
        ]), ("Editing", TYPING_HELP), ("Labels", [
            ("3d", "time since the branch's last commit"),
            (ui.pr_cell({"pr": {"number": 12, "state": "OPEN"}})[0],
             "its pull request: open, draft, merged, or closed"),
            ((util.ICONS["local"] + " local", ui.style("dim")), "a branch in this repository"),
            ((util.ICONS["remote"] + " remote", ui.style("source")),
             "only on a remote; enter creates a local branch tracking it"),
            ((util.ICONS["remote"] + " fork", ui.style("source")),
             "the remote's name, when it's not origin or there's more than one; fork/ lists only its branches"),
            ("narrow panes", "pull requests show just their number, then only the icons show, and the "
                             "branch is cut short; the plan below the "
                             "list names the selected branch in full"),
        ])])

    def branch_rows(self, visible, top, index, queries, space, count, named=False, measured=None):
        """The existing branches' heading, column titles, and rows: the same columns as the
        worktree list: branch, age, pull request, then where it is. index is the selected
        branch's place among them all, or negative when none is. queries holds the text each
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
        for i, (item, pr, source, query) in enumerate(zip(visible, prs, sources, queries), top):
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
            row.append((source, ui.style("source") if item["kind"] == "remote" else ui.style("dim")))
            rows.append(ui.Selected(row) if i == index else row)
        return rows

    def open_checked_out(self, plan, flash):
        """Open the worktree a typed branch is checked out in. Returns True once it's open."""
        path = plan["open"]
        entry = next((e for e in self.entries if e["worktree"] == path),
                     {"worktree": path, "name": plan["branch"], "branch": "refs/heads/" + plan["branch"]})
        if worktrees.missing(entry):
            flash.set("That worktree's folder is missing; prune it with dd in the list.", "attention")
            return False
        try:
            self.busy("Opening…", lambda: tabs.open_tab(self.session, entry, self.client, terminals=self.terminals))
        except (RuntimeError, OSError) as error:
            self.say(str(error), flash=flash)
            return False
        return True

    def create_and_open(self, cwd, plan):
        """Create the worktree, then open its tab. Raises RuntimeError when nothing was created, and
        TabFailed when the worktree was, but its tab couldn't be opened."""
        try:
            self.busy(f"Creating worktree for {plan['branch']}…", lambda: branching.create_worktree(cwd, plan))
        except OSError as error:
            raise RuntimeError(str(error)) from error
        try:
            self.busy("Opening tab…", lambda: tabs.open_tab(
                self.session, {"worktree": str(plan["path"]), "branch": plan["branch"]}, self.client,
                worktrees.setup_command(cwd)))
        except (RuntimeError, OSError) as error:
            raise TabFailed(f"Worktree created at {plan['path']}, but opening its tab failed: {error}") from error


class TabFailed(RuntimeError):
    """The worktree was created, but its tab couldn't be opened."""
