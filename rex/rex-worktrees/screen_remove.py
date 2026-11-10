"""Removing worktrees: one at a time, pruning one whose folder is missing, repairing a broken one,
and cleaning up finished and missing ones."""
import actions
import keyboard
import matching
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


def closing_rows(groups, width, session="", label="", full=False, cue=False):
    """A row per tab with panes that will close, marking the tab the picker was opened in and tabs
    in other sessions. A long tab name is cut, so what closes in it stays in view; in a narrow pane,
    what closes goes on its own row below. With cue, for when the footer has no room for "tab
    details", what closes after a cut name is followed by a hint that tab shows all, where it fits;
    with cue "always", what closes goes below a cut name to make room for it. In full, a name too
    long for its row wraps instead, with the mark after it and what closes below."""
    rows = []
    for group in groups:
        name, detail = tabs.describe_closing(group)
        mark = where(group, session, label)
        room = width - 2 - len(mark) - 2 - len(detail)
        below = room < min(len(name), 12) or (cue == "always" and len(name) > room)
        if below:
            room = max(4, width - 2 - len(mark))
        if full and len(name) > room:
            lines = ui.wrap_path(name, width - 2)
            rows += [[("  " + line, ui.style("bold"))] for line in lines[:-1]]
            last = [("  " + lines[-1], ui.style("bold"))]
            if mark and len(lines[-1]) + len(mark) > width - 2:
                rows.append(last)
                last = [("  " + mark.strip(), ui.style("dim"))]
            elif mark:
                last.append((mark, ui.style("dim")))
            rows += [last] + [[("    " + line, ui.style("dim"))] for line in ui.wrap(detail, width - 4)]
            continue
        hint = [(ui.TAB_CUE, ui.style("dim"), 1)] if cue and len(name) > room else []
        name = name if len(name) <= room else name[:room - 1] + "…"
        row = [("  " + name, ui.style("bold"))] + ([(mark, ui.style("dim"))] if mark else [])
        if below:
            lines = ui.wrap(detail, width - 4) if full else [detail]  # In full, wrapped in a narrow pane.
            rows += [row] + [[("    " + line, ui.style("dim"))] for line in lines]
            rows[-1] = rows[-1] + hint
        else:
            rows.append(row + [("  " + detail, ui.style("dim"))] + hint)  # The hint drops out without room.
    return rows


def file_rows(files, width, full=False):
    """A row per changed file, such as "?? notes.txt". In full, a long path wraps after a slash,
    below its start."""
    if not full:
        return [[("  " + f, 0)] for f in files]
    rows = []
    for f in files:
        status, path = (f[:3], f[3:]) if len(f) > 3 and f[2] == " " else ("", f)
        lines = ui.wrap_path(path, max(8, width - 2 - len(status)))
        rows += [[("  " + (status if i == 0 else " " * len(status)) + line, 0)] for i, line in enumerate(lines)]
    return rows


def closing_heading(groups):
    count = sum(len(g["panes"]) for g in groups)
    return ui.section(f"Panes that will close ({count})", tone="attention")


LIST_HINT = " (tab lists all)"


def summary_row(text):
    """A list's place when there's no room for any of its rows, such as "16 uncommitted files"."""
    return [("  " + text, ui.style("attention") | ui.style("bold")), ("  (tab lists them)", ui.style("dim"), 1)]


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


def candidates(entries):
    """What cleanup offers: finished worktrees (see worktrees.cleanup_reason), and ones whose
    folder is missing, which it prunes, unless they're kept (see worktrees.removal_problem)."""
    return [e for e in entries if worktrees.cleanup_reason(e)
            or (worktrees.missing(e) and not worktrees.removal_problem(e))]


def checkable(entries):
    """The candidates cleanup can check: every one but those with uncommitted changes, which it
    never discards. With none, c says why rather than starting."""
    return [e for e in candidates(entries) if e.get("dirty") is not True]


def starts_checked(entry):
    """Whether cleanup starts with a candidate checked: not one with uncommitted changes, which
    can't be checked, nor one whose folder is missing, which may be away only for now, as on a
    drive that isn't mounted, so pruning it is left for you to choose."""
    return entry.get("dirty") is not True and not worktrees.missing(entry)


def cleanable(entries):
    """What cleanup removes if enter is pressed as it opens, which the list's c counts: the
    candidates that start checked (see starts_checked)."""
    return [e for e in candidates(entries) if starts_checked(e)]


def cleanup_label(entry):
    """Why a worktree is offered for cleanup, as the list labels it: #12 merged, gone, or missing."""
    if worktrees.missing(entry):
        return ui.word("missing")
    reason = worktrees.cleanup_reason(entry) or ""
    return ui.word("deleted") if reason == worktrees.REMOTE_DELETED else reason


