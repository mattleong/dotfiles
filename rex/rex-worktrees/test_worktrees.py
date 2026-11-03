import curses
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import call, patch

# The modules live next to this file, the way Rex runs them.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import branching  # noqa: E402
import github  # noqa: E402
import keyboard  # noqa: E402
import matching  # noqa: E402
import tabs  # noqa: E402
import ui  # noqa: E402
import util  # noqa: E402
import worktrees  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class FakeRex:
    """Stands in for the rex CLI: records calls and answers session.list, new_window, and create."""
    def __init__(self, sessions=()):
        self.sessions = [{"session_id": sid, "label": label} for sid, label in sessions]
        self.calls = []

    def __call__(self, *args, cwd=None):
        self.calls.append(args)
        if "session.list" in args:
            return json.dumps({"sessions": self.sessions}).encode()
        if "session.new_window" in args:
            return b'{"window_id": "window:new"}'
        if "session.create" in args:
            request = json.loads(args[-1])
            self.sessions.append({"session_id": "session:repo", "label": request["label"]})
            return b'{"session_id": "session:repo", "initial_windows": [{"window_id": "window:first"}]}'
        return b"{}"

    def request(self, method):
        args = next(a for a in self.calls if method in a)
        return args, json.loads(args[-1])


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo with spaces"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "initial")
        home = patch.object(Path, "home", return_value=Path(self.tmp.name) / "home")
        home.start()
        self.addCleanup(home.stop)

    def add(self, name, branch):
        path = Path(self.tmp.name) / name
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path))
        return path

    def test_porcelain_paths_and_stable_destination(self):
        other = self.add("feature checkout", "feature/test")
        entries = worktrees.worktrees(str(self.repo))
        self.assertEqual([os.path.realpath(e["worktree"]) for e in entries], [os.path.realpath(self.repo), os.path.realpath(other)])
        self.assertEqual(entries[1]["branch"], "refs/heads/feature/test")
        self.assertEqual(branching.new_path(str(self.repo), "another/test"), branching.new_path(str(other), "another/test"))
        # Folders are named after the branch; a hash is added only when that folder is taken.
        first = branching.new_path(str(self.repo), "a/b")
        self.assertEqual(first.name, "a-b")
        first.mkdir(parents=True)
        second = branching.new_path(str(self.repo), "a-b")
        self.assertTrue(second.name.startswith("a-b-") and len(second.name) == len("a-b-") + 8)

    def test_entries_have_status_and_current_worktree(self):
        other = self.add("other", "other")
        (other / "untracked").write_text("changes")
        nested = other / "nested"
        nested.mkdir()
        with patch.object(tabs, "terminal_cwds", return_value=[({"window_id": "w"}, str(other))]):
            entries = actions.load_entries(str(nested), "session:1")
        main, current = entries
        self.assertTrue(main["main"])
        self.assertFalse(main["current"] or main["dirty"] or main["open"])
        self.assertTrue(current["current"] and current["dirty"] and current["open"])
        self.assertEqual(current["name"], "other")

    def test_line_editing_at_cursor(self):
        edit, c = keyboard.edit_text, curses
        self.assertEqual(edit("helo", 4, c.KEY_LEFT), ("helo", 3))
        self.assertEqual(edit("helo", 3, "l"), ("hello", 4))
        self.assertEqual(edit("hello", 0, c.KEY_LEFT), ("hello", 0))
        self.assertEqual(edit("hello", 5, c.KEY_RIGHT), ("hello", 5))
        self.assertEqual(edit("hello", 2, "\x7f"), ("hllo", 1))
        self.assertEqual(edit("hello", 0, "\x7f"), ("hello", 0))
        self.assertEqual(edit("hello", 1, c.KEY_DC), ("hllo", 1))
        self.assertEqual(edit("hello", 3, "\x01"), ("hello", 0))
        self.assertEqual(edit("hello", 0, "\x05"), ("hello", 5))
        self.assertEqual(edit("hello world", 8, "\x15"), ("rld", 0))
        self.assertEqual(edit("hello world", 5, "\x0b"), ("hello", 5))
        self.assertEqual(edit("foo bar baz", 7, "\x17"), ("foo  baz", 4))

    def test_read_key_handles_focus_reports(self):
        class Screen:
            def __init__(self, keys):
                self.keys = list(keys)
            def get_wch(self):
                if not self.keys:
                    raise curses.error()
                return self.keys.pop(0)
            def timeout(self, ms):
                pass
        unget = lambda screen: (lambda char: screen.keys.insert(0, char))
        with patch.dict(util.STATE, {"session": "session:1"}):
            screen = Screen(["\x1b", "[", "I"])
            self.assertIsNone(keyboard.read_key(screen))
            screen = Screen(["\x1b", "[", "O"])
            with patch.object(tabs, "picker_focused", return_value=True):
                self.assertIsNone(keyboard.read_key(screen))  # The whole app lost focus.
            screen = Screen(["\x1b", "[", "O"])
            with patch.object(tabs, "picker_focused", return_value=False), self.assertRaises(keyboard.FocusLost):
                keyboard.read_key(screen)
        with patch.dict(util.STATE, {"session": "session:1", "phone": True}):
            screen = Screen(["\x1b", "[", "O"])
            with patch.object(tabs, "picker_focused", return_value=False):
                self.assertIsNone(keyboard.read_key(screen))  # Swiping past other panes on a phone.
            screen = Screen(["\x1b"])
            self.assertEqual(keyboard.read_key(screen), "\x1b")
            screen = Screen(["\x1b", "x"])
            with patch.object(curses, "unget_wch", unget(screen)):
                self.assertEqual(keyboard.read_key(screen), "\x1b")
                self.assertEqual(keyboard.read_key(screen), "x")

    def test_tracking_labels(self):
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.repo, "branch", "--set-upstream-to=origin/main", "main")
        git(self.repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "ahead")
        git(self.repo, "branch", "deleted")
        git(self.repo, "config", "branch.deleted.remote", "origin")
        git(self.repo, "config", "branch.deleted.merge", "refs/heads/deleted")
        tracks = worktrees.tracking(str(self.repo))
        self.assertEqual(tracks["refs/heads/main"], ["1 ahead"])
        self.assertEqual(tracks["refs/heads/deleted"], ["remote deleted"])
        entry = {"worktree": str(self.repo), "main": True, "open": True, "dirty": True, "track": ["1 ahead", "2 behind"]}
        labels = [t for t, _ in ui.badges(entry)]
        self.assertEqual([t[2:] for t in labels], ["1 ahead", "2 behind", "main checkout", "tab open", "uncommitted"])
        self.assertEqual(ui.badges_width(entry), len(" · ".join(labels)))

    def test_columns_align_and_hide_empty_ones(self):
        entries = [{"name": "main", "worktree": "/w/repo", "main": True, "open": True, "dirty": False, "track": []},
                   {"name": "a-very-long-branch-name-that-goes-past-forty-characters", "worktree": "/w/x",
                    "open": False, "dirty": True, "track": ["2 behind"]}]
        widths = ui.column_widths(entries)
        self.assertEqual(widths["PR"], 0)
        self.assertEqual(widths["BRANCH"], 40)
        self.assertEqual(widths["SYNC"], len("x 2 behind"))
        rows = [ui.entry_row(e, False, widths) for e in entries]
        text = ["".join(t for t, _ in row) for row in rows]
        header = "".join(t for t, _ in ui.header_row(widths))
        self.assertNotIn("PR", header)
        self.assertTrue(text[1][4:].startswith("a-very-long-branch-name-that-goes-past-…"))
        # Every column starts at the same position in each row and in the header.
        for label in ("SYNC", "STATE"):
            self.assertIn(label, header)
        self.assertEqual(text[0].index(util.ICONS["main"]), text[1].index(util.ICONS["dirty"]))
        self.assertEqual(header.index("STATE"), text[0].index(util.ICONS["main"]))

    def test_narrow_panes_narrow_the_branch_then_turn_compact(self):
        path = str(self.repo)
        entries = [{"name": "main", "worktree": path, "main": True, "open": False, "dirty": True, "track": []},
                   {"name": "feature/a-long-branch-name", "worktree": path, "open": False, "dirty": False,
                    "track": ["2 ahead"], "pr": {"number": 12, "state": "OPEN"}}]
        full = ui.column_widths(entries)
        self.assertFalse(full["compact"])
        narrowed = ui.column_widths(entries, 92)
        self.assertFalse(narrowed["compact"])
        self.assertLess(narrowed["BRANCH"], full["BRANCH"])
        compact = ui.column_widths(entries, 40)
        self.assertTrue(compact["compact"])
        for widths, space in ((narrowed, 92), (compact, 40)):
            for entry in entries:
                row = ui.entry_row(entry, False, widths)
                self.assertLessEqual(len("".join(t for t, _ in row[:-1]).rstrip()), space)
        text = "".join(t for t, _ in ui.entry_row(entries[1], False, compact))
        self.assertTrue(text.rstrip().endswith(f"{util.ICONS['pr']}#12 {util.ICONS['ahead']}2"))

    def test_rows_fit_the_pane(self):
        hints = ui.footer(enter="open", move=True, slash="filter", actions=[("s", "split"), ("n", "new")])
        keys = lambda width: [t for t, _ in ui.fit(hints, width)][::2]
        self.assertEqual(keys(200), ["j/k", "/", "enter", "s", "n", "?", "q"])
        self.assertEqual(keys(49), ["/", "enter", "s", "?", "q"])
        self.assertEqual(keys(30), ["/", "enter", "?"])
        header = ui.header("Worktrees", "repo", "4 worktrees")
        self.assertEqual([t for t, _ in ui.fit(header, 16)], ["Worktrees", "  repo"])
        # Cutting through text adds an ellipsis; cutting through padding doesn't.
        self.assertEqual(ui.fit([("branch-name", 0), ("  ", 0), ("3d", 0)], 8), [("branch-…", 0)])
        self.assertEqual(ui.fit([("branch", 0), ("     ", 0), ("3d", 0)], 8), [("branch", 0), ("  ", 0)])
        self.assertEqual(ui.wrap("one two three", 8), ["one two", "three"])

    def test_default_branch_follows_the_remote(self):
        cwd = str(self.repo)
        self.assertEqual(worktrees.default_branch(cwd), "main")
        git(self.repo, "remote", "add", "upstream", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/upstream/trunk", "HEAD")
        git(self.repo, "symbolic-ref", "refs/remotes/upstream/HEAD", "refs/remotes/upstream/trunk")
        self.assertEqual(worktrees.default_branch(cwd), "upstream/trunk")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
        self.assertEqual(worktrees.default_branch(cwd), "origin/develop")

    def test_cleanup_reason(self):
        base = {"worktree": str(self.repo), "track": []}
        self.assertEqual(worktrees.cleanup_reason({**base, "pr": {"number": 3, "state": "MERGED"}}), "#3 merged")
        self.assertEqual(worktrees.cleanup_reason({**base, "track": ["remote deleted"]}), "remote deleted")
        self.assertIsNone(worktrees.cleanup_reason({**base, "pr": {"number": 3, "state": "OPEN"}}))
        self.assertIsNone(worktrees.cleanup_reason({**base, "main": True, "track": ["remote deleted"]}))
        self.assertIsNone(worktrees.cleanup_reason({**base, "locked": "", "track": ["remote deleted"]}))

    def test_remove_many_closes_tabs_once_at_the_end(self):
        one, two = self.add("one", "one"), self.add("two", "two")
        entries = {e.get("branch"): {**e, "name": e["branch"][11:]} for e in worktrees.worktrees(str(self.repo))[1:]}
        window = {"window_id": "window:shared"}
        with patch.object(tabs, "windows_for_path", return_value=[window]), \
                patch.object(tabs, "close_worktree_tabs") as close:
            removed, failures = actions.remove_many(str(self.repo), "session:1",
                                                     [(entries["refs/heads/one"], True), (entries["refs/heads/two"], False)])
        self.assertEqual((sorted(removed), failures), (["one", "two"], []))
        close.assert_called_once_with("session:1", [window])
        self.assertFalse(one.exists() or two.exists())
        self.assertNotEqual(subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--verify", "--quiet", "refs/heads/one"]).returncode, 0)
        git(self.repo, "rev-parse", "--verify", "refs/heads/two")

    def test_sort_modes(self):
        entries = [{"name": "main", "main": True, "time": 1, "worktree": "/a"},
                   {"name": "old", "time": 2, "worktree": "/b", "pr": {"state": "MERGED", "number": 1}},
                   {"name": "dirty", "time": 3, "dirty": True, "worktree": "/c"},
                   {"name": "open", "time": 4, "worktree": "/d", "pr": {"state": "OPEN", "number": 2}}]
        names = lambda mode: [e["name"] for e in matching.sort_entries(entries, mode)]
        self.assertEqual(names("git"), ["main", "old", "dirty", "open"])
        self.assertEqual(names("recent"), ["open", "dirty", "old", "main"])
        with patch.object(worktrees, "missing", return_value=False):
            self.assertEqual(names("state"), ["main", "dirty", "open", "old"])

    def test_pull_request_cache_round_trip(self):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(Path(self.tmp.name) / "cache")}):
            self.assertEqual(github.load_cached_prs(str(self.repo)), {})
            github.save_cached_prs(str(self.repo), {"a": {"number": 1, "state": "OPEN"}})
            self.assertEqual(github.load_cached_prs(str(self.repo)), {"a": {"number": 1, "state": "OPEN"}})

    def test_copy_and_editor(self):
        with patch.object(shutil, "which", side_effect=lambda name: name == "pbcopy"), \
                patch.object(subprocess, "run") as run:
            actions.copy_to_clipboard("/w/path")
            run.assert_called_once_with(["pbcopy"], input=b"/w/path", check=True)
        with patch.object(shutil, "which", return_value=None), self.assertRaises(RuntimeError):
            actions.copy_to_clipboard("x")
        rex = FakeRex([("session:repo", "repo")])
        with patch.object(worktrees, "repo_name", return_value="repo"), patch.object(util, "run", rex), \
                patch.dict(util.STATE, {"navigated": False, "repo_sessions": []}):
            tabs.open_editor("session:1", {"worktree": "/w/t", "branch": "refs/heads/t"})
            args, request = rex.request("session.new_window")
            self.assertEqual(args[3:5], ("-s", "session:repo"))
            self.assertEqual(request["layout"]["block"]["options"]["initial_input"], 'eval "${VISUAL:-${EDITOR:-vi}}" .\n')
            self.assertTrue(util.STATE["navigated"])

    def test_terminal_scan_uses_one_rex_call(self):
        found = [{"session_id": "session:1", "window": {"window_id": "window:a"}, "cwds": ["/w/a", "/w/a/src"]},
                 {"session_id": "session:repo", "window": {"window_id": "window:b"}, "cwds": {}}]
        with patch.object(util, "run", return_value=json.dumps({"windows": found}).encode()) as run, \
                patch.dict(util.STATE, {"repo_sessions": ["session:repo"]}), patch.dict(os.environ, {"REX_BLOCK": "block:picker"}):
            pairs = tabs.terminal_cwds("session:1")
        self.assertEqual([(w["window_id"], w["session_id"], cwd) for w, cwd in pairs],
                         [("window:a", "session:1", "/w/a"), ("window:a", "session:1", "/w/a/src")])
        args = run.call_args[0]
        self.assertEqual(args[:3], ("rex", "do", "-e"))
        self.assertEqual(json.loads(args[5]), {"sessions": ["session:1", "session:repo"], "skip": "block:picker"})
        for failure in (RuntimeError("old rex"), b'{"error": "not attached"}'):
            with patch.object(util, "run", side_effect=[failure] if isinstance(failure, Exception) else None,
                              return_value=failure), \
                    patch.object(tabs, "terminal_cwds_slowly", return_value=["slow"]) as slow:
                self.assertEqual(tabs.terminal_cwds("session:1"), ["slow"])
                slow.assert_called_once()

    def test_base_entries_can_defer_details(self):
        raw = worktrees.worktrees(str(self.repo))
        quick = worktrees.base_entries(str(self.repo), raw, details=False)
        self.assertEqual((quick[0]["time"], quick[0]["track"]), (None, []))
        worktrees.fill_details(str(self.repo), quick)
        self.assertIsNotNone(quick[0]["time"])

    def test_flash_messages_fade(self):
        flash = ui.Flash()
        self.assertEqual((flash.row(), flash.wait(-1), flash.wait(150)), ([], -1, 150))
        with patch.object(ui.time, "monotonic", return_value=100.0):
            flash.set("Saved", "green")
        with patch.object(ui.time, "monotonic", return_value=101.0):
            self.assertEqual(flash.wait(-1), 3050)  # Wakes up when the message fades.
            self.assertEqual(flash.wait(150), 150)
            self.assertIn("Saved", flash.row()[0][0])
        with patch.object(ui.time, "monotonic", return_value=106.0):
            self.assertEqual(flash.row(), [])
        with patch.object(ui.time, "monotonic", return_value=100.0):
            flash.set("Failed")
        with patch.object(ui.time, "monotonic", return_value=106.0):
            self.assertIn("Failed", flash.row()[0][0])  # Errors stay longer.

    def test_source_label(self):
        self.assertEqual(branching.source_label({"kind": "local", "name": "a", "ref": "a"}), "local")
        self.assertEqual(branching.source_label({"kind": "remote", "name": "feat/a", "ref": "origin/feat/a"}), "remote")
        self.assertEqual(branching.source_label({"kind": "remote", "name": "a", "ref": "upstream/a"}), "upstream")

    def test_vim_motions(self):
        motion = keyboard.motion
        self.assertEqual(motion("j", 0, 5, 4, None), (1, None))
        self.assertEqual(motion("k", 0, 5, 4, None), (0, None))
        self.assertEqual(motion("G", 0, 5, 4, None), (4, None))
        self.assertEqual(motion("g", 3, 5, 4, None), (3, "g"))
        self.assertEqual(motion("g", 3, 5, 4, "g"), (0, None))
        self.assertEqual(motion("\x04", 0, 10, 4, None), (2, None))
        self.assertEqual(motion("\x15", 1, 10, 4, None), (0, None))
        self.assertEqual(motion("x", 1, 10, 4, None), (None, None))

    def test_normalize_branch_names(self):
        cases = {
            "test branch": "test-branch",
            " feature / my  branch ": "feature/my-branch",
            "foo..bar.lock": "foo-bar-lock",
            "-bad~name?": "bad-name",
            "HEAD": "HEAD-branch",
            "///": "",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                normalized = branching.normalize_branch(source)
                self.assertEqual(normalized, expected)
                if normalized:
                    git(self.repo, "check-ref-format", "--branch", normalized)

    def setup_branches(self):
        git(self.repo, "branch", "existing")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
        cwd = str(self.repo)
        return cwd, branching.load_branches(cwd, worktrees.worktrees(cwd))

    def suggestion(self, branches, ref):
        return next(s for s in branches["suggestions"] if s["ref"] == ref)

    def test_plan_new_and_existing_branches(self):
        cwd, branches = self.setup_branches()
        new = branching.plan_branch(cwd, branches, "test branch")
        self.assertEqual((new["branch"], new["action"]), ("test-branch", "create new branch test-branch from main"))
        self.assertNotIn("error", new)
        local = branching.plan_branch(cwd, branches, "", self.suggestion(branches, "existing"))
        self.assertEqual(local["action"], "check out existing branch existing")
        remote = branching.plan_branch(cwd, branches, "", self.suggestion(branches, "origin/remote-only"))
        self.assertEqual(remote["action"], "check out origin/remote-only as local branch remote-only, tracking it")
        # A typed name only ever creates a new branch; existing ones are picked from the list.
        self.assertIn("Already checked out", branching.plan_branch(cwd, branches, "main")["error"])
        self.assertIn("select it under Existing branches", branching.plan_branch(cwd, branches, "existing")["error"])
        self.assertIn("origin/remote-only exists", branching.plan_branch(cwd, branches, "remote-only")["error"])

    def test_branch_order_recent_local_then_remote_then_old_local(self):
        def commit_at(branch, when):
            env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
            subprocess.run(["git", "-C", str(self.repo), "checkout", "-q", "-b", branch, "main"], check=True)
            subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q",
                            "--allow-empty", "-m", branch], check=True, env=env)
        commit_at("old-local", "2020-01-01T00:00:00")
        commit_at("new-local", "2030-01-01T00:00:00")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/remote-only", "old-local")
        now = 1893456000 + 86400  # 2030-01-02
        branches = branching.load_branches(str(self.repo), worktrees.worktrees(str(self.repo)), now)
        self.assertEqual([s["ref"] for s in branches["suggestions"]], ["new-local", "origin/remote-only", "old-local"])

    def test_create_worktree_for_each_plan(self):
        cwd, branches = self.setup_branches()
        plans = [branching.plan_branch(cwd, branches, "new branch"),
                 branching.plan_branch(cwd, branches, "", self.suggestion(branches, "existing")),
                 branching.plan_branch(cwd, branches, "", self.suggestion(branches, "origin/remote-only"))]
        for plan in plans:
            with self.subTest(branch=plan["branch"]):
                branching.create_worktree(cwd, plan)
                head = git(plan["path"], "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()
                self.assertEqual(head, plan["branch"])
        upstream = git(self.repo, "rev-parse", "--abbrev-ref", "remote-only@{upstream}").stdout.decode().strip()
        self.assertEqual(upstream, "origin/remote-only")
        retry = branching.plan_branch(cwd, {**branches, "checked_out": {}, "local": set()}, "new branch")
        self.assertNotEqual(retry["path"], plans[0]["path"])  # The plain name is taken, so it gets a hash.
        retry["path"].mkdir(parents=True)
        self.assertIn("Destination already exists",
                      branching.plan_branch(cwd, {**branches, "checked_out": {}, "local": set()}, "new branch")["error"])

    def test_removal_problems_and_changed_files(self):
        dirty = self.add("dirty", "dirty")
        (dirty / "untracked").write_text("changes")
        clean = self.add("clean", "clean")
        entries = {e.get("branch"): e for e in worktrees.worktrees(str(self.repo))}
        main, dirty_entry, clean_entry = (entries["refs/heads/" + b] for b in ("main", "dirty", "clean"))
        main["main"] = True
        self.assertIn("main worktree", worktrees.removal_problem(main))
        self.assertIsNone(worktrees.removal_problem(dirty_entry))
        self.assertIn("locked", worktrees.removal_problem({**clean_entry, "locked": ""}))
        self.assertIn("press p to prune", worktrees.removal_problem({**clean_entry, "prunable": ""}))
        self.assertEqual(worktrees.changed_files(dirty_entry), ["?? untracked"])
        self.assertEqual(worktrees.changed_files(clean_entry), [])
        self.assertTrue(dirty.exists() and clean.exists())

    def test_remove_worktree_then_close_tabs(self):
        other = self.add("clean", "clean")
        entry = worktrees.worktrees(str(self.repo))[1]
        windows = [{"window_id": "window:1"}]
        with patch.object(tabs, "close_worktree_tabs") as close:
            actions.remove_worktree(str(self.repo), "session:1", entry, windows)
        self.assertFalse(other.exists())
        close.assert_called_once_with("session:1", windows)
        git(self.repo, "rev-parse", "--verify", "refs/heads/clean")

    def test_failed_git_removal_leaves_tabs_open(self):
        other = self.add("dirty", "dirty")
        (other / "untracked").write_text("changes")
        entry = worktrees.worktrees(str(self.repo))[1]
        with patch.object(tabs, "close_worktree_tabs") as close, self.assertRaises(RuntimeError):
            actions.remove_worktree(str(self.repo), "session:1", entry, [{"window_id": "window:1"}])
        close.assert_not_called()

    def test_force_remove_and_delete_merged_branch(self):
        other = self.add("merged", "merged")
        (other / "untracked").write_text("changes")
        entry = {**worktrees.worktrees(str(self.repo))[1], "name": "merged"}
        self.assertEqual(worktrees.merged_into(str(self.repo), entry), "main")
        with patch.object(tabs, "close_worktree_tabs"):
            actions.remove_worktree(str(self.repo), "session:1", entry, [], force=True, delete_branch=True)
        self.assertFalse(other.exists())
        self.assertFalse(subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--verify", "--quiet", "refs/heads/merged"]).returncode == 0)

    def test_unmerged_branch_is_not_offered_for_deletion(self):
        other = self.add("ahead", "ahead")
        git(other, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "ahead")
        entry = {**worktrees.worktrees(str(self.repo))[1], "name": "ahead"}
        self.assertIsNone(worktrees.merged_into(str(self.repo), entry))

    def test_prune_missing_worktree(self):
        other = self.add("gone", "gone")
        subprocess.run(["rm", "-rf", str(other)], check=True)
        stale = [e for e in worktrees.worktrees(str(self.repo)) if "prunable" in e]
        self.assertEqual(len(stale), 1)
        worktrees.prune_worktrees(str(self.repo))
        self.assertEqual(len(worktrees.worktrees(str(self.repo))), 1)
        git(self.repo, "rev-parse", "--verify", "refs/heads/gone")

    def test_recent_commits_prefers_commits_not_in_base(self):
        description, commits = worktrees.recent_commits(str(self.repo))
        self.assertEqual((description, [c[1] for c in commits]), ("Recent commits", ["initial"]))
        self.assertEqual(commits[0][2], "Test")
        other = self.add("feature", "feature")
        for message in ("one", "two"):
            git(other, "-c", "user.name=Dev", "-c", "user.email=d@e", "commit", "-q", "--allow-empty", "-m", message)
        description, commits = worktrees.recent_commits(str(other), base="main")
        self.assertEqual(description, "2 commits not in main")
        self.assertEqual([(c[1], c[2]) for c in commits], [("two", "Dev"), ("one", "Dev")])
        self.assertEqual(worktrees.recent_commits(str(self.repo / "missing")), ("", []))

    def test_ages(self):
        now = 1_000_000_000
        cases = {now: "now", now - 120: "2m", now - 3 * 3600: "3h", now - 2 * 86400: "2d", now - 15 * 86400: "2w",
                 now - 70 * 86400: "2mo", now - 400 * 86400: "1y", None: ""}
        for stamp, expected in cases.items():
            self.assertEqual(ui.short_age(stamp, now), expected)
        head = git(self.repo, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertIn(head, worktrees.commit_times(str(self.repo), [head]))
        self.assertEqual(worktrees.base_entries(str(self.repo))[0]["time"], worktrees.commit_times(str(self.repo), [head])[head])

    def test_pull_requests_keep_newest_per_branch(self):
        mine = b'[{"number": 5, "state": "MERGED", "headRefName": "a", "isDraft": false}]'
        everyone = b'[{"number": 9, "state": "OPEN", "headRefName": "a", "isDraft": true}, {"number": 7, "state": "CLOSED", "headRefName": "b", "isDraft": false}]'
        with patch.object(util, "run", side_effect=[mine, everyone]):
            prs = github.pull_requests("/repo")
        self.assertEqual({k: v["number"] for k, v in prs.items()}, {"a": 9, "b": 7})
        labels = [t for t, _ in ui.badges({"worktree": "/nonexistent-but-ok", "pr": prs["a"], "dirty": False, "open": False})]
        self.assertIn(util.ICONS["pr"] + " #9 draft", labels)
        with patch.object(util, "run", side_effect=RuntimeError("not a GitHub repo")):
            self.assertEqual(github.pull_requests("/repo"), {})

    def test_open_in_background_and_split(self):
        rex = FakeRex([("session:repo", "repo")])
        with patch.object(tabs, "windows_for_path", return_value=[]), patch.object(worktrees, "repo_name", return_value="repo"), \
                patch.object(util, "run", rex), patch.dict(util.STATE, {"navigated": False, "repo_sessions": []}):
            self.assertEqual(tabs.open_tab("session:1", {"worktree": "/w/t", "branch": "refs/heads/t"}, focus=False), "window:new")
            args, request = rex.request("session.new_window")
            self.assertEqual((args[4], request["focus"], request["window_label"]), ("session:repo", False, "t"))
            self.assertFalse(util.STATE["navigated"])
        with patch.object(tabs, "windows_for_path", return_value=[{"window_id": "window:1"}]), patch.object(util, "run") as run:
            self.assertIsNone(tabs.open_tab("session:1", {"worktree": "/w/t"}, focus=False))
            run.assert_not_called()
        with patch.object(util, "run") as run, patch.dict(util.STATE, {"navigated": False}):
            tabs.open_split("session:1", {"worktree": "/w/t", "branch": "refs/heads/t"}, "block:origin")
            run.assert_called_once_with("rex", "split", "-s", "session:1", "-b", "block:origin", "--split=right", "-c", "/w/t", "--label", "t")
            self.assertTrue(util.STATE["navigated"])
        with self.assertRaises(RuntimeError):
            tabs.open_split("session:1", {"worktree": "/w/t"}, "")

    def test_fuzzy_filter_ranks_and_highlights(self):
        entries = [{"name": "fix/login-bug", "worktree": "/w/a"}, {"name": "feature/login", "worktree": "/w/b"},
                   {"name": "main", "worktree": "/w/c"}]
        ranked = [{"name": "l-x-o-x-g", "worktree": "/w/d"}, {"name": "logs", "worktree": "/w/e"}]
        self.assertEqual([e["name"] for e in matching.filter_entries(ranked, "log")], ["logs", "l-x-o-x-g"])
        self.assertEqual([e["name"] for e in matching.filter_entries(entries, "flog")], ["fix/login-bug", "feature/login"])
        self.assertEqual(matching.filter_entries(entries, "zzz"), [])
        score, name_hits, path_hits = matching.match_entry(entries[2], "mn")
        self.assertEqual(name_hits, {0, 3})
        self.assertIsNotNone(matching.match_entry(entries[2], "w/c"))  # Matches the full path...
        self.assertEqual(matching.match_entry({"name": "x", "worktree": "/w/sinch-email"}, "inch")[2], {1, 2, 3, 4})  # ...highlights the folder.

    def test_worktree_sources(self):
        home = str(Path.home())
        cases = {home + "/.herdr/worktrees/r/a": "herdr", home + "/.t3/worktrees/r/a": "t3",
                 home + "/.local/share/rex/worktrees/r/a": "rex", "/repo/.claude/worktrees/a": "claude", "/repo-a": ""}
        for path, source in cases.items():
            self.assertEqual(worktrees.worktree_source({"worktree": path}), source)

    def test_new_branch_from_default_base_does_not_track(self):
        git(self.repo, "checkout", "-q", "-b", "feature")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "feature work")
        cwd = str(self.repo)
        branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
        self.assertEqual([b["label"] for b in branches["bases"]], ["main", "feature"])
        plan = branching.plan_branch(cwd, branches, "from main", None, branches["bases"][0])
        self.assertEqual(plan["action"], "create new branch from-main from main")
        branching.create_worktree(cwd, plan)
        self.assertEqual(git(cwd, "rev-parse", "from-main").stdout, git(cwd, "rev-parse", "main").stdout)
        upstream = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "from-main@{upstream}"], capture_output=True)
        self.assertNotEqual(upstream.returncode, 0)

    def test_setup_command_types_into_new_tab(self):
        git(self.repo, "config", "rex.worktreeSetup", "make setup")
        self.assertEqual(worktrees.setup_command(str(self.repo)), "make setup")
        rex = FakeRex([("session:repo", "repo")])
        with patch.object(tabs, "windows_for_path", return_value=[]), patch.object(worktrees, "repo_name", return_value="repo"), \
                patch.object(util, "run", rex), patch.dict(util.STATE, {"repo_sessions": []}):
            tabs.open_tab("session:1", {"worktree": "/tmp/test", "branch": "refs/heads/topic"}, None, "make setup")
        _, request = rex.request("session.new_window")
        self.assertEqual(request["layout"]["block"]["options"], {"cwd": "/tmp/test", "initial_input": "make setup\n"})

    def test_close_picker_window_last(self):
        own = {"window_id": "window:own", "layers": [{"blocks": [{"block_id": "block:picker"}]}]}
        other = {"window_id": "window:other", "layers": []}
        with patch.dict(os.environ, {"REX_BLOCK": "block:picker"}), patch.object(util, "run") as run:
            tabs.close_worktree_tabs("session:1", [own, other])
            self.assertEqual(run.call_args_list, [
                call("rex", "window", "close", "-s", "session:1", "window:other"),
                call("rex", "window", "close", "-s", "session:1", "window:own"),
            ])

    def test_windows_for_path_matches_nested_paths_once(self):
        window, other = {"window_id": "window:1"}, {"window_id": "window:2"}
        terminals = [(window, "/w/repo/src"), (window, "/w/repo"), (other, "/w/repository")]
        self.assertEqual(tabs.windows_for_path("session:1", "/w/repo", terminals), [window])

    def test_restore_focus_only_when_no_tab_was_opened(self):
        with patch.dict(util.STATE, {"navigated": False}), patch.object(util, "run") as run:
            tabs.restore_focus("session:1", "block:origin")
            run.assert_called_once_with("rex", "focus", "-s", "session:1", "block:origin")
        with patch.dict(util.STATE, {"navigated": True}), patch.object(util, "run") as run:
            tabs.restore_focus("session:1", "block:origin")
            run.assert_not_called()
        with patch.dict(util.STATE, {"navigated": False}), patch.object(util, "run", side_effect=RuntimeError("gone")):
            tabs.restore_focus("session:1", "block:closed")

    def test_open_focuses_existing_window(self):
        window = {"window_id": "window:1", "session_id": "session:repo"}
        with patch.object(tabs, "windows_for_path", return_value=[window]), patch.object(util, "run") as run:
            self.assertEqual(tabs.open_tab("session:1", {"worktree": "/tmp/test", "branch": "refs/heads/topic"}), "window:1")
            run.assert_called_once_with("rex", "focus", "-s", "session:repo", "window:1")

    def test_open_creates_tab_and_shows_it_in_app(self):
        rex = FakeRex([("session:1", "crisp-fjord")])
        with patch.object(tabs, "windows_for_path", return_value=[]), patch.object(worktrees, "repo_name", return_value="dotfiles"), \
                patch.object(util, "run", rex), patch.dict(util.STATE, {"repo_sessions": []}):
            tabs.open_tab("session:1", {"worktree": "/tmp/test", "branch": "refs/heads/topic"}, "client:app")
            # No session for the repository yet, so one is created, named after it.
            _, request = rex.request("session.create")
            self.assertEqual((request["label"], request["initial_windows"][0]["window_label"]), ("dotfiles", "topic"))
            self.assertEqual(rex.calls[-1], ("rex", "-C", "client:app", "do", "session.select",
                                             "session_id=session:repo", "window_id=window:first"))
            self.assertIn("session:repo", util.STATE["repo_sessions"])
            # The next worktree tab goes into that same session.
            tabs.open_tab("session:1", {"worktree": "/tmp/other", "branch": "refs/heads/other"})
            args, _ = rex.request("session.new_window")
            self.assertEqual(args[4], "session:repo")

    def test_tabs_are_found_and_closed_in_the_repository_session(self):
        views = {"session:1": [{"window_id": "window:a", "layers": [{"blocks": [
                     {"block_id": "block:a", "creator_name": util.TERMINAL}]}]}],
                 "session:repo": [{"window_id": "window:b", "layers": [{"blocks": [
                     {"block_id": "block:b", "creator_name": util.TERMINAL}]}]}]}

        def fake_run(*args, cwd=None):
            if args[1] == "do":
                raise RuntimeError("no scripts")  # Exercise the per-pane fallback.
            if args[1:3] == ("session", "inspect"):
                return json.dumps({"windows": views[args[3]]}).encode()
            if args[1:3] == ("block", "call"):
                return json.dumps({"foreground": {"cwd": "/w/a" if args[6] == "block:a" else "/w/b/src"}}).encode()
            return b"{}"

        with patch.object(util, "run", side_effect=fake_run) as run, patch.dict(util.STATE, {"repo_sessions": ["session:repo"]}):
            windows = tabs.windows_for_path("session:1", "/w/b")
            self.assertEqual([(w["window_id"], w["session_id"]) for w in windows], [("window:b", "session:repo")])
            tabs.close_worktree_tabs("session:1", windows)
            self.assertEqual(run.call_args_list[-1], call("rex", "window", "close", "-s", "session:repo", "window:b"))

    def test_open_reports_app_switch_failure(self):
        def fake_run(*args):
            if "-C" in args:
                raise RuntimeError("Turn on Remote Control")
            return b"{}"
        with patch.object(tabs, "windows_for_path", return_value=[{"window_id": "window:1"}]), patch.object(util, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "Couldn't switch to the tab: Turn on Remote Control"):
                tabs.open_tab("session:1", {"worktree": "/tmp/test"}, "client:app")

    def test_repo_name_comes_from_main_worktree(self):
        other = self.add("elsewhere", "topic")
        self.assertEqual(worktrees.repo_name(str(other)), "repo with spaces")
        self.assertEqual(tabs.tab_label({"worktree": str(other), "branch": "refs/heads/topic"}), "topic")


if __name__ == "__main__":
    unittest.main()
