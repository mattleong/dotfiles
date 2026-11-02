"""The picker's state and background loading, shared by every screen."""
import os
import threading

import actions
import branching
import github
import keyboard
import screen_list
import screen_new
import screen_remove
import tabs
import ui
import util
import worktrees


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Manager(screen_list.ListScreen, screen_new.NewScreens, screen_remove.RemovalScreens):
    """The picker. Each screen's methods live in its own module, as a mixin."""

    def __init__(self, stdscr, cwd, session, client, origin=""):
        self.stdscr, self.session, self.client, self.origin = stdscr, session, client, origin
        self.origin_cwd = cwd
        raw = worktrees.worktrees(cwd)
        self.repo = raw[0]["worktree"]
        self.label = worktrees.repo_label(self.repo)
        self.default = None  # Filled in by a background thread; previews use it once it's known.
        self.terminals = None  # The latest scan of open tabs; None means scan again when needed.
        self.session_known = False
        threading.Thread(target=self.load_default, daemon=True).start()
        self.message, self.message_style = None, "red"
        self.commits = {}
        self.prs, self.prs_loading, self.sort = {}, False, "git"
        self.show_prs = github.pull_requests_enabled(self.repo)
        if self.show_prs:
            self.prs = github.load_cached_prs(self.repo)  # Shown at once, then refreshed in the background.
        self.reload(raw)
        if self.show_prs:
            self.load_prs()

    def load_prs(self):
        """Fetch pull requests on a daemon thread; badges appear when they arrive."""
        def work():
            try:
                prs = github.pull_requests(self.repo)
                if prs:  # Keep the cached ones if gh failed, such as when offline.
                    self.prs = prs
                    self.apply_prs()
                    github.save_cached_prs(self.repo, prs)
            finally:
                self.prs_loading = False
        self.prs_loading = True
        threading.Thread(target=work, daemon=True).start()

    def apply_prs(self):
        for entry in self.entries:
            if not entry.get("main") and "branch" in entry:
                entry["pr"] = self.prs.get(entry["name"])

    def busy(self, label, work):
        """Run work on a thread while showing a spinner. Keys pressed meanwhile are ignored."""
        result = {}

        def target():
            try:
                result["value"] = work()
            except BaseException as error:  # Re-raised on this thread below.
                result["error"] = error

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(0.15)  # Quick work finishes without a spinner flashing up.
        previous, frame, lost = util.STATE.get("timeout", -1), 0, False
        keyboard.set_timeout(self.stdscr, 80)
        # The spinner replaces the key hints, so the screen stays visible underneath.
        base = list(ui.LAST_FRAME["rows"]) or [[]]
        try:
            while thread.is_alive():
                spinner = [(SPINNER[frame % len(SPINNER)] + " " + label, ui.style("cyan"))]
                ui.render(self.stdscr, base[:-1] + [spinner])
                frame += 1
                try:
                    keyboard.read_key(self.stdscr)
                except keyboard.FocusLost:
                    lost = True  # Finish the work first; it mustn't be left half done.
        finally:
            keyboard.set_timeout(self.stdscr, previous)
        if "error" in result:
            raise result["error"]
        if lost:
            raise keyboard.FocusLost()
        return result.get("value")

    @property
    def cwd(self):
        # The original directory disappears if its worktree is removed.
        return self.origin_cwd if os.path.isdir(self.origin_cwd) else self.repo

    def load_default(self):
        try:
            self.default = worktrees.default_branch(self.repo)
        except (RuntimeError, OSError):
            pass

    def reload(self, raw=None):
        """List the worktrees at once, then fill in the slower details on background threads."""
        self.entries = entries = worktrees.base_entries(self.cwd, raw, details=False)
        self.commits = {}
        self.terminals = None
        self.details_loading = True
        self.apply_prs()
        actions.load_status_async(entries, self.session, scan=False)

        def details():
            try:
                worktrees.fill_details(self.cwd, entries)
            except (RuntimeError, OSError):
                pass
            finally:
                self.details_loading = False

        def scan():
            if not self.session_known:
                # Find the repository's session first, so tabs open there count too.
                try:
                    tabs.remember_repo_session(tabs.find_session(self.label))
                except (RuntimeError, ValueError, OSError):
                    pass
                self.session_known = True
            try:
                terminals = tabs.terminal_cwds(self.session)
            except (RuntimeError, ValueError, OSError):
                terminals = []
            for entry in entries:
                entry["open"] = any(util.inside(path, entry["worktree"]) for _, path in terminals)
            if self.entries is entries:
                self.terminals = terminals

        for target in (details, scan):
            threading.Thread(target=target, daemon=True).start()
        self.preload_branches()

    def preload_branches(self):
        """Load branches for New worktree in the background, so it opens without waiting."""
        holder, entries, cwd = {}, self.entries, self.cwd

        def work():
            try:
                holder["value"] = branching.load_branches(cwd, entries)
            except (RuntimeError, OSError, ValueError) as error:
                holder["error"] = error

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.branch_preload = (thread, holder)

    def branches(self):
        thread, holder = self.branch_preload
        if thread.is_alive():
            self.busy("Loading branches…", thread.join)
        if "error" in holder:
            raise holder["error"]
        return holder["value"]

    def windows_for(self, entry):
        """Tabs open in the worktree, from the latest scan when there is one."""
        return tabs.windows_for_path(self.session, entry["worktree"], self.terminals)

    def say(self, text, tone="red"):
        self.message, self.message_style = text, tone