def cleanup_role(entry):
    """The role of a cleanup row's reason, the label's own (see ui.LABELS): finished, or danger
    for a missing folder."""
    return ui.style("danger" if worktrees.missing(entry) else "finished")


# What pruning a worktree whose folder is missing does (see actions.prune_worktree).
PRUNE_TEXT = "Its folder is missing, so pruning drops only Git's record of it."


# Why cleanup starts with a missing worktree unchecked (see starts_checked).
UNCHECKED_TEXT = "It starts unchecked, since its folder may be away only for now, as on a drive that isn't mounted."


def cleanup_reason_text(entry):
    """Why a worktree is offered for cleanup, as a sentence; for a missing one, what pruning it
    does, and why it starts unchecked."""
    if worktrees.missing(entry):
        return PRUNE_TEXT + (" Its branch is kept." if "branch" in entry else "") + " " + UNCHECKED_TEXT
    reason = worktrees.cleanup_reason(entry) or ""
    if reason.endswith(" merged"):
        return f"Its pull request {reason}."
    return "Its remote branch is gone." if reason == worktrees.REMOTE_DELETED else ""


def cleanup_intro(entries, width):
    """Why the candidates are offered, naming only the reasons among them, such as "Their pull
    requests merged, or their folders are missing.", in fewer words where that doesn't fit, down
    to the rows' own labels. It leaves room for the list's position at its right (see
    ui.with_position)."""
    kinds = {"missing" if worktrees.missing(e) else "merged" if (worktrees.cleanup_reason(e) or "").endswith(" merged")
             else "gone" for e in entries}
    phrases = [("merged", "their pull requests merged", "PRs merged", "merged"),
               ("gone", "their remote branches are gone", "remote branches gone", "gone"),
               ("missing", "their folders are missing", "folders missing", "missing")]
    for column in (1, 2, 3):
        said = [phrase[column] for phrase in phrases if phrase[0] in kinds]
        text = said[-1] if len(said) == 1 else ", ".join(said[:-1]) + ", or " + said[-1]
        text = text[:1].upper() + text[1:] + "."
        if len(text) + 8 <= width:
            break
    return text


DIRTY_NOTE = "Has uncommitted changes, so it can't be cleaned up here; remove it on its own with dd."
CANT_CHECK = "Can't check it: it has uncommitted changes. Remove it on its own with dd."
NOTHING_CHECKED = "Nothing is checked. Check a worktree with space, or press q to cancel."


def cleanup_text(entry, closes, session="", label=""):
    """A cleanup candidate explained: why it can't be cleaned up, or why it's offered and what
    removing it closes. Returns (the text, its tone)."""
    if entry.get("dirty") is True:
        return DIRTY_NOTE, ui.style("attention")
    closing = closing_summary(closes.get(entry["worktree"]) or [], session, label)
    return cleanup_reason_text(entry) + (" " + closing if closing else ""), ui.style("dim")


def deletable(items):
    """How many of cleanup's rows the option to delete branches can apply to: ones it can check
    and remove, rather than prune. With none, it isn't offered."""
    return sum(not worktrees.missing(i["entry"]) and i["entry"].get("dirty") is not True for i in items)


def branch_option(count, prunes=False):
    """The cleanup option to delete the branches, and what it does, for count rows it can apply to
    (see deletable); prunes says whether some are missing, whose branches pruning keeps."""
    kept = "; pruned ones keep theirs." if prunes else "."
    if count == 1:
        return "Also delete its branch", "Deletes the worktree's branch when it has no unmerged commits."
    return "Also delete their branches", "Deletes each removed worktree's branch when it has no unmerged commits" + kept


def cleanup_done(removed, deleted, unmerged=0, pruned=0):
    """What cleanup did, such as "Removed 2 worktrees and their branches." removed counts the
    worktrees removed; deleted, their branches deleted; unmerged, the branches kept for having
    unmerged commits when deleting them was checked; pruned, the missing worktrees pruned."""
    sentences = []
    if removed:
        its, branches = ("its", "branch") if removed == 1 else ("their", "branches")
        text = f"Removed {plural(removed, 'worktree')}"
        if deleted == removed:
            text += f" and {its} {branches}"
        elif deleted:
            text += f" and {deleted} of their branches"
        if unmerged and unmerged == removed:
            text += f"; {its} {branches} had unmerged commits, so {'it was' if removed == 1 else 'they were'} kept"
        elif unmerged:
            text += f"; {unmerged} with unmerged commits {'was' if unmerged == 1 else 'were'} kept"
        sentences.append(text + ".")
    if pruned:
        sentences.append(f"Pruned {plural(pruned, 'missing worktree')}.")
    return " ".join(sentences)


