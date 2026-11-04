import curses
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

# The modules live next to this file, the way Rex runs them.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import app  # noqa: E402
import branching  # noqa: E402
import github  # noqa: E402
import keyboard  # noqa: E402
import matching  # noqa: E402
import screen_list  # noqa: E402
import screen_new  # noqa: E402
import screen_remove  # noqa: E402
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


class OutOfKeys(Exception):
    pass


class FakeScreen:
    """Stands in for a curses window: hands out keys, and records each frame drawn."""
    def __init__(self, keys, size=(16, 50)):
        self.keys, self.size, self.wait, self.frames = list(keys), size, -1, []

    def getmaxyx(self):
        return self.size

    def timeout(self, ms):
        self.wait = ms

    def get_wch(self):
        if self.keys:
            return self.keys.pop(0)
        if self.wait >= 0:
            raise curses.error()
        raise OutOfKeys()

    def text(self, frame=-1):
        return ["".join(t for t, *_ in row) for row in self.frames[frame]]


def drive(screen, run):
    """Run a screen with the fake window, drawing into screen.frames."""
    with patch.object(ui, "render", lambda stdscr, rows, cursor=None: screen.frames.append(rows)), \
            patch.object(keyboard, "unget", lambda key: screen.keys.insert(0, key)), \
            patch.object(keyboard, "pending_input", lambda stdscr: False), \
            patch.object(curses, "flushinp", lambda: None):
        return run()


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
        with patch.object(tabs, "terminal_cwds", return_value=[({"window_id": "w"}, str(other), "block:1")]):
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
        self.assertEqual(widths["BRANCH"], len(entries[1]["name"]))  # The full name, given the room.
        self.assertEqual(widths["SYNC"], len("x 2 behind"))
        rows = [ui.entry_row(e, False, widths) for e in entries]
        text = ["".join(t for t, _ in row) for row in rows]
        header = "".join(t for t, _ in ui.header_row(widths))
        self.assertNotIn("PR", header)
        self.assertTrue(text[1][4:].startswith(entries[1]["name"] + "  "))
        self.assertTrue(header.rstrip().endswith("FOLDER"))
        # Every column starts at the same position in each row and in the header.
        for label in ("SYNC", "STATE"):
            self.assertIn(label, header)
        self.assertEqual(text[0].index(util.ICONS["main"]), text[1].index(util.ICONS["dirty"]))
        self.assertEqual(header.index("STATE"), text[0].index(util.ICONS["main"]))

    def test_narrow_panes_shorten_the_labels_then_turn_compact(self):
        path = str(self.repo)
        name = "feature/a-long-branch-name-that-keeps-going"
        entries = [{"name": "main", "worktree": path, "main": True, "open": True, "dirty": True, "track": []},
                   {"name": name, "worktree": path, "open": False, "dirty": False,
                    "track": ["2 ahead"], "pr": {"number": 1234, "state": "MERGED"}}]
        full = ui.column_widths(entries)
        self.assertFalse(full["compact"] or full["short"])
        text = lambda entry, widths: "".join(t for t, _ in ui.entry_row(entry, False, widths))
        # First the labels shorten to words, keeping the whole branch.
        short = ui.column_widths(entries, 110)
        self.assertEqual((short["short"], short["compact"], short["BRANCH"]), (True, False, len(name)))
        self.assertIn("#1234 merged", text(entries[1], short))
        self.assertIn(util.ICONS["main"] + " main · " + util.ICONS["tab"] + " tab · " + util.ICONS["dirty"] + " changes",
                      text(entries[0], short))
        # Then the branch narrows, to MIN_BRANCH at most, and only then do the labels turn into icons.
        narrowed = ui.column_widths(entries, 95)
        self.assertTrue(narrowed["short"] and not narrowed["compact"])
        self.assertTrue(ui.MIN_BRANCH <= narrowed["BRANCH"] < len(name))
        compact = ui.column_widths(entries, 80)
        self.assertTrue(compact["compact"])
        for widths, space in ((short, 110), (narrowed, 95), (compact, 80)):
            for entry in entries:
                row = ui.entry_row(entry, False, widths)
                self.assertLessEqual(len("".join(t for t, _ in row[:-1]).rstrip()), space)
        self.assertTrue(text(entries[1], compact).rstrip().endswith(f"{util.ICONS['pr']}#1234 {util.ICONS['ahead']}2"))
        self.assertEqual([t for t, _ in ui.sync_cell({"track": ["1 behind", "remote deleted"]}, True)],
                         [util.ICONS["behind"] + "1", util.ICONS["deleted"] + " gone"])

    def test_folders_are_cut_in_the_middle(self):
        self.assertEqual(ui.middle("short", 10), ("short", set()))
        text, hits = ui.middle("reviewopusr2-login", 12, {0, 14, 17})
        self.assertEqual(text, "rev…r2-login")
        self.assertEqual({text[h] for h in hits}, {"r", "o", "n"})
        entries = [{"name": "a", "worktree": "/w/reviewopusr2-login", "track": []},
                   {"name": "b", "worktree": "/w/reviewopusr2-long", "track": []}]
        widths = dict(ui.column_widths(entries), FOLDER=12)
        rows = ["".join(t for t, _ in ui.entry_row(e, False, widths)) for e in entries]
        self.assertTrue(rows[0].endswith("rev…r2-login") and rows[1].endswith("rev…sr2-long"))

    def test_sort_note_outlasts_the_summary(self):
        header = ui.header("Worktrees", "repo", "4 worktrees · 1 uncommitted", "sorted by state")
        texts = lambda width: [t.strip() for t, _ in ui.fit(header, width)]
        self.assertEqual(texts(80), ["Worktrees", "repo", "4 worktrees · 1 uncommitted", "sorted by state"])
        self.assertEqual(texts(40), ["Worktrees", "repo", "sorted by state"])
        self.assertEqual(texts(20), ["Worktrees", "repo"])

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

    def test_remove_many_closes_panes_once_at_the_end(self):
        one, two = self.add("one", "one"), self.add("two", "two")
        entries = {e.get("branch"): {**e, "name": e["branch"][11:]} for e in worktrees.worktrees(str(self.repo))[1:]}
        # A tab with a pane in each removed worktree closes as a whole; one with a pane elsewhere doesn't.
        shared = {"window_id": "window:shared", "layers": [{"blocks": [{"block_id": "block:1"}, {"block_id": "block:2"}]}]}
        mixed = {"window_id": "window:mixed", "layers": [{"blocks": [{"block_id": "block:3"}, {"block_id": "block:4"}]}]}
        terminals = [(shared, str(one), "block:1"), (shared, str(two / "src"), "block:2"),
                     (mixed, str(one), "block:3"), (mixed, str(self.repo), "block:4")]
        with patch.object(tabs, "close_panes") as close:
            removed, failures = actions.remove_many(str(self.repo), "session:1",
                                                     [(entries["refs/heads/one"], True), (entries["refs/heads/two"], False)],
                                                     terminals)
        self.assertEqual((sorted(removed), failures), (["one", "two"], []))
        close.assert_called_once()
        groups = {g["window"]["window_id"]: (g["panes"], g["whole"]) for g in close.call_args[0][1]}
        self.assertEqual(groups, {"window:shared": (["block:1", "block:2"], True), "window:mixed": (["block:3"], False)})
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
        found = [{"session_id": "session:1", "window": {"window_id": "window:a"},
                  "cwds": [{"block_id": "block:1", "cwd": "/w/a"}, {"block_id": "block:2", "cwd": "/w/a/src"}]},
                 {"session_id": "session:repo", "window": {"window_id": "window:b"}, "cwds": {}}]
        with patch.object(util, "run", return_value=json.dumps({"windows": found}).encode()) as run, \
                patch.dict(util.STATE, {"repo_sessions": ["session:repo"]}), patch.dict(os.environ, {"REX_BLOCK": "block:picker"}):
            pairs = tabs.terminal_cwds("session:1")
        self.assertEqual([(w["window_id"], w["session_id"], cwd, block) for w, cwd, block in pairs],
                         [("window:a", "session:1", "/w/a", "block:1"), ("window:a", "session:1", "/w/a/src", "block:2")])
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

    def test_flash_messages_fade_and_wrap(self):
        flash = ui.Flash()
        self.assertEqual((flash.rows(40), flash.wait(-1), flash.wait(150)), ([], -1, 150))
        with patch.object(ui.time, "monotonic", return_value=100.0):
            flash.set("Saved", "green")
        with patch.object(ui.time, "monotonic", return_value=101.0):
            self.assertEqual(flash.wait(-1), 3050)  # Wakes up when the message fades.
            self.assertEqual(flash.wait(150), 150)
            self.assertIn("Saved", flash.rows(40)[0][0][0])
        with patch.object(ui.time, "monotonic", return_value=106.0):
            self.assertEqual(flash.rows(40), [])
        with patch.object(ui.time, "monotonic", return_value=100.0):
            flash.set("Failed: " + "word " * 40)
        with patch.object(ui.time, "monotonic", return_value=1000.0):
            rows = flash.rows(40)  # Errors stay until the next key, wrapped to at most three rows.
            self.assertEqual(len(rows), 3)
            self.assertTrue(rows[-1][0][0].endswith("…") and all(len(r[0][0]) <= 40 for r in rows))
            self.assertEqual(flash.wait(-1), -1)

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
        self.assertEqual((new["branch"], new["action"]), ("test-branch", "create the branch from main"))
        self.assertNotIn("error", new)
        local = branching.plan_branch(cwd, branches, "", self.suggestion(branches, "existing"))
        self.assertEqual(local["action"], "check out the existing branch existing")  # Named in full.
        remote = branching.plan_branch(cwd, branches, "", self.suggestion(branches, "origin/remote-only"))
        self.assertEqual(remote["action"], "create a local branch tracking origin/remote-only")
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
        closing = [{"window": {"window_id": "window:1"}, "panes": ["block:1"], "whole": True, "here": False}]
        with patch.object(tabs, "close_panes") as close:
            actions.remove_worktree(str(self.repo), "session:1", entry, closing)
        self.assertFalse(other.exists())
        close.assert_called_once_with("session:1", closing)
        git(self.repo, "rev-parse", "--verify", "refs/heads/clean")

    def test_failed_git_removal_leaves_tabs_open(self):
        other = self.add("dirty", "dirty")
        (other / "untracked").write_text("changes")
        entry = worktrees.worktrees(str(self.repo))[1]
        with patch.object(tabs, "close_panes") as close, self.assertRaises(RuntimeError):
            actions.remove_worktree(str(self.repo), "session:1", entry, [{"window": {"window_id": "window:1"}}])
        close.assert_not_called()

    def test_force_remove_and_delete_merged_branch(self):
        other = self.add("merged", "merged")
        (other / "untracked").write_text("changes")
        entry = {**worktrees.worktrees(str(self.repo))[1], "name": "merged"}
        self.assertEqual(worktrees.merged_into(str(self.repo), entry), "main")
        with patch.object(tabs, "close_panes"):
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
        self.assertEqual(plan["action"], "create the branch from main")
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

    def test_close_only_panes_inside_and_the_picker_tab_last(self):
        own = {"window_id": "window:own", "label": "Shell", "layers": [
            {"blocks": [{"block_id": "block:origin"}]}, {"blocks": [{"block_id": "block:picker"}]}]}
        split = {"window_id": "window:split", "label": "Work", "layers": [
            {"blocks": [{"block_id": "block:a"}, {"block_id": "block:b"}, {"block_id": "block:c"}]}]}
        terminals = [(own, "/w/tree", "block:origin"), (split, "/w/tree/src", "block:a"),
                     (split, "/w/other", "block:b"), (split, "/w/tree", "block:c")]
        with patch.dict(os.environ, {"REX_BLOCK": "block:picker"}):
            groups = tabs.panes_to_close(terminals, ["/w/tree"])
            # The picker's floating pane doesn't keep its tab open; another pane does.
            self.assertEqual([(g["window"]["window_id"], g["panes"], g["whole"], g["here"]) for g in groups],
                             [("window:own", ["block:origin"], True, True), ("window:split", ["block:a", "block:c"], False, False)])
            self.assertEqual([tabs.describe_closing(g) for g in groups],
                             [("Shell", "the whole tab"), ("Work", "2 of 3 panes; the tab stays")])
            with patch.object(util, "run") as run:
                tabs.close_panes("session:1", groups)
            self.assertEqual(run.call_args_list, [
                call("rex", "block", "close", "-s", "session:1", "block:a", "block:c"),
                call("rex", "window", "close", "-s", "session:1", "window:own"),
            ])

    def test_windows_for_path_matches_nested_paths_once(self):
        window, other = {"window_id": "window:1"}, {"window_id": "window:2"}
        terminals = [(window, "/w/repo/src", "b1"), (window, "/w/repo", "b2"), (other, "/w/repository", "b3")]
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
            tabs.close_panes("session:1", tabs.panes_to_close(tabs.terminal_cwds("session:1"), ["/w/b"]))
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


    def test_fit_parts_keeps_fixed_rows_and_shortens_lists(self):
        text = lambda rows: ["".join(t for t, _ in row) for row in rows]
        files = ui.items([[(f"  file{i}", 0)] for i in range(10)], heading=[("FILES", 0)], summary=[("10 files", 0)])
        panes = ui.items([[("  pane", 0)]] * 3, heading=[("PANES", 0)], summary=[("3 panes", 0)])
        parts = [ui.fixed([("name", 0)]), ui.extra([("path", 0)]), ui.GAP, files, ui.GAP, panes, ui.GAP,
                 ui.fixed([("[ ] option", 0)])]
        self.assertEqual(len(ui.fit_parts(parts, 40)), 21)  # Everything fits.
        self.assertFalse(ui.cut(parts, 21))
        # Lists shorten to a few rows before the gaps go.
        rows = text(ui.fit_parts(parts, 16))
        self.assertEqual(len(rows), 16)
        self.assertTrue(ui.cut(parts, 16))
        self.assertEqual(rows[rows.index("FILES") + 1:rows.index("PANES")],
                         ["  file0", "  file1", "  file2", "  file3", "  …and 6 more", ""])
        # Then the gaps and the extra rows; a list that fits whole gets the rows first.
        rows = text(ui.fit_parts(parts, 9))
        self.assertEqual(rows, ["name", "FILES", "  file0", "  …and 9 more", "PANES", "  pane", "  pane", "  pane",
                                "[ ] option"])
        # Short of rows, each list keeps its summary rather than a heading on its own.
        self.assertEqual(text(ui.fit_parts(parts, 6)), ["name", "FILES", "  file0", "  …and 9 more", "3 panes", "[ ] option"])
        self.assertEqual(text(ui.fit_parts(parts, 4)), ["name", "10 files", "3 panes", "[ ] option"])
        self.assertEqual(len(ui.all_rows(parts)), 21)

    def test_compose_pins_the_bottom_rows(self):
        rows = ui.compose(24, [[("title", 0)]], [[("body", 0)]] * 50, [[("status", 0)], [("keys", 0)]])
        self.assertEqual((len(rows), rows[0], rows[1], rows[-2:]), (24, [], [("title", 0)], [[("status", 0)], [("keys", 0)]]))
        rows = ui.compose(10, [[("title", 0)]], [], [[("keys", 0)]])
        self.assertEqual((len(rows), rows[0], rows[-1]), (10, [("title", 0)], [("keys", 0)]))  # No top margin when short.

    def test_inputs_scroll_to_keep_the_cursor_in_view(self):
        self.assertEqual(ui.input_view("short", 5, 20), ("short", 5))
        text = "abcdefghijklmnopqrstuvwxyz"
        visible, column = ui.input_view(text, len(text), 10)
        self.assertEqual((visible, column), ("…stuvwxyz", 9))
        visible, column = ui.input_view(text, 0, 10)
        self.assertEqual((visible, column), ("abcdefghi…", 0))
        visible, column = ui.input_view(text, 13, 10)
        self.assertTrue(visible.startswith("…") and visible.endswith("…") and visible[column] == text[13])
        row, column = ui.search_row("", 0, False, 40)
        self.assertEqual(([t for t, _ in row], column), (["/ to filter"], None))
        row, column = ui.search_row("", 0, True, 40)
        self.assertEqual(([t for t, _ in row], column), (["/ ", "type to filter"], 2))

    def test_paths_wrap_at_slashes(self):
        self.assertEqual(ui.wrap_path("in ~/.local/share/rex/worktrees/app-1234/feature", 34),
                         ["in ~/.local/share/rex/worktrees/", "app-1234/feature"])
        self.assertTrue(all(len(line) <= 8 for line in ui.wrap_path("/averyveryverylongfolder/x", 8)))

    def test_list_and_preview_share_the_rows(self):
        self.assertEqual(screen_list.list_layout(15, 5), (5, 10))  # A short list leaves the preview every row.
        self.assertEqual(screen_list.list_layout(15, 17), (8, 7))
        self.assertEqual(screen_list.list_layout(33, 5), (5, 28))  # A tall pane: no gap between them.
        self.assertEqual(screen_list.list_layout(10, 17), (5, 5))  # A short pane still shows some commits.
        self.assertEqual(screen_list.list_layout(6, 17), (6, 0))  # Too short for both: just the list.
        top = ui.scroll_top(0, 9, 17, 5)
        self.assertEqual(top, 5)
        self.assertEqual(ui.scroll_top(top, 2, 17, 5), 2)
        row = ui.with_position([("/ to filter", 0)], 7, 17, 5, 30)
        self.assertEqual("".join(t for t, _ in row), "/ to filter" + " " * 15 + "8/17")
        self.assertEqual(ui.with_position([("x", 0)], 0, 3, 5, 30), [("x", 0)])  # Only when the list scrolls.

    def test_folder_column_hides_before_the_branch_shortens(self):
        entries = [{"name": "experiment/a-long-branch-name-that-is-distinctive", "worktree": str(self.repo),
                    "open": False, "dirty": False, "track": []}]
        wide = ui.column_widths(entries, 120)
        self.assertEqual((wide["BRANCH"], wide["FOLDER"]), (len(entries[0]["name"]), len("repo with spaces")))
        squeezed = ui.column_widths(entries, len(entries[0]["name"]) + 11 + 5)
        self.assertEqual((squeezed["BRANCH"], squeezed["FOLDER"]), (len(entries[0]["name"]), 0))
        self.assertNotIn("FOLDER", "".join(t for t, _ in ui.header_row(squeezed)))
        narrow = ui.column_widths(entries, 40)
        self.assertEqual((narrow["BRANCH"], narrow["FOLDER"], narrow["compact"]), (40 - 11, 0, False))

    def test_selection_moves_to_the_next_row_after_removal(self):
        entries = [{"worktree": p} for p in ("/a", "/c", "/d")]
        picker = SimpleNamespace(entries=entries)
        reselect = lambda paths, index: screen_list.ListScreen.reselect(picker, paths, index)
        self.assertIs(reselect(["/a", "/b", "/c", "/d"], 1), entries[1])  # /b is gone: the next one.
        self.assertIs(reselect(["/a", "/c", "/d", "/e"], 3), entries[2])  # The last row is gone: the one before.
        self.assertIs(reselect(["/a", "/c"], 1), entries[1])  # Cancelled: the same worktree.

    def test_long_errors_get_their_own_screen(self):
        screen = SimpleNamespace(getmaxyx=lambda: (16, 50))
        picker = SimpleNamespace(stdscr=screen, flash=ui.Flash())
        with patch.object(ui, "show_error") as show:
            app.Manager.say(picker, "short failure")
            show.assert_not_called()
            self.assertEqual(picker.flash.text, "short failure")
            app.Manager.say(picker, "fatal: " + "a long git error " * 12)
            show.assert_called_once()
            app.Manager.say(picker, "a long note " * 12, "dim")  # Only errors.
            show.assert_called_once()

    def test_cleanup_rows_keep_their_notes_in_a_narrow_pane(self):
        dirty = {"entry": {"name": "feature/finished-work", "worktree": "/w/a", "dirty": True,
                           "track": ["remote deleted"]}, "checked": False}
        open_ = {"entry": {"name": "fix/done", "worktree": "/w/b", "track": ["remote deleted"]}, "checked": True}
        closes = {"/w/b": [{"window": {"window_id": "w", "label": "Shell"}, "panes": ["b1"], "total": 2,
                            "whole": False, "here": True}]}
        items = [dirty, open_]
        with patch.object(worktrees, "missing", return_value=False):
            row = lambda item, width: "".join(t for t, _ in screen_remove.RemovalScreens.cleanup_row(item, False, items, closes, width))
            self.assertTrue(row(dirty, 100).endswith(util.ICONS["dirty"] + " uncommitted, can't be cleaned up"))
            self.assertTrue(row(dirty, 47).rstrip().endswith(util.ICONS["dirty"]))
            self.assertTrue(row(open_, 100).rstrip().endswith(util.ICONS["tab"] + " closes 1 pane"))
            for item in items:
                self.assertLessEqual(len(row(item, 47).rstrip()), 47)
            detail = screen_remove.RemovalScreens.cleanup_detail(items, 1, closes, 100)
            self.assertEqual([r[0][0] for r in detail],
                             ["Its remote branch was deleted. Closes 1 of 2 panes in Shell (this tab).", "fix/done"])

    def picker(self, screen, entries=None):
        """A Manager with just the state the screens use."""
        picker = object.__new__(app.Manager)
        picker.stdscr, picker.label, picker.origin_cwd, picker.repo = screen, "repo", str(self.repo), str(self.repo)
        picker.entries = entries or []
        picker.flash, picker.sort, picker.prs, picker.show_prs = ui.Flash(), "git", {}, False
        picker.prs_loading = picker.details_loading = False
        picker.commits, picker.default, picker.terminals = {}, None, None
        return picker

    def test_list_filters_only_after_a_slash(self):
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False, "track": []}]
        screen = FakeScreen(["f", "x", "/", "q", "#", "\x1b", "q"], (24, 80))
        picker = self.picker(screen, entries)
        drive(screen, picker.list_screen)  # The last q quits.
        frames = [screen.text(i) for i in range(len(screen.frames))]
        self.assertTrue(any("Press / to filter" in "".join(f) for f in frames))
        self.assertFalse(any(line.startswith("/ f") for f in frames for line in f))  # Letters didn't start a filter.
        self.assertTrue(any(line.startswith("/ q#") for f in frames for line in f))  # After /, q types.
        self.assertIn("    No matching worktrees", [line for f in frames for line in f])

    def test_new_worktree_types_every_letter(self):
        cwd, branches = self.setup_branches()
        screen = FakeScreen(list("quick") + [curses.KEY_END, "?", "q", "\x1b", "\x1b"], (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        self.assertFalse(drive(screen, lambda: picker.new_screen(root=True)))  # esc clears, then quits.
        texts = [screen.text(i) for i in range(len(screen.frames))]
        typed = next(t for t in texts if any(line.startswith("/ quick") for line in t))
        self.assertTrue(any("Create branch “quick”" in line for line in typed))
        self.assertTrue(any(line.startswith("New worktree — keys") for t in texts for line in t))
        self.assertTrue(any("esc quit" in line for line in texts[-1]))

    def test_new_worktree_opens_a_branch_thats_checked_out_already(self):
        cwd, branches = self.setup_branches()
        screen = FakeScreen(list("main") + [curses.KEY_HOME, "\n"], (16, 60))
        path = branches["checked_out"]["main"]
        picker = self.picker(screen, [{"name": "main", "worktree": path, "branch": "refs/heads/main", "main": True}])
        picker.session, picker.client = "s", None
        picker.branches = lambda: branches
        picker.new_branch_screen = lambda *args: self.fail("enter went on to name the branch")
        with patch.object(tabs, "open_tab") as open_tab:
            self.assertTrue(drive(screen, lambda: picker.new_screen()))
        self.assertIs(open_tab.call_args[0][1], picker.entries[0])
        rows = next(screen.text(i) for i in range(len(screen.frames)) if "› + Create branch “main”" in screen.text(i))
        self.assertIn("  " + util.ICONS["info"] + " This branch is checked out already.", rows)
        self.assertIn("enter: open the worktree main is checked out in", rows)
        self.assertIn(("at " + util.tilde(path)).replace(" ", ""), "".join(rows).replace(" ", ""))  # In the plan.
        self.assertTrue(rows[-1].startswith("enter open"))

    def test_new_branch_plan_follows_the_bases_and_the_hint_fits(self):
        cwd, branches = self.setup_branches()
        screen = FakeScreen(["\n", "\x1b"], (40, 50))
        picker = self.picker(screen)
        drive(screen, lambda: picker.new_branch_screen(cwd, branches))
        rows = screen.text(0)
        hint = rows.index("Type a name; spaces become dashes.")
        self.assertLess(hint, 12)  # Near the name, not at the bottom of a tall pane.
        self.assertEqual(screen.frames[1][hint][0][1], ui.style("yellow"))  # Enter with no name: the hint again.
        self.assertEqual(sum("Type a name" in line for line in screen.text(1)), 1)
        long_name = "feature improve worktree picker handling for empty histories and stale tabs in small panes"
        with patch.object(worktrees, "setup_command", return_value="make setup"):
            screen = FakeScreen(["\x1b"], (16, 50))
            picker = self.picker(screen)
            screen.keys.append("\x1b")  # The first esc clears the name; the second goes back.
            drive(screen, lambda: picker.new_branch_screen(cwd, branches, long_name, "make setup"))
        rows = screen.text(0)
        self.assertTrue(any(line.startswith("enter: create the branch from") for line in rows))
        self.assertTrue(any("then runs make setup" in line for line in rows))
        self.assertTrue(any(line.strip().endswith("small-panes") for line in rows))  # The whole destination.

    def test_long_errors_scroll(self):
        message = "Preparing worktree\nfatal: " + "cannot lock ref because the path is long " * 30 + "the end"
        screen = FakeScreen(["j"] * 40 + ["q"], (16, 50))
        drive(screen, lambda: ui.show_error(screen, message))
        first, last = screen.text(0), screen.text()
        self.assertRegex(first[0].rstrip(), r"  \d+/\d+$")  # How far down the last visible row is.
        self.assertIn("j/k scroll", first[-1])
        self.assertTrue(any("Preparing worktree" in line for line in first))
        self.assertIn("the end", " ".join(line.strip() for line in last))
        position, total = last[0].split()[-1].split("/")
        self.assertEqual(position, total)

    def test_typing_motions_leave_letters_to_the_text(self):
        self.assertEqual(keyboard.typing_motion(curses.KEY_END, 0, 10, 4), 9)
        self.assertEqual(keyboard.typing_motion(curses.KEY_HOME, 7, 10, 4), 0)
        self.assertEqual(keyboard.typing_motion(curses.KEY_NPAGE, 0, 10, 4), 4)
        self.assertEqual(keyboard.typing_motion("\x0e", 0, 10, 4), 1)
        for key in ("j", "k", "g", "G", "q", "\x04"):
            self.assertIsNone(keyboard.typing_motion(key, 3, 10, 4))

    def test_exit_drops_keys_typed_after_quitting(self):
        screen = FakeScreen(list("uick"))
        with patch.object(curses, "flushinp"):
            keyboard.settle(screen, quiet=0.01)
        self.assertEqual(screen.keys, [])

    def test_compact_preview_shows_the_whole_branch_first(self):
        name = "experiment/very-long-branch-name-to-test-truncation-behaviour-with-a-distinctive-suffix"
        entry = {"name": name, "worktree": str(self.repo), "track": [], "dirty": False, "open": False}
        picker = self.picker(None)
        picker.commits[(str(self.repo), None)] = ("Recent commits", [("abc1234", "subject", "me", 0)])
        rows = ["".join(t for t, _ in row) for row in picker.preview(entry, 47, True, 5)]
        self.assertEqual("".join(rows[1:3]), name)  # Wrapped in full, before anything else.
        self.assertNotIn("RECENT COMMITS", rows)  # No room for a commit, so no heading either.
        rows = ["".join(t for t, _ in row) for row in picker.preview(entry, 47, True, 8)]
        self.assertIn("RECENT COMMITS", rows)
        self.assertTrue(rows[-1].startswith("abc1234"))

    def test_cleanup_detail_explains_before_naming(self):
        name = "experiment/" + "very-long-branch-name-" * 5 + "distinctive-final-suffix"
        dirty = {"entry": {"name": name, "worktree": "/w/a", "dirty": True, "track": ["remote deleted"]}, "checked": False}
        with patch.object(worktrees, "missing", return_value=False):
            detail = screen_remove.RemovalScreens.cleanup_detail([dirty], 0, {}, 47, 3)
        text = ["".join(t for t, _ in row) for row in detail]
        self.assertEqual(len(text), 3)
        self.assertTrue(text[0].startswith("Has uncommitted changes"))
        self.assertTrue(text[-1].endswith("distinctive-final-suffix") and "…" in text[-1])

    def test_closing_rows_keep_their_detail(self):
        group = {"window": {"window_id": "w", "label": "experiment/very-long-branch-name-to-test-truncation",
                            "session_id": "session:repo"}, "panes": ["b"], "total": 1, "whole": True, "here": False}
        row = "".join(t for t, _ in screen_remove.closing_rows([group], 47, "session:picker", "repo")[0])
        self.assertLessEqual(len(row), 47)
        self.assertTrue(row.endswith("(repo session)  the whole tab"))
        # A longer session name: what closes goes on its own row, and nothing overflows.
        rows = ["".join(t for t, _ in r) for r in screen_remove.closing_rows([group], 47, "session:picker", "reviewopusr3")]
        self.assertEqual(rows[1], "    the whole tab")
        self.assertTrue(rows[0].endswith("(reviewopusr3 session)") and len(rows[0]) <= 47)
        self.assertEqual(screen_remove.closing_summary([dict(group, here=True)], "session:picker", "repo"),
                         "Closes the tab experiment/very-long-branch-name-to-test-truncation (this tab).")

    def test_cleanup_counts_worktrees_git_dropped_but_could_not_delete(self):
        one = self.add("one", "one")
        entry = {**worktrees.worktrees(str(self.repo))[1], "name": "one"}

        def remove_but_keep_folder(repo, session, entry, closing, **options):
            git(self.repo, "worktree", "remove", "--force", entry["worktree"])
            Path(entry["worktree"]).mkdir()
            raise RuntimeError("Permission denied")

        with patch.object(actions, "remove_worktree", remove_but_keep_folder), patch.object(tabs, "close_panes"):
            removed, failures = actions.remove_many(str(self.repo), "s", [(entry, False)], [])
        self.assertEqual((removed, failures), (["one"], ["couldn't delete the folder of one: Permission denied"]))
        self.assertTrue(one.exists())

    def test_help_puts_wide_keys_above_their_descriptions(self):
        body = ui.help_body([("Labels", [("herdr, t3, claude, rex", "the tool"), ("3d", "age")])], 47)
        self.assertEqual(["".join(t for t, _ in row) for row in body[2:]], ["herdr, t3, claude, rex", "    the tool", "3d", "    age"])

    def test_fuzzy_prefers_whole_words_to_scattered_letters(self):
        names = ["feature/payments-refactor-with-a-long-descriptive-name", "chore/old-thing", "fix/odd-log"]
        entries = [{"name": n, "worktree": "/w/" + str(i)} for i, n in enumerate(names)]
        self.assertEqual(matching.filter_entries(entries, "old")[0]["name"], "chore/old-thing")
        self.assertEqual(matching.fuzzy("old", "chore/old-thing")[1], [6, 7, 8])  # Not the o in chore.
        self.assertGreater(matching.fuzzy_all("old", names[1]), matching.fuzzy_all("old", names[0]))
        self.assertEqual(matching.fuzzy("flog", "fix/login-bug")[1], [0, 4, 5, 6])
        self.assertIsNone(matching.fuzzy("zz", "fix/login"))
        self.assertTrue(matching.strong("login", "feature/login-redirect"))
        self.assertTrue(matching.strong("fix login", "fix/login-redirect"))
        self.assertFalse(matching.strong("main", "experiment/domain-names"))  # Not from the start of a word.
        self.assertFalse(matching.strong("remote-pr", "feature/remote-only-pr"))
        self.assertFalse(matching.strong("l", "login"))

    def new_worktree_frames(self, keys, branches, size=(16, 50), setup=""):
        screen = FakeScreen(keys, size)
        picker = self.picker(screen)
        picker.branches = lambda: branches
        picker.new_branch_screen = lambda *args: self.fail("enter went on to name a branch")
        with patch.object(worktrees, "setup_command", return_value=setup):
            drive(screen, lambda: picker.new_screen(root=True))
        return [screen.text(i) for i in range(len(screen.frames))]

    def test_new_worktree_keeps_the_create_row_for_weak_matches(self):
        long_name = "experiment/very-long-branch-name-to-test-truncation-behaviour"
        for name in (long_name, "feature/remote-only-pr", "release/login-redirect"):
            git(self.repo, "branch", name)
        cwd, branches = self.setup_branches()
        for typed, label in (("main", "“main”"), ("remote-pr", "“remote-pr”…")):  # main can't be created.
            frames = self.new_worktree_frames(list(typed) + ["\x1b", "\x1b"], branches)
            rows = next(f for f in frames if any(line.startswith("/ " + typed) for line in f))
            self.assertIn(f"› + Create branch {label}", rows, typed)
        # A word the branch starts with, or its whole name, selects it, and the plan names it.
        frames = self.new_worktree_frames(list("login") + ["\x1b", "\x1b"], branches, (24, 60))
        rows = next(f for f in frames if any(line.startswith("/ login") for line in f))
        self.assertTrue(any(line.startswith("› release/login-redirect") for line in rows))
        plan = rows[next(i for i, line in enumerate(rows) if line.startswith("enter:")):]
        self.assertIn("check out the existing branch release/login-redirect", " ".join(line[7:].strip() for line in plan))
        self.assertIn("       release/login-redirect", plan)  # Whole, not broken at its dash.

    def test_already_checked_out_shows_whichever_row_is_selected(self):
        git(self.repo, "branch", "main-old")
        cwd, branches = self.setup_branches()
        frames = self.new_worktree_frames(list("main") + ["\x1b", "\x1b"], branches, (24, 60))
        rows = next(f for f in frames if any(line.startswith("/ main") for line in f))
        self.assertTrue(any(line.startswith("› main-old") for line in rows))
        create = next(i for i, line in enumerate(rows) if "Create branch “main”" in line)
        self.assertIn("Already checked out", rows[create + 1])  # Right below the create row.

    def test_new_worktree_plan_keeps_the_branch_tail_and_setup(self):
        names = ["experiment/very-long-branch-name-to-test-truncation-behaviour-" + "x" * 40 + "-available-" + tail
                 for tail in ("alpha", "beta")]
        for name in names:
            git(self.repo, "branch", name)
        cwd, branches = self.setup_branches()
        setup = 'printf "sandbox setup complete\\n"'
        frames = self.new_worktree_frames(list("available") + [curses.KEY_DOWN, "\t", "q", "\x1b", "\x1b"],
                                          branches, (16, 50), setup)
        rows = next(f for f in frames if any(line.startswith("/ available") for line in f))
        plan = rows[next(i for i, line in enumerate(rows) if line.startswith("enter:")):]
        self.assertTrue(any(line.rstrip().endswith("available-alpha") for line in plan), plan)  # The selected branch.
        self.assertTrue(any("then runs printf" in line for line in plan))
        self.assertIn("tab details", rows[-1])
        details = next(f for f in frames if f[0].startswith("New worktree — details"))
        self.assertIn(names[1], "".join(line[7:].strip() for line in details))  # ↓ chose beta; in full.
        # Long destinations keep their distinguishing ends.
        paths = [branching.new_path(cwd, name).name for name in names]
        self.assertEqual([len(p) for p in paths], [100, 100])
        self.assertTrue(paths[0].endswith("available-alpha") and paths[1].endswith("available-beta"))

    def test_new_branch_name_cant_clash_with_a_branch_folder(self):
        git(self.repo, "branch", "docs/readme")
        git(self.repo, "branch", "spike")
        cwd, branches = self.setup_branches()
        self.assertIn("docs/readme", branching.plan_branch(cwd, branches, "docs")["error"])
        self.assertEqual(branching.plan_branch(cwd, branches, "spike/two")["error"], "spike/two can't be a branch while spike exists.")
        self.assertNotIn("error", branching.plan_branch(cwd, branches, "docs-two"))

    def test_remove_dialog_keeps_a_line_for_every_list_in_a_small_pane(self):
        name = "experiment/very-long-branch-name-to-test-truncation-behaviour-with-another-distinctive-final-suffix"
        entry = {"name": name, "worktree": str(self.repo), "branch": "refs/heads/" + name}
        files = [f"?? file{i}.txt" for i in range(16)]
        closing = [{"window": {"window_id": "w1", "label": "Shell", "session_id": "s"}, "panes": ["a", "b"],
                    "total": 4, "whole": False, "here": True},
                   {"window": {"window_id": "w2", "label": name, "session_id": "other"}, "panes": ["c"],
                    "total": 1, "whole": True, "here": False}]
        options = [{"id": "force", "checked": False, "label": [("Discard uncommitted changes", 0)]}]

        def show(size, keys):
            screen = FakeScreen(keys, size)
            picker = self.picker(screen)
            picker.session = "s"
            self.assertIsNone(drive(screen, lambda: picker.removal_options(entry, files, closing, None, options)))
            return screen

        screen = show((16, 50), ["\t", "G", "q", "q"])
        rows = screen.text(0)
        self.assertIn("  …and 14 more (tab lists all)", rows)
        self.assertIn("  Shell (this tab)  2 of 4 panes; the tab stays", rows)  # The short list whole.
        self.assertIn("The branch will be kept.", rows)
        self.assertEqual(sum("final-suffix" in line for line in rows), 1)  # The name, once.
        footer = "".join(t for t, _ in ui.fit(screen.frames[0][-1], 47))
        self.assertTrue(footer.rstrip().endswith("q cancel"))
        details = [screen.text(i) for i in range(len(screen.frames)) if screen.text(i)[0].startswith("Remove worktree — details")]
        self.assertTrue(details and any("file15.txt" in line for line in details[-1]))  # Scrolled to the end.
        rows = show((12, 50), ["q"]).text(0)
        self.assertIn("  16 uncommitted files  (tab lists them)", rows)  # A line for each list, at least.
        self.assertTrue(any("PANES THAT WILL CLOSE" in line or "3 panes will close" in line for line in rows), rows)

    def test_destructive_footers_keep_cancel_over_help(self):
        row = "".join(t for t, _ in ui.fit(ui.footer(enter="remove 2", move=True, toggle=True, back="cancel"), 47))
        self.assertEqual(row.split(), ["space", "toggle", "enter", "remove", "2", "q", "cancel"])
        row = "".join(t for t, _ in ui.fit(ui.footer(enter="open", move=True, back="quit"), 30))
        self.assertIn("? help", row)

    def test_status_messages_keep_their_line_breaks(self):
        flash = ui.Flash()
        flash.set("please run:\n  gh auth login\nAlternatively, set GH_TOKEN.")
        rows = ["".join(t for t, _ in row) for row in flash.rows(60)]
        # Under the text, not the icon.
        self.assertEqual(rows[1:], ["    gh auth login", "  Alternatively, set GH_TOKEN."])

    def test_folder_column_leaves_out_the_shared_prefix(self):
        entries = [{"name": "main", "worktree": "/w/myrepo", "main": True, "track": []},
                   {"name": "b", "worktree": "/w/myrepo-login", "track": []}]
        widths = ui.column_widths(entries, 120)
        rows = ["".join(t for t, _ in ui.entry_row(e, False, widths)).rstrip() for e in entries]
        self.assertTrue(rows[0].endswith(" myrepo") and rows[1].endswith(" login"))
        hits = matching.match_entry(entries[1], "logi")[2]
        row = ui.entry_row(entries[1], False, widths, (0, set(), hits))
        self.assertEqual(hits, {7, 8, 9, 10})
        self.assertEqual([t for t, _ in row[-2:]], ["logi", "n"])  # The hits follow the shortened name.

    def test_list_with_no_matches_starts_a_new_worktree_for_the_filter(self):
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False, "track": []}]
        screen = FakeScreen(["/"] + list("zzz") + ["\n", "q"], (24, 80))
        picker = self.picker(screen, entries)
        started = []
        picker.new_screen = lambda root=False, text="": started.append(text)
        drive(screen, picker.list_screen)
        frame = next(screen.text(i) for i in range(len(screen.frames)) if "/ zzz" in screen.text(i)[2])
        self.assertIn("    enter new worktree for “zzz”", frame)
        self.assertEqual(started, ["zzz"])

    def test_cleanup_detail_sits_under_the_options(self):
        entries = [{"name": "fix/done", "worktree": "/w/done", "track": ["remote deleted"], "dirty": False}]
        screen = FakeScreen(["q"], (40, 140))
        picker = self.picker(screen, entries)
        picker.session, picker.terminals = "s", []
        with patch.object(worktrees, "missing", return_value=False):
            drive(screen, picker.cleanup)
        rows = screen.text(0)
        option = next(i for i, line in enumerate(rows) if "Also delete its branch" in line)  # Just one.
        self.assertTrue(rows[option + 2].startswith("Its remote branch was deleted."))

    def test_prune_rows_keep_the_folder_name(self):
        stale = [{"name": "chore/bump-deps", "worktree": "/private/tmp/rexwt-review-opus-r3/reviewopusr3-gone",
                  "prunable": "gitdir file points to non-existent location"}]
        screen = FakeScreen(["q"], (16, 50))
        picker = self.picker(screen, stale)
        drive(screen, picker.prune)
        row = next(line for line in screen.text(0) if "chore/bump-deps" in line)
        self.assertLessEqual(len(row.rstrip()), 47)
        self.assertTrue(row.rstrip().endswith("reviewopusr3-gone"))

    def test_an_existing_branchs_name_selects_it_without_an_error(self):
        cwd, branches = self.setup_branches()
        for typed, plan in (("existing", "check out the existing branch existing"),
                            ("remote-only", "create a local branch tracking origin/remote-only")):
            rows = next(f for f in self.new_worktree_frames(list(typed) + ["\x1b", "\x1b"], branches, (24, 60))
                        if any(line.startswith("/ " + typed) for line in f))
            self.assertIn(f"  + Create branch “{typed}”", rows)  # No “…”: it can't be created.
            self.assertIn("  " + util.ICONS["info"] + " This branch exists; enter checks it out.", rows)
            self.assertFalse(any(util.ICONS["error"] in line for line in rows), rows)
            self.assertTrue(any(line.startswith("› " + typed) for line in rows))
            self.assertIn(plan, " ".join(rows))
        # Moved back to the create row, the error applies again.
        frames = self.new_worktree_frames(list("existing") + [curses.KEY_UP, "\x1b", "\x1b"], branches, (24, 60))
        rows = next(f for f in frames if "› + Create branch “existing”" in f)
        self.assertTrue(any(util.ICONS["error"] + " A branch with this name exists" in line for line in rows))

    def test_a_remote_qualified_name_means_the_remote_branch(self):
        cwd, branches = self.setup_branches()
        self.assertEqual(branching.split_remote("origin/fix/x", branches), ("origin", "fix/x"))
        self.assertIsNone(branching.split_remote("originals/x", branches))
        plan = branching.plan_branch(cwd, branches, "origin/remote-only")
        self.assertEqual(plan["exists"], ["origin/remote-only"])
        self.assertEqual(branching.plan_branch(cwd, branches, "origin/new-idea")["error"],
                         "A branch can't start with origin/, a remote's name; name it new-idea.")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/existing", "HEAD")
        cwd, branches = str(self.repo), branching.load_branches(str(self.repo), worktrees.worktrees(str(self.repo)))
        self.assertIn("open", branching.plan_branch(cwd, branches, "origin/main"))  # main is checked out.
        self.assertEqual(branching.plan_branch(cwd, branches, "origin/existing")["exists"], ["existing"])
        for typed, selected in (("origin/remote-only", "› remote-only"), ("origin/exist", "› existing"),
                                ("origin/remote-o", "› remote-only")):
            rows = next(f for f in self.new_worktree_frames(list(typed) + ["\x1b", "\x1b"], branches, (24, 60))
                        if any(line.startswith("/ " + typed) for line in f))
            self.assertTrue(any(line.startswith(selected) for line in rows), (typed, rows))
            self.assertFalse(any(util.ICONS["error"] in line for line in rows), rows)

    def test_wrapping_keeps_hyphenated_names_whole(self):
        self.assertEqual(ui.wrap("check out the existing branch fix/login-flow-timeout", 43),
                         ["check out the existing branch", "fix/login-flow-timeout"])
        # Paths break after a slash, and inside a folder's name only when it's too long for a line.
        self.assertEqual(ui.wrap_path("/private/tmp/a-long-parent/unique-leaf-folder", 30),
                         ["/private/tmp/a-long-parent/", "unique-leaf-folder"])
        self.assertEqual(ui.wrap_path("experiment/very-long-branch-name", 16),
                         ["experiment/very-", "long-branch-name"])

    def test_footer_keeps_the_way_back_to_create_once_typing_selects_a_branch(self):
        name = "experiment/very-long-branch-name-to-test-truncation-behaviour-" + "x" * 40 + "-available-alpha"
        git(self.repo, "branch", name)
        cwd, branches = self.setup_branches()
        screen = FakeScreen(list("available") + ["\x1b", "\x1b"], (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        with patch.object(worktrees, "setup_command", return_value='printf "setup complete\\n"'):
            drive(screen, lambda: picker.new_screen(root=True))
        frame = next(f for f in screen.frames if any("available" in t for t, *_ in f[1]))
        footer = "".join(t for t, _ in ui.fit(frame[-1], 47))
        self.assertIn("↑/↓ move", footer)

    def test_list_without_a_selection_hides_and_explains_row_actions(self):
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False, "track": []}]
        screen = FakeScreen(["/"] + list("zzz") + ["\t", "s", "q"], (24, 100))
        picker = self.picker(screen, entries)
        drive(screen, picker.list_screen)
        frames = [screen.text(i) for i in range(len(screen.frames))]
        footer = frames[-1][-1]
        self.assertNotIn("split", footer)
        self.assertNotIn("remove", footer)
        self.assertIn("n new", footer)
        self.assertTrue(any(screen_list.NONE_SELECTED in line for line in frames[-1]))

    def test_missing_folder_is_said_once(self):
        entry = {"name": "gone", "worktree": "/nonexistent/gone", "prunable": "gone", "track": []}
        picker = self.picker(None)
        rows = lambda said: ["".join(t for t, _ in row) for row in picker.preview(entry, 47, False, 8, said)]
        self.assertIn("press p to prune it.", " ".join(" ".join(rows(False)).split()))
        self.assertTrue(rows(False)[-1].endswith("prune it."))  # Wrapped, not cut.
        self.assertFalse(any("missing" in line for line in rows(True)))

    def test_folder_column_hides_folders_that_repeat_the_branch(self):
        made = str(Path.home()) + "/.local/share/rex/worktrees/repo-0123456789/"
        entries = [{"name": "main", "worktree": "/w/repo", "branch": "refs/heads/main", "main": True, "track": []},
                   {"name": "chore/old-thing", "worktree": made + "chore-old-thing", "branch": "x", "track": []},
                   {"name": "fix/b", "worktree": "/w/repo-fix-b", "branch": "x", "track": []}]
        self.assertEqual(ui.folder_name(entries[1]), ("", 0))
        self.assertEqual(ui.folder_name(entries[2], "repo-"), ("", 0))
        widths = ui.column_widths(entries, 120)
        rows = ["".join(t for t, _ in ui.entry_row(e, False, widths)).rstrip() for e in entries]
        self.assertTrue(rows[0].endswith(" repo"))
        self.assertTrue(rows[1].endswith(" rex"), rows[1])  # Only the tool that made it.
        self.assertEqual(ui.column_widths(entries[1:2], 120)["FOLDER"], len("FOLDER"))  # For "rex".
        alone = [{"name": "fix/b", "worktree": "/w/fix-b", "branch": "x", "track": []}]
        self.assertEqual(ui.column_widths(alone, 120)["FOLDER"], 0)  # Nothing to show.

    def test_cleanup_says_up_front_when_every_candidate_is_dirty(self):
        entries = [{"name": "fix/done", "worktree": "/w/done", "track": ["remote deleted"], "dirty": True}]
        picker = self.picker(FakeScreen([], (24, 80)), entries)
        with patch.object(worktrees, "missing", return_value=False):
            self.assertFalse(picker.cleanup())
        self.assertTrue(picker.flash.text.startswith("Can't clean up: the only finished worktree has uncommitted "
                                                     "changes; remove it on its own with dd."), picker.flash.text)
        self.assertTrue(picker.flash.text.endswith("It's fix/done."))
        self.assertEqual(picker.flash.tone, "yellow")

    def test_cleanup_message_names_the_deleted_branches(self):
        done = screen_remove.cleanup_done
        self.assertEqual(done(["a"], 0, False), "Removed 1 worktree.")
        self.assertEqual(done(["a"], 1, True), "Removed 1 worktree and its branch.")
        self.assertEqual(done(["a", "b"], 2, True), "Removed 2 worktrees and their branches.")
        self.assertEqual(done(["a", "b", "c"], 2, True),
                         "Removed 3 worktrees and 2 of their branches; 1 with unmerged commits was kept.")
        self.assertEqual(done(["a"], 0, True),
                         "Removed 1 worktree; its branch had unmerged commits, so it was kept.")

    def test_destructive_details_show_everything_in_full(self):
        stem = "feature-config-with-a-very-long-name-to-check-identification-"
        files = ["?? " + stem + tail + ".json" for tail in ("alpha", "beta")]
        label = "long-name-same-prefix-for-every-tab-label-with-distinguishing-tail-end"
        entry = {"name": "fix/x", "worktree": str(self.repo), "branch": "refs/heads/fix/x"}
        closing = [{"window": {"window_id": "w2", "label": label, "session_id": "other"}, "panes": ["c"],
                    "total": 1, "whole": True, "here": False}]
        screen = FakeScreen(["\t", "q", "q"], (16, 60))
        picker = self.picker(screen)
        picker.session = "s"
        options = [{"id": "force", "checked": False, "label": [("Discard uncommitted changes", 0)]}]
        drive(screen, lambda: picker.removal_options(entry, files, closing, None, options))
        footer = "".join(t for t, _ in ui.fit(screen.frames[0][-1], 57))
        details = next(screen.frames[i] for i in range(len(screen.frames))
                       if screen.text(i)[0].startswith("Remove worktree — details"))
        rows = ["".join(t for t, *_ in row).rstrip() for row in details]
        self.assertTrue(all(len(row) <= 57 for row in rows), rows)
        text = "".join(row.strip() for row in rows)
        for name in files[0][3:], files[1][3:], label:
            self.assertIn(name, text)
        self.assertIn("(repo session)", text)
        self.assertIn("tab details", footer)
        # Prune: every name and path, wrapped.
        path = "/private/tmp/rexwt-review-sol-r4/a-long-parent-name-with-extra-context-/unique-leaf-folder"
        stale = [{"name": "r4/prune-full-details", "worktree": path, "prunable": "gone"}]
        screen = FakeScreen(["\t", "q", "q"], (22, 100))
        drive(screen, self.picker(screen, stale).prune)
        details = next(screen.text(i) for i in range(len(screen.frames)) if "— details" in "".join(screen.text(i)[:2]))
        self.assertIn(path, "".join(line.strip() for line in details))

    def test_cleanup_details_name_every_candidate_in_full(self):
        name = "experiment/" + "very-long-branch-name-" * 4 + "distinctive-final-suffix"
        entries = [{"name": name, "worktree": "/w/a", "track": ["remote deleted"], "dirty": False},
                   {"name": "fix/done", "worktree": "/w/b", "track": ["remote deleted"], "dirty": True}]
        screen = FakeScreen(["\t", "q", "q"], (16, 50))
        picker = self.picker(screen, entries)
        picker.session, picker.terminals = "s", []
        with patch.object(worktrees, "missing", return_value=False):
            drive(screen, picker.cleanup)
        self.assertIn("Also delete their branches", "".join(screen.text(0)))
        details = next(screen.text(i) for i in range(len(screen.frames)) if "Clean up — details" in screen.text(i)[0])
        self.assertIn(name, "".join(line.strip() for line in details))
        self.assertTrue(all(len(line.rstrip()) <= 47 for line in details))
        self.assertTrue(any("Has uncommitted changes" in line for line in details))


    def two_remotes(self):
        """origin and fork, each with feature/remote-two at a different commit; fork's is newer, so
        it's listed first."""
        cwd, branches = self.setup_branches()
        git(self.repo, "remote", "add", "fork", "https://example.invalid/fork.git")
        shas = {}
        for remote, date in (("origin", "2026-01-01T00:00:00"), ("fork", "2026-02-01T00:00:00")):
            env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_NAME": "T",
                   "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}
            tree = git(self.repo, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
            sha = subprocess.run(["git", "-C", str(self.repo), "commit-tree", tree, "-p", "HEAD", "-m", remote],
                                 check=True, stdout=subprocess.PIPE, env=env).stdout.decode().strip()
            git(self.repo, "update-ref", f"refs/remotes/{remote}/feature/remote-two", sha)
            shas[remote] = sha
        return cwd, branching.load_branches(cwd, worktrees.worktrees(cwd)), shas

    def test_a_remote_qualified_name_never_means_another_remotes_branch(self):
        cwd, branches, shas = self.two_remotes()
        refs = [s["ref"] for s in branches["suggestions"] if s["name"] == "feature/remote-two"]
        self.assertEqual(refs, ["fork/feature/remote-two", "origin/feature/remote-two"])  # fork's first.
        origin, fork = (self.suggestion(branches, f"{r}/feature/remote-two") for r in ("origin", "fork"))
        for typed in ("origin/feature/remote-two", "origin/fe", "origin/remote-two"):
            self.assertIsNone(branching.query_for(typed, fork, branches), typed)
            self.assertEqual(branching.query_for(typed, origin, branches), typed[len("origin/"):])
        self.assertEqual(branching.query_for("fork/feature/remote-two", fork, branches), "feature/remote-two")
        self.assertIsNone(branching.query_for("fork/feature/remote-two", origin, branches))
        for remote in ("origin", "fork"):
            typed = f"{remote}/feature/remote-two"
            for size in ((16, 50), (22, 100), (40, 140)):
                rows = next(f for f in self.new_worktree_frames(list(typed) + ["\x1b", "\x1b"], branches, size)
                            if any(line.startswith("/ " + typed) for line in f))
                with self.subTest(typed=typed, size=size):
                    self.assertEqual(sum(line.startswith("  feature/remote-two") or line.startswith("› feature/remote-two")
                                         for line in rows), 1, rows)  # Only that remote's.
                    selected = next(line for line in rows if line.startswith("› feature/remote-two"))
                    self.assertTrue(selected.rstrip().endswith("remote" if remote == "origin" else "fork"), selected)
                    self.assertIn(f"tracking {remote}/feature/remote-two", " ".join(" ".join(rows).split()))
                    self.assertFalse(any(util.ICONS["error"] in line for line in rows), rows)
        # Enter checks out origin's commit, tracking origin, though fork's is listed first.
        screen = FakeScreen(list("origin/feature/remote-two") + ["\n"], (16, 50))
        picker = self.picker(screen)
        picker.session, picker.client = "s", None
        picker.branches = lambda: branches
        with patch.object(tabs, "open_tab"), patch.object(worktrees, "setup_command", return_value=""):
            self.assertTrue(drive(screen, lambda: picker.new_screen()))
        self.assertEqual(git(self.repo, "rev-parse", "feature/remote-two").stdout.decode().strip(), shas["origin"])
        upstream = git(self.repo, "rev-parse", "--abbrev-ref", "feature/remote-two@{upstream}").stdout.decode().strip()
        self.assertEqual(upstream, "origin/feature/remote-two")

    def test_wrapping_breaks_long_words_at_slashes_then_dashes(self):
        long_name = "experiment/very-long-branch-name-to-test-truncation-behaviour"
        self.assertEqual(ui.wrap(f"Closes the tab {long_name} (repo session).", 47),
                         ["Closes the tab experiment/very-long-branch-",
                          "name-to-test-truncation-behaviour (repo", "session)."])
        self.assertEqual(ui.wrap("Already checked out at /private/tmp/rexwt-review-opus-r5/reviewopusr5-gone.", 45),
                         ["Already checked out at /private/tmp/", "rexwt-review-opus-r5/reviewopusr5-gone."])
        # Short hyphenated names stay whole, even beside a long word.
        self.assertEqual(ui.wrap("fix/login-flow-timeout and " + "a/" * 30, 24),
                         ["fix/login-flow-timeout", "and a/a/a/a/a/a/a/a/a/a/", "a/a/a/a/a/a/a/a/a/a/a/a/",
                          "a/a/a/a/a/a/a/a/"])
        for width in (20, 33, 47):
            for line in ui.wrap(f"Closes the tab {long_name} (repo session).", width):
                self.assertLessEqual(len(line), width)
        self.assertEqual(ui.wrap("x" * 25, 10), ["x" * 10, "x" * 10, "x" * 5])  # Only when nothing else will do.

    def test_notes_and_messages_hang_under_their_text(self):
        flash = ui.Flash()
        flash.set("Opened experiment/very-long-branch-name in a background tab in the repo session.", "green")
        rows = ["".join(t for t, _ in row) for row in flash.rows(40)]
        self.assertTrue(rows[0].startswith(util.ICONS["ok"] + " Opened"))
        self.assertTrue(all(row.startswith("  ") and not row.startswith("   ") for row in rows[1:]), rows)
        note = ["".join(t for t, _ in row) for row in screen_new.note_rows(
            "origin/feature/remote-two exists; select it under Existing branches.", "error", "red", 47)]
        self.assertEqual(note[1][:4], "    ")  # The note's indent, then under the text.
        self.assertNotEqual(note[1][4], " ")

    def test_new_worktree_plan_cut_at_50_columns_says_tab_shows_it(self):
        git(self.repo, "branch", "fix/login")
        cwd, branches = self.setup_branches()

        def frame(size, setup):
            screen = FakeScreen(list("login") + ["\x1b", "\x1b"], size)
            picker = self.picker(screen)
            picker.branches = lambda: branches
            with patch.object(worktrees, "setup_command", return_value=setup):
                drive(screen, lambda: picker.new_screen(root=True))
            rows = next(f for f in screen.frames if any(t.startswith("login") for t, *_ in f[1]))
            return ["".join(t for t, _ in ui.fit(row, size[1] - 3)) for row in rows]

        rows = frame((16, 50), 'printf "sandbox setup complete\\n" && make setup-everything ' * 3)
        self.assertTrue(any(line.startswith("› fix/login") for line in rows))
        self.assertNotIn("tab details", rows[-1])  # No room beside ↑/↓, the way back to create.
        self.assertIn("↑/↓ move", rows[-1])
        self.assertTrue(any(line.rstrip().endswith(screen_new.TAB_CUE.strip()) for line in rows), rows)
        self.assertTrue(all(len(line.rstrip()) <= 47 for line in rows), rows)
        # A wide pane says it in the footer instead.
        rows = frame((16, 100), "x" * 300)
        self.assertIn("tab details", rows[-1])
        self.assertFalse(any(screen_new.TAB_CUE.strip() in line for line in rows))

    def test_a_branch_checked_out_in_a_missing_folder_says_so_up_front(self):
        path = self.add("gone", "chore/gone")
        shutil.rmtree(path)
        cwd = str(self.repo)
        branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
        plan = branching.plan_branch(cwd, branches, "chore/gone")
        self.assertNotIn("open", plan)
        self.assertIn("folder is missing; prune it with p", plan["error"])
        rows = next(f for f in self.new_worktree_frames(list("chore/gone") + ["\x1b", "\x1b"], branches, (22, 100))
                    if any(line.startswith("/ chore/gone") for line in f))
        self.assertTrue(any(util.ICONS["error"] + " It's checked out in a worktree whose folder is missing" in line
                            for line in rows), rows)
        self.assertFalse(rows[-1].startswith("enter open"))
        self.assertFalse(any(line.startswith("enter:") for line in rows))

    def test_a_remotes_name_alone_cant_be_a_new_branch(self):
        cwd, branches = self.setup_branches()
        for typed in ("origin", "origin/"):
            self.assertEqual(branching.plan_branch(cwd, branches, typed)["error"],
                             "A branch can't be named origin, a remote's name.")
        self.assertNotIn("error", branching.plan_branch(cwd, branches, "originals"))

    def test_cleanup_hint_counts_only_what_cleanup_can_remove(self):
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False, "track": []},
                   {"name": "fix/a", "worktree": "/w/a", "track": ["remote deleted"], "dirty": True, "open": False},
                   {"name": "fix/b", "worktree": "/w/b", "track": ["remote deleted"], "dirty": False, "open": False}]

        def footer(entries):
            screen = FakeScreen(["q"], (24, 140))
            with patch.object(worktrees, "missing", return_value=False):
                drive(screen, self.picker(screen, entries).list_screen)
            return screen.text(0)[-1]

        self.assertIn("c clean up (1)", footer(entries))
        self.assertNotIn("clean up", footer(entries[:2]))  # Only a dirty one: no hint, though c still explains.

    def test_single_dirty_refusal_leads_with_the_reason_in_a_narrow_pane(self):
        name = ("review/cleanup-with-a-very-long-shared-prefix-and-a-lot-of-useful-context-that-is-needed-to-"
                "identify-it-and-a-distinguishing-tail-17")
        entries = [{"name": name, "worktree": "/w/a", "track": ["remote deleted"], "dirty": True}]
        screen = FakeScreen([], (16, 50))
        picker = self.picker(screen, entries)
        with patch.object(worktrees, "missing", return_value=False):
            self.assertFalse(picker.cleanup())
        rows = ["".join(t for t, _ in row) for row in picker.flash.rows(47)]
        self.assertLessEqual(len(rows), ui.Flash.LINES)
        text = " ".join(" ".join(rows).split())
        self.assertIn("Can't clean up: the only finished worktree has uncommitted changes; remove it on its own "
                      "with dd.", text)
        self.assertTrue(text.endswith("tail-17."), text)  # Its distinguishing end.
        self.assertFalse(any(row.endswith("…") and row is rows[-1] for row in rows))  # Nothing cut off.
        self.assertTrue(screen_remove.dirty_refusal(entries * 2, 47).startswith("Can't clean up: the 2 finished"))

    def test_cleanup_footer_stays_put_as_the_cursor_moves(self):
        entries = [{"name": f"fix/{c}", "worktree": f"/w/{c}", "track": ["remote deleted"], "dirty": False}
                   for c in "abc"]
        screen = FakeScreen(["G", "g", "g", "q"], (22, 100))
        picker = self.picker(screen, entries)
        picker.session, picker.terminals = "s", []
        with patch.object(worktrees, "missing", return_value=False):
            drive(screen, picker.cleanup)
        footers = {screen.text(i)[-1] for i in range(len(screen.frames))}
        self.assertEqual(len(footers), 1, footers)
        self.assertNotIn("tab details", footers.pop())  # Nothing is cut.

    def test_preview_fills_its_last_row_with_a_commit(self):
        entry = {"name": "fix/login", "worktree": str(self.repo), "track": ["remote deleted"], "dirty": True,
                 "open": False}
        picker = self.picker(None)
        picker.commits[(str(self.repo), None)] = ("Recent commits", [("abc1234", "subject", "me", 0)])
        rows = ["".join(t for t, _ in row) for row in picker.preview(entry, 47, True, 5)]
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows[-1].startswith("abc1234"), rows)
        self.assertNotIn("RECENT COMMITS", rows)
        gone = {"name": "x", "worktree": "/nonexistent/" + "long-folder-name/" * 3, "prunable": "gone", "track": []}
        rows = ["".join(t for t, _ in row) for row in picker.preview(gone, 47, False, 4)]
        self.assertTrue(rows[-1].endswith("…"), rows)  # The note, cut, says so.

    def test_prune_details_title_is_not_a_question(self):
        stale = [{"name": "chore/gone", "worktree": "/private/tmp/" + "very-long-folder-" * 6 + "gone",
                  "prunable": "gone"}]
        screen = FakeScreen(["\t", "q", "q"], (16, 50))
        drive(screen, self.picker(screen, stale).prune)
        titles = [screen.text(i)[0] for i in range(len(screen.frames))]
        self.assertTrue(any(t.startswith("Prune 1 missing worktree — details") for t in titles), titles)

if __name__ == "__main__":
    unittest.main()