def sentences(clauses):
    """Clauses such as "fix/a wasn't removed: …" as sentences, for a message."""
    return " ".join(clause.strip().rstrip(".") + "." for clause in clauses)


def dirty_refusal(candidates, width):
    """Why cleanup can't start when every candidate has uncommitted changes, and what to do
    instead, before which worktree it is, cut in the middle so it all fits on the status line."""
    if len(candidates) > 1:
        return (f"Can't clean up: the {len(candidates)} finished worktrees all have uncommitted changes; "
                "remove each on its own with dd.")
    lead = "Can't clean up: the only finished worktree has uncommitted changes; remove it on its own with dd. It's "
    name = candidates[0]["name"]
    text = lead + name + "."
    # The whole name, or else the longest middle cut that fits, down to 8 characters; when even
    # that doesn't fit, the shortest, and the status line cuts the rest.
    for size in range(len(name), 7, -1):
        text = lead + ui.middle(name, size)[0] + "."
        if len(ui.hang(util.ICONS["info"], text, width)) <= ui.Flash.LINES:
            break
    return text


def kept_reason(entry, target):
    """Why removing a worktree keeps its branch, which can be deleted only once it's merged into
    the default branch, target (see worktrees.merged_into)."""
    if not target:
        return "The branch will be kept: there's no default branch to check it's merged into."
    if target in (entry["name"], "origin/" + entry["name"]):
        return "The branch will be kept: it's the default branch."
    return f"The branch will be kept: it has commits not in {target}."


LABEL_AT = len("› [ ] ")  # Where an option's label starts; the notes under the options line up with it.
DISCARD_HINT = "Check this to remove the worktree."
DISCARD_CHECKED = "Its uncommitted changes will be lost."


def note_rows(text, width, indent, tone):
    """A note, wrapped, its lines starting at indent: under the options, where their labels start,
    so it can't be read as an option itself."""
    return [[(" " * indent + line, tone)] for line in ui.wrap(text, width - indent)]


# What repairing a broken worktree does (see actions.repair_worktree).
REPAIR_TEXT = ("Its folder is there, but its .git file is gone, so Git can't use it. Repairing writes the file "
               "again (git worktree repair); the folder's files stay as they are.")
# What each kind of confirmation does, as its title, enter's label, the busy message, and a phrase
# for saying it has no options (see removal_options).
MODES = {"remove": ("Remove worktree", "remove", "removing it"),
         "prune": ("Prune missing worktree", "prune", "pruning it"),
         "repair": ("Repair worktree", "repair", "repairing it")}


class RemovalScreens:
    def remove(self, entry):
        """Confirm and remove a worktree, one that worktrees.removal_problem allows (the list's
        keys check it), or prune it when its folder is missing, or repair it when it's broken.
        Returns True if the picker should exit."""
        if worktrees.missing(entry):
            return self.prune(entry)
        if worktrees.broken(entry):
            return self.repair(entry)
        try:
            files = worktrees.changed_files(entry)
            closing = tabs.panes_to_close(self.terminals_now(), [entry["worktree"]])
            merged = worktrees.merged_into(self.repo, entry)
            kept = None if merged or "branch" not in entry else kept_reason(entry, worktrees.default_branch(self.repo))
        except (RuntimeError, OSError, ValueError) as error:
            self.say(str(error))
            return False
        options = []  # Checkboxes, toggled with space.
        if files:
            options.append({"id": "force", "checked": False,
                            "label": [("Discard uncommitted changes", ui.style("danger"))]})
        if merged:
            options.append({"id": "delete_branch", "checked": False,
                            "label": [("Also delete the branch", 0), ("   (no unmerged commits)", ui.style("dim"), 1)]})
        checked = self.removal_options(entry, files, closing, merged, options, kept)
        if checked is None:
            return False
        name = entry["name"]
        try:
            # The branch is deleted only if it's still merged then (see actions.remove_worktree).
            deleted, problems = self.busy(f"Removing {name}…", lambda: actions.remove_worktree(
                self.repo, self.session, entry, closing, force="force" in checked,
                delete_branch="delete_branch" in checked))
        except (RuntimeError, OSError) as error:  # Git still lists it: nothing was done.
            self.say(f"{name} wasn't removed: {error}")
        else:
            done = f"Removed {name}" + (" and its branch" if deleted else "")
            if problems:  # Said after what was done, so the message says both.
                self.say(done + ", but " + sentences(["; ".join(p.rstrip(".") for p in problems)]))
            else:
                self.say(done + ".", "live")
        self.reload()
        return False

    def prune(self, entry):
        """Confirm and prune a worktree whose folder is missing: drop Git's record of it, and
        nothing else, so its branch is kept (see actions.prune_worktree). Returns True if the
        picker should exit."""
        if self.removal_options(entry, [], [], None, [], mode="prune") is None:
            return False
        try:
            self.busy(f"Pruning {entry['name']}…", lambda: actions.prune_worktree(self.repo, entry))
        except (RuntimeError, OSError) as error:
            self.say(f"{entry['name']} wasn't pruned: {error}")
        else:
            self.say(f"Pruned {entry['name']}" + ("; its branch is kept." if "branch" in entry else "."), "live")
        self.reload()
        return False

    def repair(self, entry):
        """Confirm and repair a broken worktree: write its .git file again, so Git can use it, with
        its files as they are (see actions.repair_worktree). Git repairs every other worktree
        without its .git file too, so the confirmation lists them. Returns True if the picker
        should exit."""
        also = [e for e in self.entries
                if e is not entry and e["worktree"] != entry["worktree"] and worktrees.unlinked(e)]
        if self.removal_options(entry, [], [], None, [], mode="repair", also=also) is None:
            return False
        try:
            self.busy(f"Repairing {entry['name']}…", lambda: actions.repair_worktree(self.repo, entry))
        except (RuntimeError, OSError) as error:
            self.say(f"{entry['name']} wasn't repaired: {error}")
        else:
            others = sum(not worktrees.unlinked(e) for e in also)
            self.say(f"Repaired {entry['name']}" + (f" and {plural(others, 'other worktree')}; Git can use them again."
                                                    if others else "; Git can use it again."), "live")
        self.reload()
        return False

    def removal_options(self, entry, files, closing, merged, options, kept=None, mode="remove", also=()):
        """Confirm removing a worktree, or pruning one whose folder is missing (mode "prune"), or
        repairing a broken one (mode "repair"), which also repairs the worktrees in also. Returns
        the set of checked option ids, or None if cancelled. Each reads the same way: what will
        go, or change, then what that costs (its uncommitted changes and the panes that will close,
        lists that shorten to fit), then the options and the notes under them, which always show,
        as do the key hints. kept says why the branch is kept, when it can't be deleted (see
        kept_reason). Enter acts only on what was on screen: one pressed before the screen was
        drawn, such as in pasted text, is dropped."""
        title, verb, doing = MODES[mode]
        stdscr, cursor, pending, nudged, flash = self.stdscr, 0, None, False, ui.Flash()
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            height, _ = stdscr.getmaxyx()
            width = ui.space(stdscr)
            checked = {o["id"] for o in options if o["checked"]}
            # Enter removes a worktree with uncommitted changes only once discarding them is checked.
            discard = bool(files) and "force" not in checked

            def parts(width, full=False, cue=False):
                """The worktree, then what removing it loses and closes, then the options. When
                they don't all fit, each list keeps at least a line saying how many, and tab shows
                them all, in full. cue marks a cut tab name (see closing_rows)."""
                path = [[(line, ui.style("dim"))] for line in ui.wrap_path(util.tilde(entry["worktree"]), width)]
                if mode != "remove":  # Labeled as in the list, after the path where it fits.
                    tag, role = ui.label("missing" if mode == "prune" else "broken")
                    if len(ui.row_text(path[-1])) + 2 + len(tag) <= width:
                        path[-1] = path[-1] + [("  " + tag, role)]
                    else:
                        path.append([(tag, role)])
                body = [ui.fixed(*[[(line, ui.style("bold"))] for line in ui.wrap_path(entry["name"], width)]),
                        ui.extra(*path), ui.GAP]
                if mode != "remove":
                    text = PRUNE_TEXT if mode == "prune" else REPAIR_TEXT
                    body += [ui.fixed(*[[(line, 0)] for line in ui.wrap(text, width)]), ui.GAP]
                if also:
                    body += [ui.items([[("  " + line, 0)] for e in also for line in (
                        ui.wrap_path(e["name"], width - 2) if full else [e["name"]])],
                        heading=ui.section(f"Also repaired ({len(also)})", "Git repairs them all at once"),
                        summary=summary_row(plural(len(also), "other worktree") + " repaired too"), hint=LIST_HINT),
                        ui.GAP]
                if files:
                    body += [ui.items(file_rows(files, width, full), heading=ui.section(
                        f"Uncommitted changes ({len(files)})", tone="attention"),
                        summary=summary_row(plural(len(files), "uncommitted file")), hint=LIST_HINT), ui.GAP]
                if closing:
                    count = sum(len(g["panes"]) for g in closing)
                    body += [ui.items(closing_rows(closing, width, self.session, self.label, full, cue),
                                      heading=closing_heading(closing),
                                      summary=summary_row(plural(count, "pane") + " will close"), hint=LIST_HINT),
                             ui.GAP]
                option_rows = []
                for i, option in enumerate(options):
                    box = "[x] " if option["checked"] else "[ ] "
                    row = [ui.pointer(i == cursor), (box, ui.style("select"))] + option["label"]
                    option_rows.append(ui.Selected(row) if i == cursor else row)
                    if option["id"] == "force":  # Right under it: what checking it does.
                        # Standing out once enter was pressed without it, as enter's reason.
                        tone = ui.style("attention" if nudged and not option["checked"] else "dim")
                        option_rows += note_rows(DISCARD_CHECKED if option["checked"] else DISCARD_HINT,
                                                 width, LABEL_AT, tone)
                # Repairing leaves the branch alone, so there's nothing to say about it.
                about = not merged and "branch" in entry and mode != "repair"
                notes = [kept or "The branch will be kept."] if about else []
                notes = [row for note in notes for row in note_rows(note, width, LABEL_AT if options else 0,
                                                                    ui.style("dim"))]
                # A blank row between the options and the notes, even in a short pane, so a note
                # isn't read as one about the option above it, such as Discard's hint.
                if option_rows and notes:
                    notes = [[]] + notes
                return body + ([ui.fixed(*option_rows)] if option_rows else []) + ([ui.fixed(*notes)] if notes else [])

            top = [ui.header(title, self.label), []]
            status = flash.rows(width)  # Why a key can't act.
            room = height - ui.margin(height) - len(top) - len(status) - 1
            body = parts(width)
            details = ui.abridged(body, parts(width, True), room, width)
            keys = ui.Keys()
            keys.add("j/k", "move", rank=3, can=len(options) > 1)
            keys.add("space", "toggle", why="" if options else f"Nothing to check: {doing} has no options.")
            # With Discard unchecked, enter makes the hint under it stand out, rather than saying why.
            keys.add("enter", verb, can=not discard)
            keys.add("tab", "details" if details else "", rank=1)  # Always works; listed when something's cut.
            keys.add("?", "help", rank=2)
            keys.add("q", "cancel")
            footer = keys.row()
            if details and not keys.fits("tab", width):
                # The footer has no room to say tab shows what's cut, so a cut tab name says so,
                # taking a row for it only when no list says “(tab lists all)” already.
                listed = any("(tab lists" in ui.row_text(row) for row in ui.fit_parts(body, room))
                body = parts(width, cue=True if listed else "always")
            # Keys already waiting were pressed before this frame could be seen.
            ahead = keyboard.pending_input(stdscr)
            ui.render(stdscr, ui.compose(height, top, ui.fit_parts(body, room), status + [footer]))
            key = keyboard.read_key(stdscr)
            if key is None:
                continue
            nudged = False
            flash.clear()
            # A motion first, so esc cancels a count or a g typed partway rather than the screen.
            # Every option is in view, so H, M, and L choose among them all.
            new_cursor, pending = keyboard.motion(key, cursor, len(options), len(options), pending,
                                                  range(len(options)))
            if new_cursor is not None:
                cursor = new_cursor
            elif key in keyboard.CLOSE:
                return None
            elif key == " ":
                if not keys.refused("space", lambda why: flash.set(why, "attention")):
                    options[cursor]["checked"] = not options[cursor]["checked"]
            elif key == "\t":
                ui.show_details(stdscr, title, parts)
            elif key == "?":
                if mode == "prune":
                    actions_help = [("enter", "prune: drop Git's record of the worktree, whose folder is missing; "
                                              "its branch is kept, and nothing else changes")]
                elif mode == "repair":
                    actions_help = [("enter", "repair: write the worktree's .git file again (git worktree repair), "
                                              "so Git can use it; the folder's files stay as they are, and every "
                                              "other worktree without its .git file is repaired too")]
                else:
                    actions_help = [
                        ("j / k", "move between the options"),
                        ("space", "check or uncheck the option; a worktree with uncommitted changes is removed "
                                  "only with “Discard uncommitted changes” checked"),
                        ("enter", "remove the worktree, and close the panes inside it"),
                    ]
                ui.show_help(stdscr, title, [("Keys", actions_help + [ui.DETAILS_HELP, ui.CLOSE_HELP])])
            elif key in keyboard.ENTER:
                if ahead:  # Not confirmed by someone who saw the screen: it's drawn again instead.
                    continue
                if keys.refused("enter"):
                    nudged = True
                    cursor = next(i for i, o in enumerate(options) if o["id"] == "force")  # So space checks it.
                    continue
                return checked

    def cleanup(self):
        """Offer to remove every finished worktree, and prune every missing one, at once. Returns
        True if the picker should exit. With nothing it can check (see checkable), it says why
        instead. Enter acts only on what was on screen, as in removal_options."""
        offered = candidates(matching.sort_entries(self.entries, self.sort))  # In the list's order.
        if not offered:
            note = " (pull requests are still loading)" if self.prs_loading else ""
            self.refuse("No worktrees to clean up: none has a merged pull request, a remote branch that's gone, "
                        "or a missing folder" + note + ".")
            return False
        if not checkable(offered):  # Every one has uncommitted changes.
            if len(offered) == 1:
                self.reveal = offered[0]  # The list selects it, so dd removes it.
            self.refuse(dirty_refusal(offered, ui.space(self.stdscr)))
            return False
        try:
            terminals = self.terminals_now()
        except (RuntimeError, OSError, ValueError):
            terminals = []
        # Pruning closes nothing: the folder is already gone, and only Git's record of it goes.
        closes = {e["worktree"]: [] if worktrees.missing(e) else tabs.panes_to_close(terminals, [e["worktree"]])
                  for e in offered}
        stdscr, top, pending, flash = self.stdscr, 0, None, ui.Flash()
        # Worktrees with uncommitted changes start unchecked, and are never force-removed here;
        # missing ones start unchecked too, since their folder may be away only for now.
        items = [{"entry": e, "checked": starts_checked(e)} for e in offered]
        # On the first row space can check, rather than one with uncommitted changes.
        cursor = next(i for i, item in enumerate(items) if item["entry"].get("dirty") is not True)
        # The option to delete branches, for the worktrees it can apply to (see deletable), if any.
        branches = deletable(items)
        delete_branches = {"checked": False}
        while True:
            keyboard.set_timeout(stdscr, flash.wait(-1))
            height, _ = stdscr.getmaxyx()
            width = ui.space(stdscr)
            index = len(items)  # The cursor's row for "Also delete their branches", when it's offered.
            box = "[x] " if delete_branches["checked"] else "[ ] "
            branch_row = [ui.pointer(cursor == index), (box, ui.style("select")),
                          (branch_option(branches)[0], 0),
                          ("   (if it has no unmerged commits)" if branches == 1
                           else "   (only ones with no unmerged commits)", ui.style("dim"), 1)]
            # The selected row's detail, in the same number of rows for every row, so the list
            # doesn't jump as the cursor moves.
            lines = max(1, min(4, (height - 8) // 2))
            details = [self.cleanup_detail(items, i, closes, width, lines, self.session, self.label)
                       for i in range(len(items) + bool(branches))]
            # Under the options, or a message in its place, lined up with the options' labels too.
            said = [[(" " * LABEL_AT + text, attr)] for (text, attr), in flash.rows(max(1, width - LABEL_AT))]
            detail = list(said or details[cursor])  # A copy: padding it mustn't change details.
            detail += [[]] * (max(len(d) for d in details) - len(detail))
            checked = [i["entry"] for i in items if i["checked"]]
            count = len(checked)
            # With nothing checked, as when only missing worktrees are offered, the title says so,
            # since enter has nothing to do yet.
            heading = [ui.header("Clean up worktrees", self.label)
                       + ([] if count else [("   nothing checked yet", ui.style("attention"))])]
            intro = [(cleanup_intro(offered, width), ui.style("dim"))]

            def fit_list(detail):
                """The list's rows, and whether there's room for a blank row below the list and,
                with the branch option, below that too: below the list go those, the option, and
                the detail."""
                below = 3 if branches else 1
                capacity = height - ui.margin(height) - len(heading) - 2 - below - len(detail) - 1
                if capacity >= min(3, len(items)):
                    return capacity, True
                return max(1, height - ui.margin(height) - len(heading) - bool(branches) - len(detail) - 1), False

            capacity, gaps = fit_list(detail)
            # Tab shows every candidate in full when the list scrolls, or a name or detail is cut.
            longest = max(len(i["entry"]["name"]) for i in items)
            abridged = (capacity < len(items) or longest > self.cleanup_name_width(items, width) or any(
                d != self.cleanup_detail(items, i, closes, width, 1000, self.session, self.label)
                for i, d in enumerate(details)))
            dirty = cursor < index and items[cursor]["entry"].get("dirty") is True
            # Enter prunes when every checked worktree is missing, and otherwise removes, though
            # it prunes the missing ones among them; the list's "c clean up 3" counts the same.
            verb = "prune" if checked and all(worktrees.missing(e) for e in checked) else "remove"

            def keys_for(dirty, tab):
                """The keys, with the cursor on a row with uncommitted changes, which space can't
                check, or not; tab, which always works, listed or not. With nothing checked,
                enter can't remove anything."""
                keys = ui.Keys()
                keys.add("j/k", "move", rank=3, can=len(items) + bool(branches) > 1)
                keys.add("space", "toggle", why=CANT_CHECK if dirty else "")
                keys.add("enter", f"{verb} {count}", why="" if count else NOTHING_CHECKED)
                keys.add("tab", "details" if tab else "", rank=1)
                keys.add("?", "help", rank=2)
                keys.add("q", "cancel")
                return keys

            # Whether the footer has room to say tab shows what's cut is judged with space listed,
            # as it is on most rows, so it doesn't change as the cursor moves, and the list stays put.
            roomy = keys_for(False, True).fits("tab", width)
            keys = keys_for(dirty, abridged and roomy)
            footer = keys.row()
            if abridged and not roomy:
                # The footer has no room to say tab shows what's cut, so a row below the detail does.
                detail.append([(" " * LABEL_AT + ui.TAB_CUE.strip(), ui.style("dim"))])
                capacity, gaps = fit_list(detail)
            if not gaps:  # A short pane: the list keeps the rows.
                intro = None
            bottom = [footer]
            top = ui.scroll_top(top, min(cursor, len(items) - 1), len(items), capacity)
            rows = [self.cleanup_row(item, i == cursor, items, closes, width) for i, item in enumerate(items)]
            body = rows[top:top + capacity] + ([[]] if gaps else [])
            if branches:
                body.append(ui.Selected(branch_row) if cursor == index else branch_row)
                body += [[]] if gaps else []
            body += detail
            first = ui.with_position(intro or heading[0], min(cursor, len(items) - 1), len(items), capacity, width)
            head = ([heading[0], first, []] if intro else [first])
            # Keys already waiting were pressed before this frame could be seen.
            ahead = keyboard.pending_input(stdscr)
            ui.render(stdscr, ui.compose(height, head, body, bottom))
            key = keyboard.read_key(stdscr)
            if key is None:
                continue
            flash.clear()
            # The last row is the "Also delete their branches" option, when it's offered, which is
            # always in view below the list, so H, M, and L count it. A motion goes first, so esc
            # cancels a count or a g typed partway rather than the screen.
            view = keyboard.view_rows(top, capacity, len(items)) + ([index] if branches else [])
            new_cursor, pending = keyboard.motion(key, cursor, len(items) + bool(branches), capacity, pending, view)
            if new_cursor is not None:
                cursor = new_cursor
            elif key in keyboard.CLOSE:
                return False
            elif key == "\t":
                ui.page(stdscr, "Clean up worktrees — details", lambda width: self.cleanup_details(
                    items, delete_branches["checked"] if branches else None, closes, width, self.session, self.label))
            elif key == "?":
                ui.show_help(stdscr, "Clean up worktrees", [("Move", ui.NAVIGATION_HELP), ("Actions", [
                    ("space", "check or uncheck the worktree or option"),
                    ("enter", "remove the checked worktrees, closing the panes inside them, and prune the "
                              "checked missing ones"),
                    ("tab", "every worktree in full, with why it's offered and what removing it closes"),
                    ui.CLOSE_HELP,
                ]), ("Notes", [
                    ((util.ICONS["dirty"], ui.style("attention")),
                     "uncommitted changes: it can't be cleaned up here; remove it with dd"),
                    ((util.ICONS["tab"], ui.style("dim")), "panes inside it will close"),
                    ((ui.word("missing"), ui.style("danger")),
                     "its folder is missing: pruning drops only Git's record of it, and keeps its branch; it starts "
                     "unchecked, since its folder may be away only for now, as on a drive that isn't mounted"),
                ])])
            elif key == " ":
                if keys.refused("space", lambda why: flash.set(why, "attention")):
                    continue
                if cursor == index:
                    delete_branches["checked"] = not delete_branches["checked"]
                else:
                    items[cursor]["checked"] = not items[cursor]["checked"]
            elif key in keyboard.ENTER:
                if ahead:  # Not confirmed by someone who saw the screen: it's drawn again instead.
                    continue
                if keys.refused("enter", lambda why: flash.set(why, "attention")):
                    continue
                break

        asked = delete_branches["checked"] and bool(branches)

        def work():
            # Only merged branches are deleted; pruning keeps branches (see actions.remove_many).
            pairs = [(e, asked and not worktrees.missing(e) and bool(worktrees.merged_into(self.repo, e)))
                     for e in checked]
            removed, pruned, deleted, failures = actions.remove_many(self.repo, self.session, pairs, terminals)
            unmerged = sum(asked and not delete and "branch" in e and e["name"] in removed for e, delete in pairs)
            return cleanup_done(len(removed), deleted, unmerged, len(pruned)), failures

        doing = "Pruning" if verb == "prune" else "Removing"
        done, failures = self.busy(f"{doing} {plural(len(checked), 'worktree')}…", work)
        if failures:  # What was done, then what wasn't.
            self.say((done + " " if done else "") + sentences(failures))
        else:
            self.say(done, "live")
        self.reload()
        return False

    @staticmethod
    def cleanup_name_width(items, width):
        """The branch column's width: room for the reason and at least a note's icon beside it."""
        reasons = max(len(cleanup_label(i["entry"])) for i in items)
        longest = max(len(i["entry"]["name"]) for i in items)
        return max(6, min(longest, width - 6 - 2 - reasons - 3))

    @staticmethod
    def cleanup_details(items, delete_branches, closes, width, session="", label=""):
        """Every candidate in full, for tab: its checkbox and branch, wrapped, then why it's offered
        and what removing it closes; then the branch option, unless delete_branches is None, when
        it isn't offered (see deletable)."""
        rows = []
        for item in items:
            entry = item["entry"]
            dirty = entry.get("dirty") is True
            box = ("[ ] ", ui.style("dim")) if dirty else ("[x] " if item["checked"] else "[ ] ", ui.style("select"))
            for i, line in enumerate(ui.wrap_path(entry["name"], width - 4)):
                rows.append([box if i == 0 else ("    ", 0), (line, ui.style("bold"))])
            text, tone = cleanup_text(entry, closes, session, label)
            rows += [[("    " + line, tone)] for line in ui.wrap(text, width - 4)] + [[]]
        if delete_branches is None:
            return [[]] + rows[:-1]
        label_text, explanation = branch_option(deletable(items), any(worktrees.missing(i["entry"]) for i in items))
        rows.append([("[x] " if delete_branches else "[ ] ", ui.style("select")), (label_text, 0)])
        rows += [[("    " + line, ui.style("dim"))] for line in ui.wrap(explanation, width - 4)]
        return [[]] + rows

    @staticmethod
    def cleanup_row(item, selected, items, closes, width):
        """A candidate: its checkbox, branch, and reason, then a note that shortens to its icon in a
        narrow pane: uncommitted changes, or panes that will close."""
        entry = item["entry"]
        reasons = max(len(cleanup_label(i["entry"])) for i in items)
        dirty = entry.get("dirty") is True
        groups = closes.get(entry["worktree"]) or []
        panes = sum(len(g["panes"]) for g in groups)
        if dirty:
            notes = [ui.word("dirty") + ", can't be cleaned up", ui.word("dirty")]
            icon, tone = util.ICONS["dirty"], ui.style("attention")
        elif groups:
            whole = len(groups) == 1 and groups[0]["whole"]
            notes = ["closes its tab" if whole else f"closes {panes} pane{'s' if panes != 1 else ''}"]
            icon, tone = util.ICONS["tab"], ui.style("dim")
        else:
            notes, icon, tone = [], "", 0
        name_width = RemovalScreens.cleanup_name_width(items, width)
        name = entry["name"] if len(entry["name"]) <= name_width else entry["name"][:name_width - 1] + "…"
        box = ("[ ] ", ui.style("dim")) if dirty else ("[x] " if item["checked"] else "[ ] ", ui.style("select"))
        row = [ui.pointer(selected), box, (name.ljust(name_width), ui.style("bold") if selected else 0),
               ("  " + cleanup_label(entry).ljust(reasons), cleanup_role(entry))]
        if icon:
            room = width - 6 - name_width - 2 - reasons - 2
            note = next((n for n in notes if len(icon) + 1 + len(n) <= room), "")
            row.append(("  " + icon + (" " + note if note else ""), tone))
        return ui.Selected(row) if selected else row

    @staticmethod
    def cleanup_detail(items, cursor, closes, width, lines=3, session="", label=""):
        """The selected row explained in up to lines rows: why it can't be cleaned up, or why it's
        offered and what removing it closes; then its branch in full, or cut in the middle. On the
        branch option, what it does. Either way it's a note under the options, lined up with their
        labels (see LABEL_AT)."""
        if cursor >= len(items):
            explanation = branch_option(deletable(items), any(worktrees.missing(i["entry"]) for i in items))[1]
            return note_rows(explanation, width, LABEL_AT, ui.style("dim"))[:lines]
        entry = items[cursor]["entry"]
        text, tone = cleanup_text(entry, closes, session, label)
        room = max(1, width - LABEL_AT)
        reason = ui.wrap(text, room)
        name = ui.wrap_path(entry["name"], room)
        if len(reason) + len(name) > lines:
            name = [ui.middle(entry["name"], room)[0]] if lines > 1 else []
        if len(reason) + len(name) > lines:
            reason = reason[:max(1, lines - len(name))]
            reason[-1] = reason[-1][:room - 1] + "…"
        indent = " " * LABEL_AT
        return [[(indent + line, tone)] for line in reason] + [[(indent + line, ui.style("bold"))] for line in name]
