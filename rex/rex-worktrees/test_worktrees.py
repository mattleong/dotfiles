import curses
import json
import os
from pathlib import Path
import random
import re
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


def drive(screen, run, waiting=lambda: False):
    """Run a screen with the fake window, drawing into screen.frames. waiting says whether keys
    are queued (see keyboard.pending_input): by default never, as if each key came after the
    screen was drawn."""
    with patch.object(ui, "render", lambda stdscr, rows, cursor=None: screen.frames.append(rows)), \
            patch.object(keyboard, "unget", lambda key: screen.keys.insert(0, key)), \
            patch.object(keyboard, "pending_input", lambda stdscr: waiting()), \
            patch.object(curses, "flushinp", lambda: None):
        return run()


def typing(text):
    """The keys that type text in New worktree, which opens in normal mode: i, then the text."""
    return ["i"] + list(text)


# The keys that leave New worktree while typing: esc stops typing, esc clears the text, and esc,
# with no text left, goes back.
LEAVE = ["\x1b"] * 3

PAUSE = "pause"  # Among a Typed screen's keys: the end of a burst, as when someone reads the screen.


class Typed(FakeScreen):
    """Keys that arrive in bursts, as pasted text does: a key in a burst is already queued while
    the frame before it is drawn. PAUSE ends a burst."""
    def get_wch(self):
        while self.keys and self.keys[0] == PAUSE:
            self.keys.pop(0)
        return super().get_wch()

    def waiting(self):
        return bool(self.keys) and self.keys[0] != PAUSE

    def drive(self, run):
        return drive(self, run, self.waiting)


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
        # Remembered choices, such as the list's sort, go here rather than in your own state folder.
        state = patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        state.start()
        self.addCleanup(state.stop)

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
        self.assertEqual(labels, [util.ICONS["ahead"] + "1", util.ICONS["behind"] + "2", util.ICONS["main"] + " main",
                                  util.ICONS["tab"] + " tab", util.ICONS["dirty"] + " changes"])
        self.assertEqual(ui.badges_width(entry), len(" · ".join(labels)))

    def test_columns_align_and_hide_empty_ones(self):
        entries = [{"name": "main", "worktree": "/w/repo", "main": True, "open": True, "dirty": False, "track": []},
                   {"name": "a-very-long-branch-name-that-goes-past-forty-characters", "worktree": "/w/x",
                    "open": False, "dirty": True, "track": ["2 behind"]}]
        widths = ui.column_widths(entries)
        self.assertEqual(widths["PR"], 0)
        self.assertEqual(widths["BRANCH"], ui.MAX_BRANCH)  # Not the full name, which would push the others away.
        self.assertEqual(widths["SYNC"], len("SYNC"))  # Wider than ↓2.
        rows = [ui.entry_row(e, False, widths) for e in entries]
        text = ["".join(t for t, _ in row) for row in rows]
        header = "".join(t for t, _ in ui.header_row(widths))
        self.assertNotIn("PR", header)
        self.assertTrue(text[1][4:].startswith(ui.middle(entries[1]["name"], ui.MAX_BRANCH)[0] + "  "))
        self.assertTrue(header.rstrip().endswith("FOLDER"))
        # Every column starts at the same position in each row and in the header.
        for label in ("SYNC", "STATE"):
            self.assertIn(label, header)
        self.assertEqual(text[0].index(util.ICONS["main"]), text[1].index(util.ICONS["dirty"]))
        self.assertEqual(header.index("STATE"), text[0].index(util.ICONS["main"]))

    def test_narrow_panes_narrow_the_branch_then_turn_compact(self):
        path = str(self.repo)
        name = "feature/a-long-branch-name-that-keeps-going"
        entries = [{"name": "main", "worktree": path, "main": True, "open": True, "dirty": True, "track": []},
                   {"name": name, "worktree": path, "open": False, "dirty": False,
                    "track": ["2 ahead"], "pr": {"number": 1234, "state": "MERGED"}}]
        self.assertFalse(ui.column_widths(entries)["compact"])
        text = lambda entry, widths: "".join(t for t, _ in ui.entry_row(entry, False, widths))
        labels = util.ICONS["main"] + " main · " + util.ICONS["tab"] + " tab · " + util.ICONS["dirty"] + " changes"
        # The labels read the same at every width: first they keep the whole branch beside them.
        wide = ui.column_widths(entries, 110)
        self.assertEqual((wide["compact"], wide["BRANCH"]), (False, ui.MAX_BRANCH))
        self.assertIn(util.ICONS["pr"] + " #1234 merged", text(entries[1], wide))
        self.assertIn(labels, text(entries[0], wide))
        # Then the branch narrows, to MIN_BRANCH at most, and only then do the labels turn into icons.
        narrowed = ui.column_widths(entries, 95)
        self.assertFalse(narrowed["compact"])
        self.assertTrue(ui.MIN_BRANCH <= narrowed["BRANCH"] < ui.MAX_BRANCH)
        self.assertIn(labels, text(entries[0], narrowed))
        compact = ui.column_widths(entries, 80)
        self.assertTrue(compact["compact"])
        for widths, space in ((wide, 110), (narrowed, 95), (compact, 80)):
            for entry in entries:
                row = ui.entry_row(entry, False, widths)
                self.assertLessEqual(len("".join(t for t, _ in row[:-1]).rstrip()), space)
        self.assertTrue(text(entries[1], compact).rstrip().endswith(f"{util.ICONS['pr']}#1234 {util.ICONS['ahead']}2"))
        self.assertEqual([t for t, _ in ui.sync_cell({"track": ["1 behind", "remote deleted"]})],
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
        hints = ui.Keys().add("j/k", "move", rank=8).add("/", "filter").add("enter", "open").add("s", "split", rank=3)
        hints = hints.add("n", "new", rank=4).add("?", "help").add("q", "quit", rank=1).row()
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
            removed, pruned, deleted, failures = actions.remove_many(
                str(self.repo), "session:1", [(entries["refs/heads/one"], True), (entries["refs/heads/two"], False)],
                terminals)
        self.assertEqual((sorted(removed), pruned, deleted, failures), (["one", "two"], [], 1, []))
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
        # The main checkout stays first, whatever the order.
        self.assertEqual(names("recent"), ["main", "open", "dirty", "old"])
        self.assertEqual(names("name"), ["main", "dirty", "old", "open"])
        self.assertEqual(names("unknown"), names("recent"))
        entries.append({"name": "older-dirty", "time": 0, "dirty": True, "worktree": "/e"})
        with patch.object(worktrees, "missing", return_value=False):
            self.assertEqual(names("state"), ["main", "dirty", "older-dirty", "open", "old"])  # Newest first in each.

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
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.repo, "branch", "--set-upstream-to=origin/main", "main")
        git(self.repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "ahead")
        quick = worktrees.base_entries(str(self.repo), raw, details=False)
        # Ages come at once, since the list is sorted by them; ahead/behind can wait.
        self.assertIsNotNone(quick[0]["time"])
        self.assertEqual(quick[0]["track"], [])
        worktrees.fill_details(str(self.repo), quick)
        self.assertEqual(quick[0]["track"], ["1 ahead"])

    def test_flash_messages_fade_and_wrap(self):
        flash = ui.Flash()
        self.assertEqual((flash.rows(40), flash.wait(-1), flash.wait(150)), ([], -1, 150))
        with patch.object(ui.time, "monotonic", return_value=100.0):
            flash.set("Saved", "live")
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

    def test_vim_counts_and_rows_in_view(self):
        motion = keyboard.motion

        def press(keys, index=2, count=20, view=None):
            """Each key in turn. Returns (the index, what's pending, whether the last key was a motion)."""
            pending, moved = None, None
            for key in keys:
                moved, pending = motion(key, index, count, 6, pending, view)
                index = index if moved is None else moved
            return index, pending, moved is not None

        self.assertEqual(press("5j"), (7, None, True))
        self.assertEqual(press("3k"), (0, None, True))  # Clamped.
        self.assertEqual(press("12j"), (14, None, True))  # Digits add up.
        self.assertEqual(press("5G"), (4, None, True))  # Row 5.
        self.assertEqual(press("5gg"), (4, None, True))
        self.assertEqual(press("99G"), (19, None, True))  # Clamped to the last row.
        self.assertEqual(press("gg"), (0, None, True))
        self.assertEqual(press("G"), (19, None, True))
        self.assertEqual(press("10"), (2, "10", True))  # 0 adds to a count,
        self.assertEqual(press("0"), (2, None, False))  # but doesn't start one.
        self.assertEqual(press("5"), (2, "5", True))
        self.assertEqual(press("5g"), (2, "5g", True))
        self.assertEqual(press(["5", "\x1b"]), (2, None, True))  # esc cancels just the count,
        self.assertEqual(press(["5", "g", "\x1b"]), (2, None, True))  # or the g,
        self.assertEqual(press(["\x1b"]), (2, None, False))  # and is no motion of its own.
        # Any other key drops the count and does its one thing: d starts dd, never 5 of them.
        self.assertEqual(press("5d"), (2, None, False))
        self.assertEqual(press(["5", curses.KEY_DOWN]), (7, None, True))  # Arrows take a count too.
        self.assertEqual(press(["5", "\x04"]), (5, None, True))  # Half a page, as without it.
        self.assertEqual(press("g5"), (2, None, False))  # A count goes before g, not after it.
        self.assertEqual(press("9" * 20), (2, "9" * keyboard.COUNT_DIGITS, True))
        # H, M, and L: the top, middle, and bottom of the rows in view, where they're given.
        view = list(range(8, 14))
        self.assertEqual([press(key, 10, view=view)[0] for key in "HML"], [8, 10, 13])
        self.assertEqual([press(key, 10, view=[0, 1] + view)[0] for key in "HML"], [0, 9, 13])  # Not one run.
        self.assertEqual(press("H", 10), (10, None, False))  # No view, as in help: not motions.
        self.assertEqual(press("5L", 10, view=view), (13, None, True))  # A count before them is dropped.
        # A list with no rows takes no motion, nor a count.
        self.assertEqual(press("5", 0, 0), (0, None, False))
        self.assertEqual(keyboard.pending_note("5"), "5… then j or k to move 5 rows, or G for row 5")
        self.assertEqual(keyboard.pending_note("1"), "1… then j or k to move 1 row, or G for row 1")
        self.assertEqual(keyboard.pending_note("5g", "p to open the pull request"),
                         "5g… then g for row 5, or p to open the pull request")
        self.assertEqual(keyboard.pending_note("g"), "g… then g for the first row")

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
        self.assertEqual((new["branch"], new["verb"], new["action"]), ("test-branch", "Creates", "test-branch from main"))
        self.assertNotIn("error", new)
        local = branching.plan_branch(cwd, branches, "", self.suggestion(branches, "existing"))
        self.assertEqual((local["verb"], local["action"]), ("Checks out", "existing"))  # Named in full.
        remote = branching.plan_branch(cwd, branches, "", self.suggestion(branches, "origin/remote-only"))
        self.assertEqual((remote["verb"], remote["action"]), ("Creates", "remote-only tracking origin/remote-only"))
        # Half typed, a name that normalizing would end without what was typed last isn't created
        # without it: such as fix- on the way to fix-login, which would otherwise create fix.
        for typed in ("fix/", "v2.", "fix /", "fix-", "fix~", "fix:", "fix?", "fix@{", "fix*", "fix - "):
            plan = branching.plan_branch(cwd, branches, typed)
            self.assertTrue(plan.get("hint") and plan["error"].startswith("Type the rest of the name"), (typed, plan))
        self.assertEqual(branching.plan_branch(cwd, branches, "fix-")["error"],
                         "Type the rest of the name; it can't end with a dash.")
        self.assertIn("“@{”", branching.plan_branch(cwd, branches, "fix@{")["error"])
        for typed in ("fix ", "fix_", "fix@", "x.lock", "HEAD"):  # Spaces at the end, or names kept whole.
            self.assertNotIn("error", branching.plan_branch(cwd, branches, typed), typed)
        # Another base changes only where the branch starts.
        other = branching.from_base(new, {"ref": "origin/main", "label": "origin/main"})
        self.assertEqual((other["action"], other["args"][-1], other["path"]), ("test-branch from origin/main",
                                                                              "origin/main", new["path"]))
        # A typed name only ever creates a new branch; existing ones are picked from the list.
        self.assertIn("Already checked out", branching.plan_branch(cwd, branches, "main")["error"])
        self.assertIn("select it under Existing branches", branching.plan_branch(cwd, branches, "existing")["error"])
        self.assertIn("origin/remote-only exists", branching.plan_branch(cwd, branches, "remote-only")["error"])

    def test_branch_order_recent_local_then_recent_remote_then_everything_older(self):
        def commit_at(branch, when):
            env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
            subprocess.run(["git", "-C", str(self.repo), "checkout", "-q", "-b", branch, "main"], check=True)
            subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q",
                            "--allow-empty", "-m", branch], check=True, env=env)
        for branch, when in (("ancient-local", "2020-01-01T00:00:00"), ("old-local", "2029-09-01T00:00:00"),
                             ("ancient-remote", "2025-01-01T00:00:00"), ("new-remote", "2029-12-30T00:00:00"),
                             ("new-local", "2029-12-25T00:00:00"), ("newer-local", "2030-01-01T00:00:00")):
            commit_at(branch, when)
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        for branch in ("ancient-remote", "new-remote"):
            git(self.repo, "update-ref", f"refs/remotes/origin/{branch}", branch)
            git(self.repo, "branch", "-D", branch)
        now = 1893456000 + 86400  # 2030-01-02
        branches = branching.load_branches(str(self.repo), worktrees.worktrees(str(self.repo)), now)
        # A five-year-old remote branch no longer lists above a four-month-old local one.
        self.assertEqual([s["ref"] for s in branches["suggestions"]],
                         ["newer-local", "new-local", "origin/new-remote", "old-local", "origin/ancient-remote",
                          "ancient-local"])
        # New branches start from the default branch, else the current one, each saying which.
        self.assertEqual([(b["label"], b["note"]) for b in branches["bases"]], [("main", "default and current branch")])
        git(self.repo, "checkout", "-q", "--detach", "new-local")
        bases = branching.load_branches(str(self.repo), worktrees.worktrees(str(self.repo)), now)["bases"]
        self.assertEqual([b["note"] for b in bases], ["default branch", "current commit"])

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
        gone = {**clean_entry, "worktree": str(clean) + "-gone", "prunable": ""}
        self.assertIsNone(worktrees.removal_problem(gone))  # dd prunes it.
        self.assertIsNone(worktrees.removal_problem({**clean_entry, "prunable": ""}))  # Broken: dd repairs it.
        # Locked, as a worktree on a drive that isn't mounted is: Git keeps it, and so does dd.
        self.assertIn("unlock it to prune it", worktrees.removal_problem({**gone, "locked": ""}))
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

    def test_prune_drops_only_the_one_missing_worktree(self):
        """git worktree remove on a missing folder drops just its record, where git worktree prune
        would drop every missing one; the README relies on it."""
        for name in ("gone", "also-gone", "back"):
            shutil.rmtree(self.add(name, name))
        stale = {e["branch"][11:]: {**e, "name": e["branch"][11:]} for e in worktrees.worktrees(str(self.repo))
                 if "prunable" in e}
        self.assertEqual(sorted(stale), ["also-gone", "back", "gone"])
        actions.prune_worktree(str(self.repo), stale["gone"])
        left = [e["branch"][11:] for e in worktrees.worktrees(str(self.repo))[1:]]
        self.assertEqual(sorted(left), ["also-gone", "back"])  # The other missing ones stay.
        for branch in ("gone", "also-gone", "back"):
            git(self.repo, "rev-parse", "--verify", "refs/heads/" + branch)  # Branches are kept.
        # A folder that's back isn't pruned, since removing the worktree would delete it.
        Path(stale["back"]["worktree"]).mkdir()
        (Path(stale["back"]["worktree"]) / "work.txt").write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "folder is back"):
            actions.prune_worktree(str(self.repo), stale["back"])
        self.assertTrue((Path(stale["back"]["worktree"]) / "work.txt").exists())
        # In a cleanup, a missing one is pruned, keeping its branch even when deleting branches is checked.
        with patch.object(tabs, "close_panes") as close:
            removed, pruned, deleted, failures = actions.remove_many(str(self.repo), "s", [(stale["also-gone"], True)], [])
        self.assertEqual((removed, pruned, deleted, failures), ([], ["also-gone"], 0, []))
        git(self.repo, "rev-parse", "--verify", "refs/heads/also-gone")
        self.assertEqual(close.call_args[0][1], [])  # Nothing to close.

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
        score, name_hits, path_hits = matching.match_entry(entries[2], "ain")
        self.assertEqual(name_hits, {1, 2, 3})
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
        self.assertEqual(plan["action"], "from-main from main")
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
        # New worktree's prompt, with a mark: neither a placeholder nor long text pushes the mark out.
        prompt, place = screen_new.PROMPT, screen_new.PLACEHOLDER
        for text in ("", "feature payments refactor checkout flow for invoices"):
            row, column = ui.search_row(text, len(text), True, 47, place, "3/6", prompt)
            line = "".join(t for t, _ in row)
            self.assertTrue(line.startswith(prompt) and line.endswith("  3/6") and len(line) == 47, line)
            self.assertEqual(column, len(prompt) + (len(row[1][0]) if text else 0))  # After the text.

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
        entries = [{"name": "experiment/a-long-branch-name-that-is", "worktree": str(self.repo),
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
            self.assertTrue(row(dirty, 100).endswith(util.ICONS["dirty"] + " changes, can't be cleaned up"))
            self.assertTrue(row(dirty, 47).endswith(util.ICONS["dirty"] + " changes"))
            self.assertTrue(row(dirty, 36).rstrip().endswith("gone  " + util.ICONS["dirty"]))
            self.assertLessEqual(len(row(dirty, 36).rstrip()), 36)
            self.assertTrue(row(open_, 100).rstrip().endswith(util.ICONS["tab"] + " closes 1 pane"))
            for item in items:
                self.assertLessEqual(len(row(item, 47).rstrip()), 47)
            detail = screen_remove.RemovalScreens.cleanup_detail(items, 1, closes, 100)
            self.assertEqual([r[0][0].strip() for r in detail],
                             ["Its remote branch is gone. Closes 1 of 2 panes in Shell (this tab).", "fix/done"])

    def picker(self, screen, entries=None):
        """A Manager with just the state the screens use."""
        picker = object.__new__(app.Manager)
        picker.stdscr, picker.label, picker.origin_cwd, picker.repo = screen, "repo", str(self.repo), str(self.repo)
        picker.entries = entries or []
        picker.flash, picker.sort, picker.prs, picker.show_prs = ui.Flash(), "recent", {}, False
        picker.prs_loading = picker.details_loading = False
        picker.commits, picker.default, picker.terminals, picker.reveal = {}, None, None, None
        return picker

    def test_list_filters_only_after_a_slash(self):
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False, "track": []}]
        screen = FakeScreen(["f", "x", "/", "q", "#", "\x1b", "q"], (24, 80))
        picker = self.picker(screen, entries)
        drive(screen, picker.list_screen)  # esc stops typing, keeping the filter; the last q quits.
        frames = [screen.text(i) for i in range(len(screen.frames))]
        self.assertTrue(any("Press / to filter" in "".join(f) for f in frames))
        self.assertFalse(any(self.search_text(f).startswith("f") for f in frames))  # Letters didn't start a filter.
        self.assertTrue(any(self.search_text(f) == "q#" for f in frames))  # After /, q types.
        self.assertIn("    No matching worktrees", [line for f in frames for line in f])
        self.assertEqual(self.search_text(frames[-1]), "q#")

    def test_new_worktree_types_every_letter(self):
        cwd, branches = self.setup_branches()
        screen = FakeScreen(typing("quick") + [curses.KEY_END, "?", "q"] + LEAVE, (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        # esc stops typing, esc clears the text, and esc quits.
        self.assertFalse(drive(screen, lambda: picker.new_screen(root=True)))
        texts = [screen.text(i) for i in range(len(screen.frames))]
        typed = next(t for t in texts if any(line.startswith(screen_new.PROMPT + "quick") for line in t))
        self.assertTrue(any(line.startswith("› + Create “quick” from main") for line in typed), typed)
        self.assertTrue(any(line.startswith("New worktree — keys") for t in texts for line in t))
        self.assertTrue(any("q quit" in line for line in texts[-1]))
        self.assertFalse(any(line.startswith("/") for t in texts for line in t[:3]))  # / is the list's key.

    def test_new_worktree_opens_in_normal_mode_where_letters_are_keys(self):
        cwd, branches = self.setup_branches()
        screen = FakeScreen(["x", "j", "k", "a", "x", "\x1b", "/", "\x1b", "\x1b", "q"], (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        with patch.dict(ui.COLORS, {"dim": 1 << 20, "bold": 1 << 21, "yellow": 1 << 22, "cyan": 1 << 25}):
            self.assertFalse(drive(screen, lambda: picker.new_screen(root=True)))
        texts = [screen.text(i) for i in range(len(screen.frames))]
        # Not typing: the prompt is dim, there's no cursor, and the row asking for a name says
        # which key starts typing, as the input does.
        first = texts[0]
        self.assertIn(screen_new.PROMPT + screen_new.IDLE, first)
        prompt = next(row[0] for row in screen.frames[0] if row and row[0][0] == screen_new.PROMPT)
        self.assertEqual(prompt[1], 1 << 20)  # Dim, not the select role's cyan.
        self.assertIn("› + Press i and type a name to create a branch", first)
        footer = self.footer_keys(screen.frames[0])
        self.assertTrue({"i", "j/k", "q"} <= set(footer) and not {"esc", "enter"} & set(footer), footer)
        self.assertIn("i type", first[-1])
        # A letter that isn't a key does nothing but say how to type, in the attention role.
        said = next(seg for row in screen.frames[1] for seg in row if screen_new.NUDGE in seg[0])
        self.assertTrue(said[1] & (1 << 22), said)
        self.assertEqual(self.search_text(texts[1]), screen_new.IDLE)
        self.assertFalse(any(line.startswith("› + Press i") for line in texts[2]))  # j moved to a branch,
        self.assertIn("› + Press i and type a name to create a branch", texts[3])  # and k back.
        # a starts typing: x is text now, and esc stops typing, keeping the text and the row.
        self.assertEqual(self.search_text(texts[5]), "x")
        self.assertIn("esc done", texts[5][-1])
        self.assertEqual(self.search_text(texts[6]), "x")
        self.assertIn("esc clear", texts[6][-1])
        self.assertEqual([line for line in texts[5] if line.startswith("› ")],
                         [line for line in texts[6] if line.startswith("› ")])
        # / types too, at the end of the text; esc stops, esc clears, and q goes back at once.
        self.assertIn("esc done", texts[7][-1])
        self.assertEqual(self.search_text(texts[9]), screen_new.IDLE)
        self.assertEqual(len(texts), 10)
        # Opened from the list with text, as when its filter matched nothing, it's not typing
        # either: letters are keys, and the create row is selected.
        screen = FakeScreen(["x", "\x1b", "\x1b"], (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        self.assertFalse(drive(screen, lambda: picker.new_screen(text="fix-login")))
        texts = [screen.text(i) for i in range(len(screen.frames))]
        self.assertTrue(any(line.startswith("› + Create “fix-login” from main") for line in texts[0]), texts[0])
        self.assertEqual(self.search_text(texts[1]), "fix-login")  # x wasn't typed.
        self.assertIn(screen_new.NUDGE, " ".join(texts[1]))
        self.assertEqual(self.search_text(texts[2]), screen_new.IDLE)  # esc cleared, then went back.
        self.assertIn("q back", texts[2][-1])
        # Enter on the row asking for a name starts typing; while typing, it can't act there.
        screen = FakeScreen(["\n", "z", "\x1b", "\x1b", "\x1b"], (24, 100))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        drive(screen, lambda: picker.new_screen(root=True))
        self.assertNotIn("enter", screen.text(0)[-1])  # It starts typing, but only i is listed.
        self.assertIn("› + Type a name to create a branch", screen.text(1))
        self.assertIn("esc done", screen.text(1)[-1])
        self.assertEqual(self.search_text(screen.text(2)), "z")
        # Only the input being typed in has a cursor.
        self.assertIsNone(ui.search_row("", 0, False, 47, screen_new.PLACEHOLDER, "", screen_new.PROMPT, "",
                                        screen_new.IDLE)[1])
        self.assertIsNone(ui.search_row("fix", 3, False, 47, prompt=screen_new.PROMPT, idle=screen_new.IDLE)[1])
        self.assertEqual(ui.search_row("fix", 3, True, 47, prompt=screen_new.PROMPT, idle=screen_new.IDLE)[1],
                         len(screen_new.PROMPT) + 3)

    def test_new_worktree_opens_a_branch_thats_checked_out_already(self):
        cwd, branches = self.setup_branches()
        screen = FakeScreen(typing("main") + [curses.KEY_HOME, "\n"], (16, 60))
        path = branches["checked_out"]["main"]
        picker = self.picker(screen, [{"name": "main", "worktree": path, "branch": "refs/heads/main", "main": True}])
        picker.session, picker.client = "s", None
        picker.branches = lambda: branches
        with patch.object(tabs, "open_tab") as open_tab:
            self.assertTrue(drive(screen, lambda: picker.new_screen()))
        self.assertIs(open_tab.call_args[0][1], picker.entries[0])
        rows = next(screen.text(i) for i in range(len(screen.frames))
                    if "› Open the worktree for “main”" in screen.text(i))  # Green, as enter opens it.
        self.assertFalse(any("checked out" in line for line in rows), rows)  # The row and the plan say it.
        self.assertIn("Opens".ljust(screen_new.PLAN_COLUMN) + "the worktree for main", rows)
        self.assertIn(("In" + util.tilde(path)).replace(" ", ""), "".join(rows).replace(" ", ""))  # In the plan.
        self.assertTrue(rows[-1].startswith("enter open"))
        self.assertFalse(any("→" in line for line in rows), rows)  # The pointer is the only arrow.

    @staticmethod
    def plan_start(rows):
        """Where the plan below New worktree's list starts: the row with its first label, or None."""
        return next((i for i, line in enumerate(rows) if line.startswith(("Creates ", "Checks out ", "Opens "))), None)

    def two_bases(self):
        """Branches whose default, origin/main, is a commit behind main, the current branch."""
        cwd, _ = self.setup_branches()
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "ahead")
        return cwd, branching.load_branches(cwd, worktrees.worktrees(cwd))

    def test_new_worktree_creates_a_branch_from_either_base_at_once(self):
        cwd, branches = self.two_bases()
        self.assertEqual([(b["label"], b["note"]) for b in branches["bases"]],
                         [("origin/main", "default branch"), ("main", "current branch")])
        # With no text, one dim row asks for a name, and while typing, the footer leaves enter out;
        # enter makes the row stand out.
        screen, styles = self.frames_with_styles(["i", "\n"] + LEAVE[1:], branches, (24, 100))
        rows = screen.text(1)
        self.assertIn("› + Type a name to create a branch", rows)
        self.assertEqual(sum(line.startswith("› ") for line in rows), 1)
        self.assertNotIn("enter", self.footer_keys(screen.frames[1]))
        self.assertIsNone(self.plan_start(rows))
        row = next(r for r in screen.frames[2] if "Type a name" in "".join(s[0] for s in r))
        self.assertTrue(row[1][1] & styles["yellow"] and row[1][1] & styles["bold"], row)
        # Typed, a row for each base, the default first, each with the name as it will be.
        screen = FakeScreen(typing("Fix the login") + [curses.KEY_DOWN] + list(" now") + LEAVE, (24, 100))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        with patch.object(worktrees, "setup_command", return_value="make setup"):
            drive(screen, lambda: picker.new_screen(root=True))
        frames = [screen.text(i) for i in range(len(screen.frames))]
        rows = next(f for f in frames if self.search_text(f) == "Fix the login")
        first = next(line for line in rows if "from origin/main" in line)
        second = next(line for line in rows if "from main" in line)
        self.assertTrue(first.startswith("› + Create “Fix-the-login” from origin/main"), rows)
        self.assertTrue(second.startswith("  + Create “Fix-the-login” from main"), rows)
        self.assertEqual(first.index("default branch"), second.index("current branch"))  # The notes line up.
        plan = rows[self.plan_start(rows):]
        self.assertEqual(plan[0], "Creates".ljust(screen_new.PLAN_COLUMN) + "Fix-the-login from origin/main")
        self.assertTrue(plan[1].startswith("In".ljust(screen_new.PLAN_COLUMN)), plan)
        self.assertIn("Then runs".ljust(screen_new.PLAN_COLUMN) + "make setup", plan)
        self.assertIn("No matching branches", " ".join(rows))
        self.assertNotIn("EXISTING BRANCHES (0)", rows)  # One line, no heading over it.
        self.assertEqual(rows[-1].split()[:2], ["enter", "create"])
        # ↓ chooses main, and typing on keeps it.
        rows = next(f for f in frames if self.search_text(f) == "Fix the login now")
        self.assertTrue(any(line.startswith("› + Create “Fix-the-login-now” from main") for line in rows), rows)
        self.assertIn("Fix-the-login-now from main", rows[self.plan_start(rows)])
        # Passing main's row on the way to a branch doesn't choose it, and esc starts afresh.
        frames = self.new_worktree_frames(typing("i") + [curses.KEY_DOWN] * 2 + ["s", curses.KEY_DOWN, "x", "\x1b",
                                                                              "\x1b"] + typing("j") + LEAVE,
                                          branches, (24, 100))
        for typed, label in (("is", "› + Create “is” from origin/main"), ("isx", "› + Create “isx” from main"),
                             ("j", "› + Create “j” from origin/main")):
            rows = next(f for f in frames if self.search_text(f) == typed)
            self.assertTrue(any(line.startswith(label) for line in rows), (typed, rows))
        # Enter creates the branch at once, from the selected row's base, tracking nothing.
        for text, moves, start in (("from default", [], "origin/main"), ("from current", [curses.KEY_DOWN], "main")):
            screen = FakeScreen(typing(text) + moves + ["\n"], (24, 100))
            picker = self.picker(screen)
            picker.session, picker.client, picker.branches = "s", None, lambda: branches
            with patch.object(tabs, "open_tab") as open_tab, patch.object(worktrees, "setup_command", return_value=""):
                self.assertTrue(drive(screen, lambda: picker.new_screen()))
            name = branching.normalize_branch(text)
            with self.subTest(start=start):
                self.assertEqual(open_tab.call_args[0][1]["branch"], name)
                self.assertEqual(git(cwd, "rev-parse", name).stdout, git(cwd, "rev-parse", start).stdout)
                upstream = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", name + "@{upstream}"],
                                          capture_output=True)
                self.assertNotEqual(upstream.returncode, 0)

    def test_new_worktree_stays_with_a_message_when_creating_fails(self):
        cwd, branches = self.setup_branches()

        class Racing(FakeScreen):
            """Runs a key that's a function, such as one making the folder the plan named."""
            def get_wch(self):
                key = super().get_wch()
                if callable(key):
                    key()
                    return curses.KEY_RESIZE
                return key

        taken = branching.new_path(cwd, "race")
        screen = Racing(typing("race") + [lambda: (taken.mkdir(parents=True), (taken / "file").write_text("x")), "\n",
                                        "\n"], (24, 120))
        picker = self.picker(screen)
        picker.session, picker.client = "s", None
        picker.branches = lambda: branching.load_branches(cwd, worktrees.worktrees(cwd))
        picker.reload = lambda: None
        with patch.object(tabs, "open_tab") as open_tab, patch.object(worktrees, "setup_command", return_value=""):
            self.assertTrue(drive(screen, lambda: picker.new_screen()))
        said = next(screen.text(i) for i in range(len(screen.frames)) if "Destination already exists" in
                    " ".join(screen.text(i)))
        self.assertTrue(said[-1].startswith("enter create"), said)  # Still on the screen.
        # The first enter created nothing, not even the branch; the second used another folder.
        self.assertEqual(open_tab.call_count, 1)
        made = Path(open_tab.call_args[0][1]["worktree"])
        self.assertNotEqual(made, taken)
        self.assertTrue(made.name.startswith("race-"))
        self.assertEqual(sorted(os.listdir(taken)), ["file"])
        # A tab that fails to open after the worktree is made isn't a failure to create: it's said
        # on the picker's error screen, which exits.
        screen = FakeScreen(typing("tab-fails") + ["\n"], (24, 120))
        picker = self.picker(screen)
        picker.session, picker.client, picker.branches = "s", None, lambda: branches
        with patch.object(tabs, "open_tab", side_effect=RuntimeError("no session")), \
                patch.object(worktrees, "setup_command", return_value=""):
            with self.assertRaises(screen_new.TabFailed):
                drive(screen, lambda: picker.new_screen())
        self.assertTrue(branching.new_path(cwd, "tab-fails").exists() or
                        any("tab-fails" in line for line in git(cwd, "worktree", "list").stdout.decode().splitlines()))

    def test_long_errors_scroll(self):
        message = "Preparing worktree\nfatal: " + "cannot lock ref because the path is long " * 30 + "the end"
        screen = FakeScreen(["j"] * 40 + ["q"], (16, 50))
        drive(screen, lambda: ui.show_error(screen, message))
        first, last = screen.text(0), screen.text()
        self.assertRegex(first[0].rstrip(), r"  1–\d+ of \d+$")  # The rows in view.
        self.assertIn("j/k scroll", first[-1])
        lead = next(line for line in first if util.ICONS["error"] in line)
        self.assertIn(util.ICONS["error"] + " fatal: cannot lock ref", lead)
        self.assertFalse(any("Preparing worktree" in line for line in first))  # It goes after the error.
        self.assertIn("the end", " ".join(line.strip() for line in last))
        self.assertIn("Preparing worktree", last[-2])
        _, end, _, total = last[0].replace("–", " ").split()[-4:]
        self.assertEqual(end, total)

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
        self.assertTrue(text[0].startswith(" " * screen_remove.LABEL_AT + "Has uncommitted changes"))
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
        self.add("one", "one")
        self.add("two", "two")
        entries = [{**e, "name": e["branch"][11:]} for e in worktrees.worktrees(str(self.repo))[1:]]
        real_git = util.git

        def remove_but_keep_folder(cwd, *args):
            """Git drops the worktree from its list, but some of the folder is left."""
            out = real_git(cwd, *args)
            if args[:2] == ("worktree", "remove"):
                Path(args[-1]).mkdir()
                raise RuntimeError("error: failed to delete: Permission denied")
            return out

        with patch.object(util, "git", remove_but_keep_folder), patch.object(tabs, "close_panes"):
            removed, pruned, deleted, failures = actions.remove_many(str(self.repo), "s", [(entries[0], True)], [])
        # Removed, so its branch was deleted as asked; the message says what was left.
        self.assertEqual((removed, pruned, deleted), (["one"], [], 1))
        self.assertEqual(failures, ["one was removed, but its folder couldn't be deleted entirely: "
                                    "error: failed to delete: Permission denied"])
        # Git refusing, as for uncommitted changes, removes nothing, and says so.
        (Path(entries[1]["worktree"]) / "notes.txt").write_text("changes")
        with patch.object(tabs, "close_panes"):
            removed, pruned, deleted, failures = actions.remove_many(str(self.repo), "s", [(entries[1], True)], [])
        self.assertEqual((removed, deleted), ([], 0))
        self.assertTrue(failures[0].startswith("two wasn't removed: "), failures)
        git(self.repo, "rev-parse", "--verify", "refs/heads/two")
        # One at a time: what was done, then what wasn't.
        screen = FakeScreen([], (24, 160))
        picker = self.picker(screen)
        picker.session, picker.terminals, picker.reload = "s", [], lambda: None
        picker.removal_options = lambda *args, **kwargs: {"delete_branch"}
        with patch.object(worktrees, "merged_into", return_value="main"), \
                patch.object(actions, "remove_worktree", return_value=(False, ["its branch wasn't deleted: no."])):
            drive(screen, lambda: picker.remove(entries[1]))
        self.assertEqual(picker.flash.text, "Removed two, but its branch wasn't deleted: no.")
        with patch.object(worktrees, "merged_into", return_value=None):
            drive(screen, lambda: picker.remove(entries[1]))
        self.assertTrue(picker.flash.text.startswith("two wasn't removed: "), picker.flash.text)
        self.assertTrue((Path(entries[1]["worktree"]) / "notes.txt").exists())

    def test_help_lines_up_descriptions_across_sections(self):
        sections = [("Move", [("ctrl+d / ctrl+u", "half page down / up"), ("q", "quit")]),
                    ("Labels", [("herdr, t3, claude, rex", "the tool"), ("3d", "age")])]
        rows = ["".join(t for t, _ in row) for row in ui.help_body(sections, 77)]
        self.assertEqual(rows, ["", "MOVE", "ctrl+d / ctrl+u   half page down / up", "q                 quit", "",
                                "LABELS", "herdr, t3, claude, rex", "                  the tool",
                                "3d                age"])  # A wide key goes above its description.
        # In a narrow pane, so does a key wider than a quarter of it, leaving the descriptions room.
        rows = ["".join(t for t, _ in row) for row in ui.help_body(sections, 47)]
        self.assertEqual(rows[2:4], ["ctrl+d / ctrl+u", "     half page down / up"])
        # Help wraps at a readable width, even in a wide pane.
        rows = ["".join(t for t, _ in row) for row in ui.help_body([("Keys", [("x", "word " * 60)])], 200)]
        self.assertTrue(len(rows) > 3 and all(len(row.rstrip()) <= ui.HELP_WIDTH for row in rows), rows)

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
        with patch.object(worktrees, "setup_command", return_value=setup):
            drive(screen, lambda: picker.new_screen(root=True))
        return [screen.text(i) for i in range(len(screen.frames))]

    def test_new_worktree_keeps_the_create_row_for_weak_matches(self):
        long_name = "experiment/very-long-branch-name-to-test-truncation-behaviour"
        for name in (long_name, "feature/remote-only-pr", "release/login-redirect"):
            git(self.repo, "branch", name)
        cwd, branches = self.setup_branches()
        # main is checked out, so its row opens it; remote-pr only matches weakly.
        for typed, label in (("main", "Open the worktree for “main”"),
                             ("remote-pr", "+ Create “remote-pr” from main")):
            frames = self.new_worktree_frames(typing(typed) + LEAVE, branches)
            rows = next(f for f in frames if any(line.startswith(screen_new.PROMPT + typed) for line in f))
            self.assertTrue(any(line.startswith(f"› {label}") for line in rows), (typed, rows))
        # A word the branch starts with, or its whole name, selects it, and the plan names it, whole,
        # not broken at its dash.
        frames = self.new_worktree_frames(typing("login") + LEAVE, branches, (24, 60))
        rows = next(f for f in frames if any(line.startswith(screen_new.PROMPT + "login") for line in f))
        self.assertTrue(any(line.startswith("› release/login-redirect") for line in rows))
        self.assertEqual(rows[self.plan_start(rows)], "Checks out".ljust(screen_new.PLAN_COLUMN) + "release/login-redirect")

    def test_already_checked_out_shows_whichever_row_is_selected(self):
        git(self.repo, "branch", "main-old")
        cwd, branches = self.setup_branches()
        frames = self.new_worktree_frames(typing("main") + [curses.KEY_DOWN] + LEAVE, branches, (24, 60))
        rows = next(f for f in frames if self.search_text(f) == "main" and any(line.startswith("› main-old") for line in f))
        create = next(i for i, line in enumerate(rows) if "  Open the worktree for “main”" in line)
        self.assertIn("Already checked out", rows[create + 1])  # Right below the create row.

    def test_new_worktree_plan_keeps_the_branch_tail_and_setup(self):
        names = ["experiment/very-long-branch-name-to-test-truncation-behaviour-" + "x" * 40 + "-available-" + tail
                 for tail in ("alpha", "beta")]
        for name in names:
            git(self.repo, "branch", name)
        cwd, branches = self.setup_branches()
        setup = 'printf "sandbox setup complete\\n"'
        frames = self.new_worktree_frames(typing("available") + [curses.KEY_DOWN, "\t", "q"] + LEAVE,
                                          branches, (16, 50), setup)
        rows = next(f for f in frames if any(line.startswith(screen_new.PROMPT + "available") for line in f))
        plan = rows[self.plan_start(rows):]
        self.assertTrue(any(line.rstrip().endswith("available-alpha") for line in plan), plan)  # The selected branch.
        self.assertTrue(any(line.startswith("Then runs   printf") for line in plan), plan)
        self.assertIn("tab details", rows[-1])
        details = next(f for f in frames if f[0].startswith("New worktree — details"))
        # ↓ chose beta; in full.
        self.assertIn(names[1], "".join(line[screen_new.PLAN_COLUMN:].strip() for line in details))
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
        self.assertIn("  …and 15 more (tab lists all)", rows)
        self.assertIn("  Shell (this tab)  2 of 4 panes; the tab stays", rows)  # The short list whole.
        # Discard's hint right under it, and the note apart from it, both lined up with its label.
        discard = rows.index("› [ ] Discard uncommitted changes")
        self.assertEqual(rows[discard + 1:discard + 4],
                         ["      " + screen_remove.DISCARD_HINT, "", "      The branch will be kept."])
        self.assertEqual(sum("final-suffix" in line for line in rows), 1)  # The name, once.
        footer = "".join(t for t, _ in ui.fit(screen.frames[0][-1], 47))
        self.assertTrue(footer.rstrip().endswith("q cancel"))
        details = [screen.text(i) for i in range(len(screen.frames)) if screen.text(i)[0].startswith("Remove worktree — details")]
        self.assertTrue(details and any("file15.txt" in line for line in details[-1]))  # Scrolled to the end.
        rows = show((12, 50), ["q"]).text(0)
        self.assertIn("  16 uncommitted files  (tab lists them)", rows)  # A line for each list, at least.
        self.assertTrue(any("PANES THAT WILL CLOSE" in line or "3 panes will close" in line for line in rows), rows)

    def test_destructive_footers_keep_cancel_over_help(self):
        entries = [{"name": f"fix/{c}", "worktree": f"/w/{c}", "track": ["remote deleted"], "dirty": False}
                   for c in "ab"]
        screen = FakeScreen(["q"], (16, 50))
        picker = self.picker(screen, entries)
        picker.session, picker.terminals = "s", []
        with patch.object(worktrees, "missing", return_value=False):
            drive(screen, picker.cleanup)
        row = "".join(t for t, _ in ui.fit(screen.frames[0][-1], 47))
        self.assertEqual(row.split(), ["space", "toggle", "enter", "remove", "2", "q", "cancel"])
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False, "track": []}]
        screen = FakeScreen(["q"], (24, 33))
        drive(screen, self.picker(screen, entries).list_screen)
        self.assertIn("? help", "".join(t for t, _ in ui.fit(screen.frames[0][-1], 30)))

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
        label = rows[option].index("Also delete")
        self.assertEqual(label, screen_remove.LABEL_AT)
        # The selected row's detail starts where the options' labels do, as the option's own note does.
        self.assertTrue(rows[option + 2].startswith(" " * label + "Its remote branch is gone."), rows)

    def test_prune_shows_the_whole_folder_and_keeps_the_branch(self):
        stale = {"name": "chore/bump-deps", "worktree": "/private/tmp/rexwt-review-opus-r3/reviewopusr3-gone",
                 "branch": "refs/heads/chore/bump-deps", "prunable": "gitdir file points to non-existent location"}
        screen = FakeScreen(["q"], (16, 50))
        picker = self.picker(screen, [stale])
        drive(screen, lambda: picker.remove(stale))  # dd on a missing folder prunes it.
        rows = [line.rstrip() for line in screen.text(0)]
        self.assertTrue(rows[0].startswith("Prune missing worktree"), rows)
        self.assertTrue(all(len(line) <= 47 for line in rows), rows)
        self.assertIn("reviewopusr3-gone  " + ui.label("missing")[0], rows)  # The folder whole, labeled.
        self.assertIn(screen_remove.PRUNE_TEXT, " ".join(rows))
        self.assertIn("The branch will be kept.", rows)  # No options, so not indented under them.
        self.assertEqual(self.footer_keys(screen.frames[0]), ["enter", "?", "q"])
        self.assertTrue(screen.text(0)[-1].startswith("enter prune"))

    def test_an_existing_branchs_name_selects_it_without_an_error(self):
        cwd, branches = self.setup_branches()
        for typed, plan in (("existing", "Checks out".ljust(screen_new.PLAN_COLUMN) + "existing"),
                            ("remote-only", "remote-only tracking origin/remote-only")):
            rows = next(f for f in self.new_worktree_frames(typing(typed) + LEAVE, branches, (24, 60))
                        if any(line.startswith(screen_new.PROMPT + typed) for line in f))
            self.assertIn(f"  + Create “{typed}”", rows)  # Dim, with no base: it can't be created.
            self.assertIn("  " + util.ICONS["info"] + " This branch exists; enter checks it out.", rows)
            self.assertFalse(any(util.ICONS["error"] in line for line in rows), rows)
            self.assertTrue(any(line.startswith("› " + typed) for line in rows))
            self.assertIn(plan, " ".join(rows))
        # Moved back to the create row, the error applies again.
        frames = self.new_worktree_frames(typing("existing") + [curses.KEY_UP] + LEAVE, branches, (24, 60))
        rows = next(f for f in frames if "› + Create “existing”" in f)
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
            rows = next(f for f in self.new_worktree_frames(typing(typed) + LEAVE, branches, (24, 60))
                        if any(line.startswith(screen_new.PROMPT + typed) for line in f))
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
        screen = FakeScreen(typing("available") + LEAVE, (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        with patch.object(worktrees, "setup_command", return_value='printf "setup complete\\n"'):
            drive(screen, lambda: picker.new_screen(root=True))
        frame = next(f for f in screen.frames if any("available" in t for t, *_ in f[1]))
        footer = "".join(t for t, _ in ui.fit(frame[-1], 47))
        self.assertIn("↑/↓ move", footer)

    def test_list_without_a_selection_hides_and_explains_row_actions(self):
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False, "track": []}]
        screen = FakeScreen(["/"] + list("zzz") + ["\x1b", "s", "q"], (24, 100))
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
        self.assertIn("press dd to prune it.", " ".join(" ".join(rows(False)).split()))
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
        self.assertEqual(picker.flash.tone, "attention")

    def test_dd_on_a_missing_folder_prunes_just_it(self):
        path = self.add("gone", "docs/gone")
        self.add("other", "docs/other-gone")
        shutil.rmtree(path)
        shutil.rmtree(Path(self.tmp.name) / "other")
        entries = worktrees.base_entries(str(self.repo), details=False)
        for e in entries:  # docs/gone the newest, so it's listed after the main checkout.
            e.update(dirty=False, open=False, time=2 if e["name"] == "docs/gone" else 1)
        gone = next(e for e in entries if e["name"] == "docs/gone")
        # The list offers dd as prune on it, and the half-typed dd says so.
        screen = FakeScreen(["j", "d", "q", "q"], (24, 120))
        picker = self.picker(screen, entries)
        for e in entries:
            picker.commits[(e["worktree"], None)] = ("Recent commits", [])
        drive(screen, picker.list_screen)
        self.assertTrue(any(line.startswith("› ") and "docs/gone " in line for line in screen.text(1)), screen.text(1))
        self.assertIn("dd prune", screen.text(1)[-1])
        self.assertIn("d… press d again to prune docs/gone", screen.text(2))
        # Confirmed, Git drops only its record; the other missing one and both branches stay.
        screen = FakeScreen(["\n"], (24, 120))
        picker = self.picker(screen, entries)
        picker.reload = lambda: None
        drive(screen, lambda: picker.remove(gone))
        self.assertEqual(picker.flash.text, "Pruned docs/gone; its branch is kept.")
        self.assertEqual([e["branch"] for e in worktrees.worktrees(str(self.repo))[1:]], ["refs/heads/docs/other-gone"])
        git(self.repo, "rev-parse", "--verify", "refs/heads/docs/gone")

    def test_cleanup_offers_missing_worktrees_to_prune(self):
        def entry(name, **state):
            return {"name": name, "worktree": "/w/" + name, "branch": "refs/heads/" + name, "dirty": False,
                    "open": False, "track": [], **state}

        main = entry("main", main=True, worktree=str(self.repo))
        done, dirty = entry("fix/done", track=["remote deleted"]), entry("fix/dirty", track=["remote deleted"], dirty=True)
        gone = entry("docs/gone", prunable="gone", dirty=None)
        kept = entry("ext/unmounted", prunable="gone", locked="", dirty=None)  # Git keeps a locked one.
        missing = patch.object(worktrees, "missing", side_effect=lambda e: "prunable" in e)

        def run(entries, keys, size=(24, 100), listed=False):
            """Cleanup, or with listed, the list."""
            screen = FakeScreen(keys, size)
            picker = self.picker(screen, entries)
            picker.session, picker.terminals, picker.reload = "s", [], lambda: None
            for e in entries:
                picker.commits[(e["worktree"], None)] = ("Recent commits", [])
            with missing:
                drive(screen, picker.list_screen if listed else picker.cleanup)
            return screen, picker

        # The list's c counts what cleanup removes as it opens: the clean finished one, but not the
        # missing one, which starts unchecked, since its folder may be away only for now.
        everything = [main, done, dirty, gone, kept]
        screen, _ = run(everything, ["q"], (24, 140), listed=True)
        self.assertIn("c clean up 1", screen.text(0)[-1])
        # Cleanup offers the missing one with its own reason, unchecked, and enter's count agrees.
        screen, _ = run(everything, ["j", "j", " ", "q"])
        rows = screen.text(0)
        self.assertTrue(any("[ ] docs/gone" in line and line.rstrip().endswith("missing") for line in rows), rows)
        self.assertFalse(any("ext/unmounted" in line for line in rows))
        self.assertIn("enter remove 1", rows[-1])
        self.assertTrue(any(line.startswith("Their remote branches are gone, or their folders are missing.")
                            for line in rows), rows)
        on_missing = " ".join(" ".join(screen.text(2)).split())  # Its detail says what pruning does.
        self.assertIn(screen_remove.PRUNE_TEXT + " Its branch is kept. " + screen_remove.UNCHECKED_TEXT, on_missing)
        self.assertNotIn("closes", on_missing)
        self.assertIn("enter remove 2", screen.text(3)[-1])  # Checked, it counts.
        # Enter removes the finished one and prunes the missing one, never deleting the pruned one's branch.
        with patch.object(actions, "remove_many", return_value=(["fix/done"], ["docs/gone"], 1, [])) as many, \
                patch.object(worktrees, "merged_into", return_value="main"):
            _, picker = run(everything, ["j", "j", " ", "G", " ", "\n"])
        self.assertEqual([(e["name"], delete) for e, delete in many.call_args[0][2]],
                         [("fix/done", True), ("docs/gone", False)])
        self.assertEqual(picker.flash.text, "Removed 1 worktree and its branch. Pruned 1 missing worktree.")
        # What did and didn't happen, when part of it fails.
        with patch.object(actions, "remove_many", return_value=([], ["docs/gone"], 0, ["fix/done wasn't removed: no"])):
            _, picker = run(everything, ["\n"])
        self.assertEqual(picker.flash.text, "Pruned 1 missing worktree. fix/done wasn't removed: no.")
        # Only missing ones: no branches to delete, nothing checked, so enter has nothing to do and
        # the title says so, and c has no count; checked, enter prunes.
        screen, _ = run([main, gone], [" ", "q"])
        rows = screen.text(0)
        self.assertFalse(any("Also delete" in line for line in rows), rows)
        self.assertNotIn("enter", rows[-1])
        self.assertTrue(rows[1].startswith("Clean up worktrees") and "nothing checked yet" in rows[1], rows)
        self.assertIn("enter prune 1", screen.text(1)[-1])
        self.assertNotIn("nothing checked", screen.text(1)[1])
        screen, _ = run([main, gone], ["q"], (24, 140), listed=True)
        self.assertRegex(screen.text(0)[-1], r"c clean up   \? help")
        # With nothing to clean up, c says what it looks for.
        _, picker = run([main, kept], ["q"])
        self.assertIn("or a missing folder", picker.flash.text)

    def test_enter_without_discard_points_at_it(self):
        entry = {"name": "fix/x", "worktree": str(self.repo), "branch": "refs/heads/fix/x"}
        options = [{"id": "force", "checked": False, "label": [("Discard uncommitted changes", 0)]},
                   {"id": "delete_branch", "checked": False, "label": [("Also delete the branch", 0)]}]
        screen = FakeScreen(["j", "\n", " ", "\n"], (16, 50))
        picker = self.picker(screen)
        picker.session = "s"
        with patch.dict(ui.COLORS, {"yellow": 1 << 20, "dim": 1 << 21}):
            result = drive(screen, lambda: picker.removal_options(entry, ["?? a.txt"], [], "main", options))
        hint = lambda frame: next(row for row in screen.frames[frame] if row and row[0][0].strip() == screen_remove.DISCARD_HINT)
        self.assertEqual(hint(1)[0][1], 1 << 21)  # Dim,
        self.assertEqual(hint(2)[0][1], 1 << 20)  # Enter made it stand out, in the attention role,
        self.assertTrue(screen.text(2)[screen.text(2).index("      " + screen_remove.DISCARD_HINT) - 1].startswith("› "))
        self.assertFalse(any(screen_remove.DISCARD_HINT in line for line in screen.text(2)[-2:]))  # not at the bottom,
        self.assertIn("      " + screen_remove.DISCARD_CHECKED, screen.text(3))  # and space, on Discard, checked it.
        self.assertEqual(result, {"force"})

    def test_cleanup_message_names_the_deleted_branches(self):
        done = screen_remove.cleanup_done
        self.assertEqual(done(1, 0), "Removed 1 worktree.")
        self.assertEqual(done(1, 1), "Removed 1 worktree and its branch.")
        self.assertEqual(done(2, 2), "Removed 2 worktrees and their branches.")
        self.assertEqual(done(3, 2, 1), "Removed 3 worktrees and 2 of their branches; 1 with unmerged commits was kept.")
        self.assertEqual(done(1, 0, 1), "Removed 1 worktree; its branch had unmerged commits, so it was kept.")
        self.assertEqual(done(2, 2, 0, 1), "Removed 2 worktrees and their branches. Pruned 1 missing worktree.")
        self.assertEqual(done(0, 0, 0, 2), "Pruned 2 missing worktrees.")

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
        drive(screen, lambda: self.picker(screen, stale).prune(stale[0]))
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
        # Its, since the other has uncommitted changes, so its branch can't be deleted here.
        self.assertIn("Also delete its branch ", "".join(screen.text(0)))
        details = next(screen.text(i) for i in range(len(screen.frames)) if "Clean up worktrees — details" in screen.text(i)[0])
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
                rows = next(f for f in self.new_worktree_frames(typing(typed) + LEAVE, branches, size)
                            if any(line.startswith(screen_new.PROMPT + typed) for line in f))
                with self.subTest(typed=typed, size=size):
                    self.assertEqual(sum(line.startswith("  feature/remote-two") or line.startswith("› feature/remote-two")
                                         for line in rows), 1, rows)  # Only that remote's.
                    selected = next(line for line in rows if line.startswith("› feature/remote-two"))
                    self.assertTrue(selected.rstrip().endswith(remote), selected)  # Two remotes: each named.
                    self.assertIn(f"tracking {remote}/feature/remote-two", " ".join(" ".join(rows).split()))
                    self.assertFalse(any(util.ICONS["error"] in line for line in rows), rows)
        # Enter checks out origin's commit, tracking origin, though fork's is listed first.
        screen = FakeScreen(typing("origin/feature/remote-two") + ["\n"], (16, 50))
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
        flash.set("Opened experiment/very-long-branch-name in a background tab in the repo session.", "live")
        rows = ["".join(t for t, _ in row) for row in flash.rows(40)]
        self.assertTrue(rows[0].startswith(util.ICONS["ok"] + " Opened"))
        self.assertTrue(all(row.startswith("  ") and not row.startswith("   ") for row in rows[1:]), rows)
        note = ["".join(t for t, _ in row) for row in screen_new.note_rows(
            "origin/feature/remote-two exists; select it under Existing branches.", "error", "danger", 47)]
        self.assertEqual(note[1][:4], "    ")  # The note's indent, then under the text.
        self.assertNotEqual(note[1][4], " ")

    def test_new_worktree_plan_cut_at_50_columns_says_tab_shows_it(self):
        git(self.repo, "branch", "fix/login")
        cwd, branches = self.setup_branches()

        def frame(size, setup):
            screen = FakeScreen(typing("login") + LEAVE, size)
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
        rows = frame((16, 100), "x" * 600)
        self.assertIn("tab details", rows[-1])
        self.assertFalse(any(screen_new.TAB_CUE.strip() in line for line in rows))

    def test_a_branch_checked_out_in_a_missing_folder_says_so_up_front(self):
        path = self.add("gone", "chore/gone")
        shutil.rmtree(path)
        cwd = str(self.repo)
        branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
        plan = branching.plan_branch(cwd, branches, "chore/gone")
        self.assertNotIn("open", plan)
        self.assertIn("folder is missing; prune it with dd", plan["error"])
        rows = next(f for f in self.new_worktree_frames(typing("chore/gone") + LEAVE, branches, (22, 100))
                    if any(line.startswith(screen_new.PROMPT + "chore/gone") for line in f))
        self.assertTrue(any(util.ICONS["error"] + " It's checked out in a worktree whose folder is missing" in line
                            for line in rows), rows)
        self.assertFalse(rows[-1].startswith("enter open"))
        self.assertIsNone(self.plan_start(rows), rows)  # No plan: enter can't go anywhere.

    def test_a_remotes_name_alone_cant_be_a_new_branch(self):
        cwd, branches = self.setup_branches()
        self.assertEqual(branching.plan_branch(cwd, branches, "origin")["error"],
                         "origin is a remote; type origin/ to list its branches.")
        self.assertEqual(branching.plan_branch(cwd, branches, "origin/")["error"],
                         "origin is a remote; its branches are below.")
        self.assertTrue(branching.plan_branch(cwd, branches, "origin/")["hint"])
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

        self.assertIn("c clean up 1", footer(entries))
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
        drive(screen, lambda: self.picker(screen, stale).prune(stale[0]))
        titles = [screen.text(i)[0] for i in range(len(screen.frames))]
        self.assertTrue(any(t.startswith("Prune missing worktree — details") for t in titles), titles)

    def test_single_dirty_refusal_fits_names_of_any_length(self):
        long_name = "review/cleanup-with-a-very-long-shared-prefix-and-context-" * 3 + "tail-17"
        for name in ("a", "fix/a", "hotfix", "release", "fix/abcd", "hotfix/1", "fix/login-flow", long_name):
            for width in range(10, 161):
                with self.subTest(name=name, width=width):
                    text = screen_remove.dirty_refusal([{"name": name}], width)
                    self.assertTrue(text.startswith("Can't clean up: the only finished worktree has uncommitted"))
                    flash = ui.Flash()
                    flash.set(text, "yellow")
                    rows = ["".join(t for t, _ in row) for row in flash.rows(width)]
                    self.assertTrue(all(len(row) <= width for row in rows), rows)
                    if len(name) <= 8:
                        self.assertTrue(text.endswith(f"It's {name}."), text)  # Short names whole.
                    if width >= 47:
                        self.assertLessEqual(len(ui.hang(util.ICONS["info"], text, width)), ui.Flash.LINES)
                        self.assertTrue(text.endswith(name[-8:] + "."), text)  # Its distinguishing end.

    def test_c_with_one_dirty_candidate_selects_it_for_dd(self):
        for name in ("fix/a", "release", "fix/abcdefgh-ijk"):
            for size in ((16, 50), (22, 100), (40, 140)):
                entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False,
                            "track": []},
                           {"name": "fix/b", "worktree": "/w/b", "dirty": False, "open": False, "track": []},
                           {"name": name, "worktree": "/w/a", "track": ["remote deleted"], "dirty": True,
                            "open": False}]
                # Filtered to main, so c has to clear the filter to select the candidate.
                screen = FakeScreen(["/", "m", "a", "i", "n", "\x1b", "c", "d", "d"], size)
                picker = self.picker(screen, entries)
                for e in entries:
                    picker.commits[(e["worktree"], None)] = ("Recent commits", [])
                removed = []
                picker.remove = lambda entry: removed.append(entry) or True
                picker.terminals = []
                with self.subTest(name=name, size=size), patch.object(worktrees, "missing", return_value=False):
                    drive(screen, picker.list_screen)
                    self.assertEqual(removed, [entries[2]])  # dd removed the candidate.
                    after = next(screen.text(i) for i in range(len(screen.frames))
                                 if "Can't clean up" in "".join(screen.text(i)))
                    self.assertTrue(any(line.startswith("  › ") or line.startswith("› ") for line in after))
                    self.assertTrue(any(name[:5] in line and "›" in line for line in after), after)
                    self.assertIn("/ to filter", after)  # The filter that hid it was cleared.
                    message = " ".join(" ".join(after).split())
                    self.assertIn("remove it on its own with dd", message)

    def test_wrapping_keeps_the_spaces_inside_a_command(self):
        setup = 'printf "keep  two  spaces\\n" && printf "%s\\n" "/tmp/' + "long-folder-name-" * 9 + 'end"'
        for text in ("then runs " + setup, "  indented  " + "x" * 50 + "   gap", "a" + " " * 60 + "b " + "c/" * 40):
            for width in range(4, 161):
                lines = ui.wrap(text, width)
                with self.subTest(text=text[:20], width=width):
                    self.assertTrue(all(len(line) <= width and line in text for line in lines), lines)
                    self.assertEqual("".join(lines).replace(" ", ""), text.replace(" ", ""))
        git(self.repo, "branch", "fix/login")
        cwd, branches = self.setup_branches()
        for size in ((16, 50), (22, 100), (40, 140)):
            screen = FakeScreen(typing("login") + ["\t", "q"] + LEAVE, size)
            picker = self.picker(screen)
            picker.branches = lambda: branches
            with patch.object(worktrees, "setup_command", return_value=setup):
                drive(screen, lambda: picker.new_screen(root=True))
            details = next(screen.text(i) for i in range(len(screen.frames))
                           if any(line.startswith("New worktree — details") for line in screen.text(i)[:2]))
            # The command, after its label, and the rows under it, in the label's column.
            column = screen_new.PLAN_COLUMN
            start = next(i for i, line in enumerate(details) if line.startswith("Then runs"))
            lines = [details[start][column:].rstrip()]
            for line in details[start + 1:-1]:
                if not line.strip() or not line.startswith(" " * column):
                    break
                lines.append(line[column:].rstrip())
            sentence = setup
            with self.subTest(size=size):
                self.assertTrue(all(line in sentence for line in lines), lines)
                self.assertEqual("".join(lines).replace(" ", ""), sentence.replace(" ", ""))
                if size[1] >= 100:
                    self.assertTrue(any('"keep  two  spaces\\n"' in line for line in lines), lines)

    def frames_with_styles(self, keys, branches, size):
        """New worktree's frames, as (text, attr) rows, with colors that tell styles apart."""
        styles = {"dim": 1 << 20, "bold": 1 << 21, "yellow": 1 << 22, "red": 1 << 23, "green": 1 << 24,
                  "cyan": 1 << 25, "blue": 1 << 26, "magenta": 1 << 27}
        screen = FakeScreen(keys, size)
        picker = self.picker(screen)
        picker.branches = lambda: branches
        with patch.dict(ui.COLORS, styles), patch.object(worktrees, "setup_command", return_value=""):
            drive(screen, lambda: picker.new_screen(root=True))
        return screen, styles

    @staticmethod
    def search_text(rows):
        """The text in a screen's filter, without the list's position beside it."""
        for prompt in ("/ ", screen_new.PROMPT):  # The list's, or New worktree's.
            line = next((line for line in rows[:3] if line.startswith(prompt)), None)
            if line is not None:
                return line[len(prompt):].split("  ")[0].rstrip()
        return ""

    def test_why_text_cant_be_a_branch_is_dim_until_its_row_is_selected(self):
        git(self.repo, "branch", "feat/login-flow")
        cwd, branches = self.setup_branches()
        # feat names no branch, but can't be one beside feat/login-flow, which typing selects.
        screen, styles = self.frames_with_styles(typing("feat") + [curses.KEY_UP, "\n"] + LEAVE, branches,
                                                 (24, 100))
        index = next(i for i in range(len(screen.frames)) if self.search_text(screen.text(i)) == "feat")
        frames = screen.frames[index:index + 3]  # The branch selected, then the create row, then enter.

        def note(frame):
            return next(seg for row in frame for seg in row if "can't be a branch while feat/login-flow" in seg[0])

        self.assertTrue(any(line.startswith("› feat/login-flow") for line in screen.text(index)))
        self.assertTrue(note(frames[0])[1] & styles["dim"] and not note(frames[0])[1] & styles["red"])
        self.assertIn(util.ICONS["info"], note(frames[0])[0])  # Not an error while enter goes elsewhere.
        self.assertIn("› + Create “feat”", screen.text(index + 1))
        self.assertTrue(note(frames[1])[1] & styles["red"] and not note(frames[1])[1] & styles["bold"])
        self.assertIn(util.ICONS["error"], note(frames[1])[0])
        self.assertNotIn("enter", self.footer_keys(frames[1]))
        self.assertTrue(note(frames[2])[1] & styles["red"] and note(frames[2])[1] & styles["bold"])  # Refused.

    def test_a_remotes_name_lists_its_branches_with_a_dim_hint(self):
        cwd, branches, shas = self.two_remotes()
        for size in ((16, 50), (22, 100), (40, 140)):
            for typed, hint in (("origin/", "origin is a remote; its branches are below."),
                                ("origin", "origin is a remote; type origin/ to list its branches.")):
                screen, styles = self.frames_with_styles(typing(typed) + ["\n"] + LEAVE, branches, size)
                index = next(i for i in range(len(screen.frames)) if self.search_text(screen.text(i)) == typed)
                rows, before, after = screen.text(index), screen.frames[index], screen.frames[index + 1]
                with self.subTest(typed=typed, size=size):
                    self.assertIn("› + Create a new branch", rows)  # Dim, no “…”: no branch to name.
                    self.assertFalse(any(util.ICONS["error"] in line for line in rows), rows)
                    start = next(i for i, line in enumerate(rows) if line.startswith("› + Create a new branch")) + 1
                    note = []
                    for line in rows[start:]:
                        if not line.startswith("  ") or not line.strip():
                            break
                        note.append(line.replace(util.ICONS["info"], "").strip())
                    self.assertEqual(" ".join(note), hint)
                    self.assertFalse(rows[-1].startswith("enter"), rows[-1])  # Enter would do nothing.
                    listed = [line for line in rows if "feature/remote-two" in line and not line.startswith("enter")]
                    if typed == "origin/":  # Only origin's, named so beside fork's.
                        self.assertEqual(len(listed), 1, rows)
                        self.assertTrue(listed[0].rstrip().endswith("origin"), listed)
                        self.assertTrue(any("remote-only" in line for line in rows), rows)
                    note = [seg for row in before for seg in row if "is a remote" in seg[0]]
                    nudged = [seg for row in after for seg in row if "is a remote" in seg[0]]
                    self.assertTrue(note and note[0][1] & styles["dim"] and not note[0][1] & styles["bold"], note)
                    # Enter, which can't go anywhere, makes the hint stand out.
                    self.assertTrue(nudged and nudged[0][1] & styles["yellow"] and nudged[0][1] & styles["bold"])
        # A red note stands out the same way.
        screen, styles = self.frames_with_styles(typing("origin/new-idea") + ["\n"] + LEAVE, branches, (22, 100))
        index = next(i for i in range(len(screen.frames)) if self.search_text(screen.text(i)) == "origin/new-idea")
        self.assertFalse(screen.text(index)[-1].startswith("enter"))
        red = [seg for row in screen.frames[index + 1] for seg in row if "can't start with origin/" in seg[0]]
        self.assertTrue(red and red[0][1] & styles["red"] and red[0][1] & styles["bold"], red)

    def test_from_column_names_every_remote_when_there_are_several(self):
        origin = {"kind": "remote", "name": "a", "ref": "origin/a"}
        fork = {"kind": "remote", "name": "a", "ref": "fork/a"}
        self.assertEqual(branching.source_label(origin, True), "origin")
        self.assertEqual(branching.source_label(fork, False), "fork")
        self.assertTrue(branching.named_remotes([origin, fork]))
        self.assertFalse(branching.named_remotes([origin, {"kind": "local", "name": "b", "ref": "b"}]))
        cwd, branches = self.setup_branches()  # Only origin: "remote", as before.
        rows = next(f for f in self.new_worktree_frames(typing("remote-only") + LEAVE, branches, (22, 100))
                    if any(line.startswith(screen_new.PROMPT + "remote-only") for line in f))
        self.assertTrue(next(line for line in rows if line.startswith("› remote-only")).rstrip().endswith("remote"))

    def test_remove_cut_tab_name_says_tab_shows_all_when_the_footer_cant(self):
        label = "review/cleanup-with-a-very-long-shared-prefix-and-context-" * 2 + "tail-17"
        entry = {"name": "fix/x", "worktree": str(self.repo), "branch": "refs/heads/fix/x"}
        closing = [{"window": {"window_id": "w2", "label": label, "session_id": "other"}, "panes": ["c"],
                    "total": 1, "whole": True, "here": False}]
        options = [{"id": "force", "checked": False, "label": [("Discard uncommitted changes", 0)]}]
        for size in ((16, 50), (22, 100), (40, 140)):
            screen = FakeScreen(["q"], size)
            picker = self.picker(screen)
            picker.session = "s"
            drive(screen, lambda: picker.removal_options(entry, ["?? a.txt"], closing, None, options))
            rows = ["".join(t for t, _ in ui.fit(row, size[1] - 3)).rstrip() for row in screen.frames[0]]
            with self.subTest(size=size):
                self.assertTrue(any(label[:8] in line and "…" in line for line in rows), rows)  # Cut.
                self.assertTrue(any("the whole tab" in line for line in rows), rows)
                if "tab details" in rows[-1]:
                    self.assertFalse(any(ui.TAB_CUE.strip() in line for line in rows), rows)
                else:
                    self.assertIn("    the whole tab" + ui.TAB_CUE, rows)
                    self.assertEqual(size, (16, 50))

    def test_plan_paths_break_after_a_slash_and_preview_labels_between_labels(self):
        lines = ["open feature/login-redirect's worktree", ("at /private/tmp/rexwt-impl-r6/implr6-login", "path")]
        self.assertEqual(screen_new.plan_lines(lines, 40, 10),
                         (["open feature/login-redirect's worktree", "at /private/tmp/rexwt-impl-r6/", "implr6-login"],
                          False))  # Not "at" alone above the path.
        self.assertEqual(screen_new.plan_lines(lines, 90, 10)[0][1], "at /private/tmp/rexwt-impl-r6/implr6-login")
        entry = {"name": "fix/x", "worktree": str(self.repo), "track": ["remote deleted", "2 ahead"], "dirty": True,
                 "open": True, "pr": {"number": 12345, "state": "MERGED", "isDraft": False}}
        picker = self.picker(None)
        picker.commits[(str(self.repo), None)] = ("Recent commits", [])
        rows = ["".join(t for t, _ in row) for row in picker.preview(entry, 47, True, 9)]
        labels = [row for row in rows if "merged" in row or "changes" in row]
        self.assertEqual(len(labels), 2, rows)  # Wrapped, not cut.
        self.assertTrue(all(len(row) <= 47 for row in rows), rows)
        self.assertIn("changes", labels[-1])

    def test_every_screen_fits_any_width_and_name(self):
        """A quick fuzz: every screen, at every width from 20 to 160, with names that are empty-ish,
        one character, very long, unicode, or full of spaces. Nothing raises, and text that wraps
        stays within its width."""
        rng = random.Random(6)
        alphabet = "abcxyz/-_.é日本 "
        names = ["", " ", "a", "é", "fix/a", "release", "a b", "  spaced  name  ", "日本語/ブランチ", "x" * 300,
                 "-" * 40, "/" * 40, "feature/" + "very-long-segment-" * 12 + "tail", "/".join(["ab"] * 40)]
        names += ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 120))) for _ in range(8)]
        widths = [20, 21, 26, 33, 40, 47, 56, 64, 77, 97, 118, 137, 160]
        rex_paths = str(Path.home() / ".local/share/rex/worktrees/repo-abc")

        def check(rows, width, what):
            for row in rows:
                text = row if isinstance(row, str) else "".join(s[0] for s in row)
                self.assertLessEqual(len(text.rstrip()), width, (what, width, text))

        def rendered(frame, width):
            """A frame's rows as render draws them."""
            return ["".join(t for t, _ in ui.fit(row, width)) for row in frame]

        def check_details(frames, width, what):
            """The body of every details page among frames: below the title, above the key hints.
            Narrower than 30 columns, headings such as UNCOMMITTED CHANGES (2) are cut to fit."""
            for frame in frames if width >= 30 else ():
                title = next((i for i, row in enumerate(frame) if row), 0)
                if "— details" in "".join(s[0] for s in frame[title]):
                    check(frame[title + 1:-1], width, what)

        for width in widths:
            for name in names:
                with self.subTest(width=width, name=name[:30]):
                    for wrap in (ui.wrap, ui.wrap_path, ui.wrap_lines):
                        check(wrap(name, width), width, wrap.__name__)
                    check(ui.hang(util.ICONS["error"], name, width), width, "hang")
                    check(ui.clip(ui.wrap(name, width), 2, width), width, "clip")
                    flash = ui.Flash()
                    flash.set("Removed " + name + ".", "live")
                    check(flash.rows(width), width, "flash")
                    flash.set(screen_remove.dirty_refusal([{"name": name}], width), "attention")
                    check(flash.rows(width), width, "dirty_refusal")
                    check(screen_new.note_rows(name, "error", "danger", width), width, "note_rows")
                    check(screen_new.error_rows(name, width, 3), width, "error_rows")
                    sentences = [("Checks out", name), ("In", rex_paths + "/" + name, "path"),
                                 ("In", rex_paths + "/" + name), ("Then runs", name, "end")]
                    for limit in (1, 3, 1000):
                        check(screen_new.plan_lines([(t, *k) if k else t for _, t, *k in sentences], width, limit,
                                                    "a branch from " + name)[0], width, "plan")
                        for cue in (False, True):
                            rows, _ = screen_new.plan_rows(sentences, width, limit, "a branch from " + name, cue)
                            check(rendered(rows, width), width, "plan_rows")
                    bases = [{"ref": "origin/" + name, "label": "origin/" + name, "note": "default branch"},
                             {"ref": "HEAD", "label": name, "note": "current branch"}]
                    tops = [{"kind": "create", "base": base} for base in bases]
                    for index in (0, 1):
                        check(rendered(screen_new.create_rows(tops, name, width, index).values(), width), width,
                              "create_rows")
                    entry = {"name": name, "worktree": rex_paths + "/" + name, "branch": "refs/heads/" + name,
                             "track": ["remote deleted", "2 ahead"], "dirty": True, "open": True, "time": 0,
                             "pr": {"number": 12345, "state": "MERGED", "isDraft": False}}
                    entries = [{"name": "main", "worktree": str(self.repo), "main": True, "track": []}, entry]
                    widths_for = ui.column_widths(entries, width)
                    rows = [ui.header_row(widths_for)] + [ui.entry_row(e, i == 1, widths_for)
                                                          for i, e in enumerate(entries)]
                    check(rendered(rows, width), width, "list")
                    picker = self.picker(None)
                    picker.commits[(entry["worktree"], None)] = ("Recent commits", [("abc1234", name, name, 0)])
                    for size in (2, 4, 9, 40):
                        rows = picker.preview(entry, width, widths_for["compact"], size)
                        self.assertLessEqual(len(rows), size)
                        check(rendered(rows, width), width, "preview")
                        check([r for r in rows if not r or r[0][0] != "abc1234"], width, "preview text")
                    check(screen_remove.closing_rows([{"window": {"window_id": "w", "label": name, "session_id": "o"},
                                                       "panes": ["a"], "total": 2, "whole": False, "here": False}],
                                                     width, "s", "repo", True), width, "closing_rows")
                    check(screen_remove.file_rows(["?? " + name, name], width, True), width, "file_rows")

        # Whole screens, driven at a few heights and every width, with the same names.
        sizes = [(h, w) for w in range(20, 161, 20) for h in (8, 16, 40)] + [(16, 50), (22, 100), (40, 140)]
        picks = names[:8] + names[-3:]
        branches = {"local": {"existing"}, "remote": {}, "remotes": ["origin", "fork"], "checked_out": {},
                    "missing": set(), "head": "main", "bases": [{"ref": "main", "label": "main"}],
                    "suggestions": [{"name": n, "ref": n, "kind": "local", "time": 0} for n in picks]
                    + [{"name": n, "ref": r + "/" + n, "kind": "remote", "time": 0} for n in picks[:3]
                       for r in ("origin", "fork")]}
        for n in picks[:3]:
            branches["remote"][n] = ["origin/" + n, "fork/" + n]
        with patch.object(util, "git_ok", return_value=True), \
                patch.object(branching, "worktree_root_key", return_value="repo-abc"), \
                patch.object(worktrees, "missing", side_effect=lambda e: "prunable" in e):
            for i, (height, width) in enumerate(sizes):
                name = picks[i % len(picks)]
                with self.subTest(screen="new", size=(height, width), name=name[:30]):
                    # Both modes: it opens in normal mode, then i types, and esc stops typing.
                    screen = FakeScreen([curses.KEY_DOWN, "\t", "q", "5", "j", "H", "L", "M", "i", curses.KEY_UP,
                                         "\t", "q", "\x1b", "g", "g", "\n"] + ["\x1b"] * 4, (height, width))
                    picker = self.picker(screen)
                    picker.branches = lambda: branches
                    picker.create_and_open = lambda cwd, plan: None  # Rendering only: nothing is created.
                    picker.open_checked_out = lambda plan, flash: False
                    with patch.object(worktrees, "setup_command", return_value="printf '" + name + "'"):
                        drive(screen, lambda: picker.new_screen(root=True, text=name))
                    self.assertTrue(all(len(frame) <= height for frame in screen.frames))
                    check_details(screen.frames, width - 3, "new details")
                with self.subTest(screen="remove", size=(height, width), name=name[:30]):
                    entry = {"name": name, "worktree": "/w/" + name, "branch": "refs/heads/" + name}
                    closing = [{"window": {"window_id": "w", "label": name, "session_id": "o"}, "panes": ["a"],
                                "total": 3, "whole": False, "here": True},
                               {"window": {"window_id": "v", "label": name[::-1], "session_id": "o"}, "panes": ["b"],
                                "total": 1, "whole": True, "here": False}]
                    files = ["?? " + name, " M " + name[::-1]] + [f"?? f{j}" for j in range(i % 20)]
                    options = [{"id": "force", "checked": False, "label": [("Discard uncommitted changes", 0)]},
                               {"id": "delete_branch", "checked": False, "label": [("Also delete the branch", 0)]}]
                    screen = FakeScreen(["j", " ", "\n", "\t", "q", "q"], (height, width))
                    picker = self.picker(screen)
                    picker.session = "s"
                    drive(screen, lambda: picker.removal_options(entry, files, closing, "main", options))
                    self.assertTrue(all(len(frame) <= height for frame in screen.frames))
                    check_details(screen.frames, width - 3, "remove details")
                with self.subTest(screen="cleanup", size=(height, width), name=name[:30]):
                    # Finished ones, some with changes, and missing ones, which cleanup prunes.
                    candidates = [{"name": n, "worktree": "/w/" + str(j), "track": ["remote deleted"],
                                   "dirty": j % 3 == 0, "open": False, **({"prunable": "gone", "dirty": None}
                                                                          if j % 4 == 2 or i % 7 == 3 else {})}
                                  for j, n in enumerate(picks[:i % 6 + 1])]
                    screen = FakeScreen(["j", " ", "G", " ", "2", "k", "L", "M", "H", "\t", "q", "q"],
                                        (height, width))
                    picker = self.picker(screen, candidates)
                    picker.session, picker.terminals = "s", []
                    drive(screen, picker.cleanup)
                    if picker.flash.text:  # Every candidate dirty: a message instead.
                        check(picker.flash.rows(width - 3), width - 3, "cleanup refusal")
                    self.assertTrue(all(len(frame) <= height for frame in screen.frames))
                    check_details(screen.frames, width - 3, "cleanup details")
                with self.subTest(screen="prune", size=(height, width), name=name[:30]):
                    stale = {"name": name, "worktree": "/private/tmp/" + name + "/" + name[::-1], "prunable": "gone",
                             **({"branch": "refs/heads/" + name} if i % 2 else {})}
                    screen = FakeScreen(["\t", "q", "?", "q", "q"], (height, width))
                    drive(screen, lambda: self.picker(screen, [stale]).remove(stale))
                    self.assertTrue(all(len(frame) <= height for frame in screen.frames))
                    check_details(screen.frames, width - 3, "prune details")
                with self.subTest(screen="list", size=(height, width), name=name[:30]):
                    entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False,
                                "open": False, "track": []}]
                    entries += [{"name": n, "worktree": "/w/" + str(j), "track": ["remote deleted"], "dirty": True,
                                 "open": j % 2 == 0, "time": 0} for j, n in enumerate(picks[:i % 9 + 1])]
                    screen = FakeScreen(["j", "G", "3", "k", "L", "M", "H", "c", "/", *name[:3], "\x1b", "?", "q", "q"],
                                        (height, width))
                    picker = self.picker(screen, entries)
                    picker.terminals = []
                    for e in entries:
                        picker.commits[(e["worktree"], None)] = ("Recent commits", [("abc1234", name, name, 0)])
                    drive(screen, picker.list_screen)
                    self.assertTrue(all(len(frame) <= height for frame in screen.frames))

    def commit_at(self, message, date):
        """A commit on top of HEAD, made at date, on no branch. Returns its hash."""
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_NAME": "T",
               "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}
        tree = git(self.repo, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
        return subprocess.run(["git", "-C", str(self.repo), "commit-tree", tree, "-p", "HEAD", "-m", message],
                              check=True, stdout=subprocess.PIPE, env=env).stdout.decode().strip()

    def test_a_name_typed_in_full_beats_longer_names_that_start_with_it(self):
        self.add("fixa", "fix/a")
        gone = self.add("gone", "chore/gone")
        shutil.rmtree(gone)
        older, newer = self.commit_at("older", "2026-01-01T00:00:00"), self.commit_at("newer", "2026-02-01T00:00:00")
        for branch, sha in (("fix/a-followup", newer), ("maintenance/cleanup", newer), ("chore/gone-2", newer),
                            ("feature/login-redirect", older), ("feature/login-redirect-v2", newer)):
            git(self.repo, "branch", branch, sha)
        cwd, branches = self.setup_branches()
        git(self.repo, "update-ref", "refs/remotes/origin/feature/x", older)
        git(self.repo, "update-ref", "refs/remotes/origin/feature/x-2", newer)
        branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
        names = [s["name"] for s in branches["suggestions"]]
        # Newer first, so the longer names would come first by their order alone.
        self.assertLess(names.index("feature/login-redirect-v2"), names.index("feature/login-redirect"))
        self.assertLess(names.index("feature/x-2"), names.index("feature/x"))
        cases = [("fix/a", "› Open the worktree for “fix/a”", "enter open"),
                 ("main", "› Open the worktree for “main”", "enter open"),
                 ("chore/gone", "› + Create a new branch", "↑/↓ move"),  # Its folder is missing: no enter.
                 ("feature/login-redirect", "› feature/login-redirect ", "enter create"),
                 ("feature/x", "› feature/x ", "enter create"),
                 ("origin/feature/x", "› feature/x ", "enter create")]
        for typed, selected, footer in cases:
            for size in ((16, 50), (22, 100), (40, 140)):
                frames = self.new_worktree_frames(typing(typed) + LEAVE, branches, size)
                rows = next(f for f in frames if self.search_text(f) == typed)
                with self.subTest(typed=typed, size=size):
                    self.assertTrue(any(line.startswith(selected) for line in rows), rows)
                    self.assertTrue(rows[-1].startswith(footer), rows[-1])
                    listed = [line for line in rows if line[:2] in ("  ", "› ") and line[2:].startswith("feature/")]
                    if selected.startswith("› feature"):  # The whole name goes first, above longer ones.
                        self.assertTrue(listed[0].startswith(selected), listed)
        # Enter opens fix/a's worktree; it doesn't check out fix/a-followup.
        screen = FakeScreen(typing("fix/a") + ["\n"], (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        opened = []
        picker.open_checked_out = lambda plan, flash: opened.append(plan["branch"]) or True
        picker.create_and_open = lambda *args: self.fail("enter checked out another branch")
        with patch.object(worktrees, "setup_command", return_value=""):
            self.assertTrue(drive(screen, lambda: picker.new_screen()))
        self.assertEqual(opened, ["fix/a"])
        # Typing on past the checked-out name still finds the longer one.
        rows = next(f for f in self.new_worktree_frames(typing("fix/a-f") + LEAVE, branches)
                    if self.search_text(f) == "fix/a-f")
        self.assertTrue(any(line.startswith("› fix/a-followup") for line in rows), rows)

    def test_a_remote_qualified_name_says_when_the_local_branch_differs(self):
        cwd, branches, shas = self.two_remotes()
        git(self.repo, "branch", "--track", "feature/remote-two", "origin/feature/remote-two")
        branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
        plan = branching.plan_branch(cwd, branches, "fork/feature/remote-two")
        self.assertEqual(plan["tracks"], ("origin", "fork"))
        self.assertNotIn("tracks", branching.plan_branch(cwd, branches, "origin/feature/remote-two"))
        rows = next(f for f in self.new_worktree_frames(typing("fork/feature/remote-two") + LEAVE, branches,
                                                         (22, 100)) if self.search_text(f) == "fork/feature/remote-two")
        self.assertTrue(any(line.startswith("› feature/remote-two") for line in rows), rows)
        self.assertIn("fork's version differs; enter checks out the local branch, which tracks origin.",
                      " ".join(line.strip() for line in rows))
        # Checked out, the first row opens it, and says the same.
        self.add("two", "feature/remote-two-other")
        git(self.repo, "worktree", "add", "-q", str(Path(self.tmp.name) / "remote-two"), "feature/remote-two")
        branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
        rows = next(f for f in self.new_worktree_frames(typing("fork/feature/remote-two") + LEAVE, branches,
                                                         (16, 50)) if self.search_text(f) == "fork/feature/remote-two")
        self.assertTrue(any(line.startswith("› Open the worktree for") for line in rows), rows)
        self.assertIn("fork's version differs; the local branch tracks origin.", " ".join(line.strip() for line in rows))
        # At fork's commit, or with no upstream at another, it says so only where they differ.
        git(self.repo, "update-ref", "refs/remotes/fork/feature/remote-two", shas["origin"])
        self.assertNotIn("tracks", branching.plan_branch(cwd, branches, "fork/feature/remote-two"))
        git(self.repo, "update-ref", "refs/remotes/fork/feature/remote-two", shas["fork"])
        git(self.repo, "branch", "--unset-upstream", "feature/remote-two")
        self.assertEqual(branching.plan_branch(cwd, branches, "fork/feature/remote-two")["tracks"], ("", "fork"))

    def test_remote_qualified_text_that_cant_be_created_names_no_branch(self):
        cwd, branches = self.setup_branches()
        for typed in ("origin/remote-only", "origin/new-idea"):
            frames = self.new_worktree_frames(typing(typed) + [curses.KEY_HOME] + LEAVE, branches, (22, 100))
            rows = [f for f in frames if self.search_text(f) == typed][-1]  # Home: the first row.
            self.assertIn("› + Create a new branch", rows, typed)
            self.assertFalse(any(f"“{typed}”" in line for line in rows), rows)

    def test_pull_requests_show_their_number_before_rows_turn_compact(self):
        cwd, branches, shas = self.two_remotes()
        screen = FakeScreen(typing("feature/remote-two") + LEAVE, (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        picker.prs = {"feature/remote-two": {"number": 12, "state": "MERGED", "isDraft": False}}
        with patch.object(worktrees, "setup_command", return_value=""):
            drive(screen, lambda: picker.new_screen(root=True))
        rows = next(screen.text(i) for i in range(len(screen.frames)) if self.search_text(screen.text(i)))
        listed = [line.rstrip() for line in rows if "feature/remote-two" in line and "#12" in line]
        self.assertEqual(len(listed), 2, rows)
        self.assertTrue(listed[0].endswith("fork") and listed[1].endswith("origin"), listed)
        self.assertFalse(any("merged" in line for line in listed), listed)

    def test_list_filter_with_no_matches_offers_no_move(self):
        entries = self.list_entries(["fix/mm"])
        screen = FakeScreen(["/", "m", "z", "\x1b", "\x1b", "q"], (24, 80))
        drive(screen, self.picker(screen, entries).list_screen)
        texts = [screen.text(i) for i in range(len(screen.frames))]
        self.assertIn("↑/↓ move", next(t for t in texts if self.search_text(t) == "m")[-1])
        self.assertNotIn("move", next(t for t in texts if self.search_text(t) == "mz")[-1])  # Nothing to move to.


    def test_cleanup_says_tab_shows_all_when_the_footer_cant(self):
        names = ["fix/a", "fix/flaky-checkout-tests", "review/cleanup-with-a-very-long-name-for-cutting"]
        entries = [{"name": n, "worktree": f"/w/{i}", "track": ["remote deleted"], "dirty": i == 0}
                   for i, n in enumerate(names)]
        for size, cue in (((16, 50), True), ((12, 50), True), ((22, 100), False)):
            screen = FakeScreen(["j", "j", "q"], size)
            picker = self.picker(screen, entries)
            picker.session, picker.terminals = "s", []
            with patch.object(worktrees, "missing", return_value=False):
                drive(screen, picker.cleanup)
            for i, frame in enumerate(screen.frames):
                rows = ["".join(t for t, _ in ui.fit(row, size[1] - 3)) for row in frame]  # As drawn.
                with self.subTest(size=size, frame=i):
                    self.assertEqual(ui.TAB_CUE.strip() in [row.strip() for row in rows], cue, rows)
                    self.assertNotIn("tab details", rows[-1])  # No room at 50 columns; nothing's cut at 100.
                    self.assertTrue(all(any(name[:10] in line for line in rows) for name in names), rows)

    def test_a_checked_out_name_typed_loosely_opens_its_worktree(self):
        self.add("fixa", "fix/a")
        self.add("login", "feature/login-redirect")
        gone = self.add("gone", "chore/gone")
        shutil.rmtree(gone)
        newer = self.commit_at("newer", "2026-02-01T00:00:00")
        for branch in ("fix/a-followup", "maintenance/cleanup", "feature/login-redirect-v2", "chore/gone-2"):
            git(self.repo, "branch", branch, newer)
        cwd, branches = self.setup_branches()
        cases = [("fix a", "fix/a"), ("FIX/A", "fix/a"), ("Fix_A", "fix/a"), ("MAIN", "main"),
                 ("feature login-redirect", "feature/login-redirect")]
        for typed, branch in cases:
            plan = branching.plan_branch(cwd, branches, typed)
            self.assertEqual((plan["branch"], plan.get("open") is not None), (branch, True), typed)
            for size in ((16, 50), (22, 100), (40, 140)):
                rows = next(f for f in self.new_worktree_frames(typing(typed) + LEAVE, branches, size)
                            if self.search_text(f) == typed)
                with self.subTest(typed=typed, size=size):
                    # The only row selected, not a longer name that starts so. A narrow pane cuts the
                    # name's middle, and the plan names it in full.
                    selected = [line for line in rows if line.startswith("› ")]
                    self.assertEqual(len(selected), 1, rows)
                    self.assertTrue(selected[0].startswith("› Open the worktree for “"), rows)
                    shown = selected[0][len("› Open the worktree for “"):-1]
                    self.assertTrue(shown == branch or (size[1] == 50 and branch.endswith(shown.split("…")[-1])), rows)
                    self.assertIn(branch, " ".join(line.strip() for line in rows[self.plan_start(rows):]))
                    self.assertTrue(rows[-1].startswith("enter open"), rows[-1])
        # Its folder missing, it's refused just as the exact name is, naming no branch to create.
        plan = branching.plan_branch(cwd, branches, "CHORE/GONE")
        self.assertEqual((plan["branch"], "missing" in plan), ("chore/gone", True))
        rows = next(f for f in self.new_worktree_frames(typing("CHORE/GONE") + LEAVE, branches)
                    if self.search_text(f) == "CHORE/GONE")
        self.assertIn("› + Create a new branch", rows)
        self.assertTrue(rows[-1].startswith("↑/↓ move"), rows[-1])
        # Enter on fix a opens fix/a; it doesn't check out fix/a-followup.
        screen = FakeScreen(typing("fix a") + ["\n"], (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        opened = []
        picker.open_checked_out = lambda plan, flash: opened.append(plan["branch"]) or True
        picker.create_and_open = lambda *args: self.fail("enter checked out another branch")
        with patch.object(worktrees, "setup_command", return_value=""):
            self.assertTrue(drive(screen, lambda: picker.new_screen()))
        self.assertEqual(opened, ["fix/a"])
        # A listed branch just as close wins: it's the one the list selects.
        git(self.repo, "branch", "fix-a", newer)
        branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
        self.assertNotIn("open", branching.plan_branch(cwd, branches, "fix a"))
        rows = next(f for f in self.new_worktree_frames(typing("fix a") + LEAVE, branches)
                    if self.search_text(f) == "fix a")
        self.assertTrue(any(line.startswith("› fix-a ") for line in rows), rows)

    def test_new_worktree_columns_stay_put_while_the_list_scrolls(self):
        long_name = "experiment/very-long-branch-name-to-test-trunc"
        names = [f"feature/topic-{i:02}" for i in range(24)] + [long_name]
        for name in names:
            git(self.repo, "branch", name)
        cwd, branches = self.setup_branches()
        prs = {names[3]: {"number": 105, "state": "MERGED", "isDraft": False},
               names[20]: {"number": 7, "state": "OPEN", "isDraft": False}}
        for size, compact in (((16, 50), False), ((22, 100), False), ((40, 140), False), ((16, 40), True)):
            screen = FakeScreen([curses.KEY_DOWN] * 30 + [curses.KEY_UP] * 30 + ["\x1b"], size)
            picker = self.picker(screen)
            picker.branches = lambda: branches
            picker.prs = prs
            with patch.object(worktrees, "setup_command", return_value=""):
                drive(screen, lambda: picker.new_screen(root=True))
            titles, sources = set(), set()
            for i in range(len(screen.frames)):
                rows = screen.text(i)
                title = next(line for line in rows if line.startswith("  BRANCH"))
                titles.add(title.rstrip())
                sources.update(line.index("local") for line in rows
                               if line[:2] in ("  ", "› ") and line.rstrip().endswith("local"))
            with self.subTest(size=size):
                self.assertEqual(len(titles), 1, titles)  # The same titles, in the same places, all along.
                self.assertEqual(("FROM" in titles.pop()), not compact)
                self.assertLessEqual(len(sources), 1, sources)

    def test_long_remote_names_keep_the_source_column_in_a_narrow_pane(self):
        cwd, branches = self.setup_branches()
        long_remote = "upstream-with-a-descriptive-name"
        git(self.repo, "remote", "add", long_remote, "https://example.invalid/up.git")
        git(self.repo, "update-ref", f"refs/remotes/{long_remote}/only-upstream", "HEAD")
        branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
        screen = FakeScreen(["\x1b"], (16, 50))
        picker = self.picker(screen)
        picker.branches = lambda: branches
        with patch.object(worktrees, "setup_command", return_value=""):
            drive(screen, lambda: picker.new_screen(root=True))
        rows = screen.text(0)
        title = next(line for line in rows if line.startswith("  BRANCH"))
        self.assertIn("FROM", title)  # Not compact: the long name is cut instead.
        listed = next(line for line in rows if "only-upstream" in line)
        self.assertIn(util.ICONS["remote"] + " upstrea", listed)
        self.assertTrue(listed.rstrip().endswith("…"), listed)
        # Every row fits, the footer once its keys drop by rank, as render draws it.
        self.assertTrue(all(len(line) <= 47 for line in rows[:-1]), rows)
        self.assertLessEqual(len("".join(t for t, _ in ui.fit(screen.frames[0][-1], 47)).rstrip()), 47)

    def test_a_long_remote_names_note_leads_with_the_difference_and_tab_shows_it(self):
        cwd, branches = self.setup_branches()
        upstream, fork = "upstream-with-a-descriptive-name", "fork-with-a-descriptive-name"
        for remote in (upstream, fork):
            git(self.repo, "remote", "add", remote, f"https://example.invalid/{remote}.git")
        git(self.repo, "update-ref", f"refs/remotes/{upstream}/note-test", "HEAD")
        git(self.repo, "update-ref", f"refs/remotes/{fork}/note-test", self.commit_at("fork", "2026-02-01T00:00:00"))
        git(self.repo, "branch", "--track", "note-test", f"{upstream}/note-test")
        typed = f"{fork}/note-test"
        full = f"{fork}'s version differs; enter checks out the local branch, which tracks {upstream}."

        def check(branches, full):
            screen = FakeScreen(typing(typed) + ["\t", "q"] + LEAVE, (16, 50))
            picker = self.picker(screen)
            picker.branches = lambda: branches
            with patch.object(worktrees, "setup_command", return_value=""):
                drive(screen, lambda: picker.new_screen(root=True))
            frames = [screen.text(i) for i in range(len(screen.frames))]
            rows = next(f for f in frames if self.search_text(f) == typed)
            note = next(i for i, line in enumerate(rows) if util.ICONS["info"] in line)
            self.assertIn(f"{fork}'s version", rows[note])  # The difference first.
            self.assertTrue(rows[note + 1].rstrip().endswith("…") or "(tab shows all)" in rows[note + 1], rows)
            self.assertTrue("tab details" in rows[-1] or "(tab shows all)" in " ".join(rows), rows)
            details = next(f for f in frames if f[0].startswith("New worktree — details"))
            self.assertIn(full, " ".join(line.strip().lstrip(util.ICONS["info"]).strip() for line in details), details)

        check(branching.load_branches(cwd, worktrees.worktrees(cwd)), full)
        # Checked out, the first row opens it, and says the same, with tab though the plan fits.
        git(self.repo, "worktree", "add", "-q", str(Path(self.tmp.name) / "note"), "note-test")
        check(branching.load_branches(cwd, worktrees.worktrees(cwd)),
              f"{fork}'s version differs; the local branch tracks {upstream}.")

    def test_same_name_remote_branches_prefer_origin_or_the_default_remote(self):
        cwd, branches, shas = self.two_remotes()
        self.assertEqual(branches["remote"]["feature/remote-two"],
                         ["origin/feature/remote-two", "fork/feature/remote-two"])
        for preferred in ("origin", "fork"):
            if preferred == "fork":
                git(self.repo, "config", "checkout.defaultRemote", "fork")
                branches = branching.load_branches(cwd, worktrees.worktrees(cwd))
            rows = next(f for f in self.new_worktree_frames(typing("feature/remote-two") + LEAVE, branches)
                        if self.search_text(f) == "feature/remote-two")
            selected = next(line for line in rows if line.startswith("› feature/remote-two"))
            self.assertTrue(selected.rstrip().endswith(preferred), rows)
            self.assertIn(f"tracking {preferred}/feature/remote-two", " ".join(line.strip() for line in rows))

    def test_new_worktree_help_splits_what_typing_does(self):
        screen = FakeScreen(["?", "q", "\x1b"], (40, 100))
        picker = self.picker(screen)
        picker.branches = lambda: {"suggestions": [], "checked_out": {}, "local": set(), "remote": {},
                                   "remotes": [], "head": "main", "bases": []}
        with patch.object(worktrees, "setup_command", return_value=""):
            drive(screen, lambda: picker.new_screen(root=True))
        rows = next(screen.text(i) for i in range(len(screen.frames)) if "keys" in screen.text(i)[1])
        typing = next(i for i, line in enumerate(rows) if line.lstrip().startswith("typing"))
        self.assertTrue(rows[typing].rstrip().endswith("filter the existing branches, or name a new branch"), rows)
        self.assertTrue(any(line.lstrip().startswith("origin/name") for line in rows), rows)
        self.assertTrue(any(line.split() == ["whole", "name", "wins", "over", "longer", "names", "that", "start", "with",
                                             "it"] for line in rows), rows)

    def test_cleanup_footer_leaves_out_enter_with_nothing_checked(self):
        entries = [{"name": "fix/done", "worktree": "/w/done", "track": ["remote deleted"], "dirty": False}]
        for size in ((16, 50), (22, 100)):
            screen = FakeScreen([" ", "\n", " ", "q"], size)
            picker = self.picker(screen, entries)
            picker.session, picker.terminals = "s", []
            with patch.object(worktrees, "missing", return_value=False):
                drive(screen, picker.cleanup)
            footers = [screen.text(i)[-1] for i in range(len(screen.frames))]
            with self.subTest(size=size):
                self.assertIn("enter remove 1", footers[0])
                self.assertNotIn("enter", footers[1])  # Unchecked.
                self.assertIn("Nothing is checked", " ".join(screen.text(2)))  # Enter still says why.
                self.assertNotIn("enter", footers[2])
                self.assertIn("enter remove 1", footers[3])

    def test_list_with_no_matches_offers_new_once(self):
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False, "track": []}]
        screen = FakeScreen(["/"] + list("zzz") + ["\x1b", "q"], (24, 100))
        drive(screen, self.picker(screen, entries).list_screen)
        footer = screen.text()[-1]
        self.assertEqual(footer.count("new"), 1, footer)
        self.assertIn("n new", footer)
        typing = next(screen.text(i) for i in range(len(screen.frames)) if self.search_text(screen.text(i)) == "zzz")
        self.assertEqual(typing[-1].count("new"), 1, typing[-1])

    def list_entries(self, names, **state):
        """Worktrees for the list: the main checkout, then one per name, newest first."""
        entries = [{"name": "main", "worktree": str(self.repo), "main": True, "dirty": False, "open": False,
                    "track": [], "time": 1}]
        return entries + [{"name": n, "worktree": "/w/" + str(i), "dirty": False, "open": False, "track": [],
                           "time": 1000 - i, **state} for i, n in enumerate(names)]

    def run_list(self, keys, entries, size=(24, 100)):
        screen = FakeScreen(keys, size)
        picker = self.picker(screen, entries)
        picker.terminals = []
        for e in entries:
            picker.commits[(e["worktree"], None)] = ("Recent commits", [])
        with patch.object(worktrees, "missing", return_value=False):
            drive(screen, picker.list_screen)
        return picker, [screen.text(i) for i in range(len(screen.frames))]

    def test_fuzzy_letters_follow_each_other_or_start_words(self):
        self.assertEqual(matching.fuzzy("flf", "feat/login-flow")[1], [0, 5, 11])
        self.assertEqual(matching.fuzzy("logfl", "feat/login-flow")[1], [5, 6, 7, 11, 12])
        self.assertIsNone(matching.fuzzy("dark", "dependabot/npm_and_yarn/webpack-5.94.0"))
        self.assertIsNone(matching.fuzzy("ef", "feat/login-flow"))  # e is inside a word, and no gap ends at f.
        self.assertEqual(matching.fuzzy("ogin", "feat/login-flow")[1], [6, 7, 8, 9])  # A substring always matches.
        self.assertEqual(matching.fuzzy("flb", "fixLoginBug")[1], [0, 3, 8])  # Capitals start words too.
        self.assertIsNone(matching.fuzzy("mn", "main"))
        entries = [{"name": n, "worktree": "/w/" + str(i)} for i, n in enumerate(
            ["dependabot/npm_and_yarn/webpack-5.94.0", "feat/dark-mode", "feat/login-flow"])]
        self.assertEqual([e["name"] for e in matching.filter_entries(entries, "dark")], ["feat/dark-mode"])
        self.assertEqual([e["name"] for e in matching.filter_entries(entries, "flf")], ["feat/login-flow"])

    def test_long_branches_lose_their_middle_and_keep_the_columns_close(self):
        long_name = "feature/payments/refactor-checkout-flow-to-support-multi-currency-invoices"
        entries = self.list_entries(["fix/a", long_name], dirty=True)
        widths = ui.column_widths(entries, 117)
        self.assertEqual(widths["BRANCH"], ui.MAX_BRANCH)
        row = "".join(t for t, _ in ui.entry_row(entries[2], False, widths))
        shown = ui.middle(long_name, ui.MAX_BRANCH)[0]
        self.assertTrue(row[4:].startswith(shown + "  ") and shown.endswith("multi-currency-invoices"), row)
        self.assertIn("…", shown)
        # Filter hits move with the cut, and only the ones still showing are kept.
        hits = matching.match_entry(entries[2], "invoices")[1]
        with patch.dict(ui.COLORS, {"bold": 1 << 21, "underline": 1 << 22}):
            segments = ui.entry_row(entries[2], False, widths, (0, hits, set()))
        self.assertEqual("".join(t for t, a in segments if a == 3 << 21), "invoices")
        # The preview names it in full.
        _, frames = self.run_list(["j", "j", "q"], entries, (24, 120))
        self.assertTrue(any(long_name in line for line in frames[-1]), frames[-1])

    def test_list_counts_matches_beside_the_filter(self):
        entries = self.list_entries(["fix/login", "feat/login-flow", "docs/readme"])
        _, frames = self.run_list(["/"] + list("login") + ["\x1b", "q"], entries)
        typed = next(f for f in frames if self.search_text(f) == "login")
        filter_row = next(line for line in typed[:3] if line.startswith("/ login"))
        self.assertEqual(filter_row.rstrip(), "/ login  2 of 4")  # Right after the text, not at the far right.
        self.assertFalse(any("match" in line for line in typed[-3:]), typed[-3:])  # Not on the status line.
        # While the list scrolls, the selected row's place goes at the right, apart from the count.
        many = self.list_entries([f"fix/login-{i}" for i in range(20)])
        _, frames = self.run_list(["/"] + list("login") + ["\x1b", "q"], many, (16, 100))
        row = next(line for f in frames for line in f[:3] if line.startswith("/ login"))
        self.assertTrue(row.startswith("/ login  20 of 21 ") and row.rstrip().endswith(" 1/20"), row)
        # With nothing matching it says so too, and in a narrow pane the text keeps its room.
        _, frames = self.run_list(["/"] + list("zzz") + ["\x1b", "q"], entries, (16, 50))
        self.assertTrue(any(line.rstrip().endswith("0 of 4") for f in frames for line in f[:3]))
        _, frames = self.run_list(["/"] + list("a-long-filter-text") + ["\x1b", "q"], entries, (16, 22))
        rows = [line for f in frames for line in f[:3] if line.startswith("/ ")]
        self.assertTrue(rows and all(len(line.rstrip()) <= 19 for line in rows), rows)

    def test_list_esc_stops_typing_then_clears_then_quits(self):
        entries = self.list_entries(["fix/login", "docs/readme"])
        picker, frames = self.run_list(["/"] + list("login") + ["\x1b", "\x1b", "\x1b", "q"], entries)
        typing, done, cleared = frames[6], frames[7], frames[8]
        self.assertIn("esc done", typing[-1])
        self.assertNotIn("tab", typing[-1])
        self.assertEqual(self.search_text(done), "login")  # Kept, with the list's keys back.
        self.assertIn("esc clear filter", done[-1])
        self.assertIn("› ", "".join(line for line in done if "fix/login" in line))
        self.assertIn("/ to filter", cleared)
        self.assertEqual(len(frames), 9)  # The third esc quit; the q was never read.
        # tab doesn't stop typing.
        _, frames = self.run_list(["/"] + list("log") + ["\t", "x", "\x1b", "\x1b", "\x1b"], entries)
        self.assertTrue(any(self.search_text(f) == "logx" for f in frames))

    @staticmethod
    def list_rows(frame):
        """The worktrees a list frame shows, and which of them is selected."""
        names = [line[4:].split()[0] for line in frame
                 if line[:4] in ("    ", "›   ", "  ● ", "› ● ") and line[4:].startswith(("main", "fix/"))]
        selected = next((line[4:].split()[0] for line in frame if line.startswith("› ")), None)
        return names, selected

    def test_list_takes_counts_and_the_rows_in_view(self):
        entries = self.list_entries([f"fix/{i:02}" for i in range(30)])  # main, then fix/00 to fix/29.
        keys = ["5", "j", "3", "k", "1", "2", "G", "H", "L", "M", "2", "\x1b", "j", "5", "g", "g", "G", "q"]
        _, frames = self.run_list(keys, entries)
        rows = [self.list_rows(f) for f in frames]
        self.assertIn("5… then j or k to move 5 rows, or G for row 5", frames[1])
        self.assertEqual(rows[2][1], "fix/04")  # 5j: five rows down from main.
        self.assertEqual(rows[4][1], "fix/01")  # 3k.
        self.assertEqual(rows[7][1], "fix/10")  # 12G: row 12.
        shown = rows[8][0]
        self.assertGreater(len(shown), 3)
        self.assertEqual([rows[i][1] for i in (8, 9, 10)], [shown[0], shown[-1], shown[(len(shown) - 1) // 2]])
        middle = rows[10][1]
        # esc cancels the count and does nothing else: the list stays, and j moves one row.
        self.assertEqual(rows[12][1], middle)
        self.assertNotIn("2…", " ".join(frames[12]))
        self.assertEqual(entries[[e["name"] for e in entries].index(middle) + 1]["name"], rows[13][1])
        self.assertEqual(rows[16][1], "fix/03")  # 5gg: row 5, counting main.
        self.assertEqual(rows[17][1], "fix/29")
        self.assertEqual(len(frames), 18)  # The q quit.

    def test_list_count_before_dd_removes_just_the_selected_worktree(self):
        entries = self.list_entries(["fix/a", "fix/b", "fix/c"])
        for keys, removed in ((["j", "2", "d", "d", "q"], ["fix/a"]), (["j", "2", "d", "\x1b", "d", "\x1b", "q"], []),
                              (["3", "\x1b", "d", "d", "q"], [])):
            screen = FakeScreen(keys, (24, 100))
            picker = self.picker(screen, [dict(e) for e in entries])
            picker.terminals, gone = [], []
            picker.remove = lambda entry: gone.append(entry["name"]) or False
            with patch.object(worktrees, "missing", return_value=False):
                drive(screen, picker.list_screen)
            with self.subTest(keys=keys):
                self.assertEqual(gone, removed)  # Not 2: the count was dropped. Nor main, which dd refuses.

    def test_list_i_starts_the_filter_as_slash_does(self):
        entries = self.list_entries(["fix/05", "fix/50"])
        _, frames = self.run_list(["i", "0", "5", "\x1b", "q"], entries)
        self.assertEqual(self.search_text(frames[3]), "05")  # Digits are text while typing, and so is i after.
        self.assertIn("/ filter", frames[0][-1])  # The footer names /, the filter's prompt.
        self.assertNotIn("i ", frames[0][-1].replace("ii", ""))

    def test_new_worktree_and_cleanup_take_counts_and_the_rows_in_view(self):
        names = [f"feature/topic-{i:02}" for i in range(24)]
        for name in names:
            git(self.repo, "branch", name)
        cwd, branches = self.setup_branches()
        keys = ["5", "j", "L", "H", "M", "G", "H", "M", "2", "\x1b", "k", "3", "g", "g", "x", "q"]
        frames = self.new_worktree_frames(keys, branches, (22, 100))

        def rows(frame):
            """The rows in view, by name, the one asking for a name first, as "+", and the selected one's."""
            known = [s["name"] for s in branches["suggestions"]]
            listed = ["+" if "Press i" in line else line[2:].split(" ")[0] for line in frame
                      if line.startswith(("› ", "  ")) and ("Press i" in line or line[2:].split(" ")[0] in known)]
            selected = next(line for line in frame if line.startswith("› "))
            return listed, "+" if "Press i" in selected else selected[2:].split(" ")[0]

        def middle(listed):
            return listed[(len(listed) - 1) // 2]

        # H, M, and L choose among the rows on screen as the key is pressed: frame i shows what the
        # key read after frame i - 1 did. Selecting a branch shows its plan, which shortens the
        # list, so the frame after can show fewer rows, though always the one chosen.
        self.assertIn("5… then j or k to move 5 rows, or G for row 5", frames[1])
        listed, at = rows(frames[2])
        self.assertEqual(listed.index(at), 5)  # 5j: five rows down from the one asking for a name.
        self.assertEqual(rows(frames[3])[1], rows(frames[2])[0][-1])  # L: the bottom row in view.
        self.assertEqual(rows(frames[4])[1], "+")  # H: the top, the row asking for a name.
        self.assertEqual(rows(frames[5])[1], middle(rows(frames[4])[0]))  # M.
        self.assertEqual(rows(frames[6])[1], [s["name"] for s in branches["suggestions"]][-1])  # G: the last.
        self.assertEqual(rows(frames[7])[1], "+")  # H, scrolled: the row asking for a name is still the top.
        at = rows(frames[8])[1]
        self.assertEqual(at, middle(rows(frames[7])[0]))  # M, among the rows scrolled to.
        self.assertEqual(rows(frames[10])[1], at)  # esc cancelled the count, and stayed;
        listed = rows(frames[11])[0]
        self.assertEqual(listed.index(rows(frames[11])[1]), listed.index(at) - 1)  # k moved one row.
        self.assertEqual(rows(frames[14])[0].index(rows(frames[14])[1]), 2)  # 3gg: row 3.
        self.assertIn(screen_new.NUDGE, " ".join(frames[15]))  # A count isn't text either.
        self.assertEqual(len(frames), 16)  # q went back.
        # Clean up: the rows in view, and the option below them, which is always in view.
        entries = [{"name": f"fix/{i:02}", "worktree": f"/w/{i}", "track": ["remote deleted"], "dirty": False}
                   for i in range(12)]
        screen = FakeScreen(["3", "j", "L", "H", "M", "5", "G", "2", "\x1b", "j", "G", "q"], (16, 100))
        picker = self.picker(screen, entries)
        picker.session, picker.terminals = "s", []
        with patch.object(worktrees, "missing", return_value=False):
            self.assertFalse(drive(screen, picker.cleanup))
        texts = [screen.text(i) for i in range(len(screen.frames))]

        def cursor(rows):
            """The selected row's name, or "branches" on the option to delete them."""
            line = next(line for line in rows if line.startswith("› "))
            return "branches" if "Also delete" in line else line[6:].split()[0]

        def shown(rows):
            return [line[6:].split()[0] for line in rows if line[2:6] in ("[x] ", "[ ] ") and "fix/" in line]

        self.assertEqual(cursor(texts[2]), "fix/03")  # 3j.
        self.assertEqual(cursor(texts[3]), "branches")  # L: the option, the bottom row in view.
        self.assertEqual(cursor(texts[4]), shown(texts[4])[0])  # H.
        view = shown(texts[5]) + ["branches"]
        self.assertEqual(cursor(texts[5]), view[(len(view) - 1) // 2])  # M.
        self.assertEqual(cursor(texts[7]), "fix/04")  # 5G: row 5.
        self.assertEqual(cursor(texts[10]), "fix/05")  # esc cancelled the count, and j moved one row.
        self.assertEqual(cursor(texts[11]), "branches")  # G.
        self.assertEqual(len(texts), 12)  # The esc didn't cancel cleanup; the q did.

    def test_help_and_details_scroll_with_counts_and_esc_cancels_one(self):
        message = "fatal: " + "cannot lock ref because the path is long " * 30
        screen = FakeScreen(["5", "j", "2", "\x1b", "j", "H", "q"], (16, 50))
        drive(screen, lambda: ui.show_error(screen, message))
        tops = [int(screen.text(i)[0].split()[-3].split("–")[0]) for i in range(len(screen.frames))]
        self.assertEqual(tops, [1, 1, 6, 6, 6, 7, 7])  # 5j, then esc only cancels the 2; H isn't a motion here.

    def test_list_offers_n_when_the_main_checkout_is_alone(self):
        alone = self.list_entries([])
        for size in ((16, 50), (24, 100)):
            _, frames = self.run_list(["q"], alone, size)
            with self.subTest(size=size):
                main_row = next(i for i, line in enumerate(frames[0]) if line.startswith("› ● main")
                                or line.startswith("›   main"))
                self.assertEqual(frames[0][main_row + 1].strip(), "No other worktrees yet: n creates one")
        _, frames = self.run_list(["q"], self.list_entries(["fix/a"]))
        self.assertFalse(any("No other worktrees" in line for line in frames[0]))

    def test_sort_cycles_and_is_remembered(self):
        entries = self.list_entries(["b/old", "a/new"])
        entries[1]["time"], entries[2]["time"] = 10, 20
        names = lambda frame: [line[4:].split()[0] for line in frame if line[4:].startswith(("main", "a/", "b/"))]
        picker, frames = self.run_list(["S", "S", "q"], entries)
        self.assertEqual(names(frames[0]), ["main", "a/new", "b/old"])  # Most recent first, by default.
        self.assertNotIn("sorted by", frames[0][1])
        self.assertIn("sorted by state", frames[1][1])
        self.assertIn("sorted by name", frames[2][1])
        self.assertEqual(util.load_settings(), {"sort": "name"})
        manager = lambda: app.Manager(SimpleNamespace(), str(self.repo), "session:1", "")
        with patch.object(app.Manager, "reload"), patch.object(app.threading, "Thread"), \
                patch.object(github, "pull_requests_enabled", return_value=False):
            self.assertEqual(manager().sort, "name")  # The next run starts sorted by name.
            # A broken or odd file never stops the picker: it just starts with the default.
            for content in ("{not json", "[1, 2]", '{"sort": "size"}', '{"sort": ["name"]}', ""):
                util.settings_path().write_text(content)
                self.assertEqual(manager().sort, "recent", content)
            util.settings_path().unlink()
            self.assertEqual(manager().sort, "recent")
        with patch.dict(os.environ, {"XDG_STATE_HOME": "relative/state"}):
            self.assertEqual(util.settings_path(), Path(os.path.expanduser("~/.local/state/rex/worktrees.json")))
        unwritable = Path(self.tmp.name) / "file"
        unwritable.write_text("")
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(unwritable)}):
            util.save_settings(sort="state")  # A folder that can't be made: forgotten, not an error.
            self.assertEqual(util.load_settings(), {})

    def test_filter_clears_once_its_only_match_is_removed(self):
        entries = self.list_entries(["fix/login", "docs/readme"])
        screen = FakeScreen(["/"] + list("login") + ["\x1b", "d", "d", "q"], (24, 100))
        picker = self.picker(screen, entries)
        picker.terminals = []

        def remove(entry):
            picker.entries.remove(entry)
            return False

        picker.remove = remove
        with patch.object(worktrees, "missing", return_value=False):
            drive(screen, picker.list_screen)
        frames = [screen.text(i) for i in range(len(screen.frames))]
        self.assertIn("/ to filter", frames[-1])
        self.assertFalse(any("No matching worktrees" in line for line in frames[-1]))
        # A filter that still matches something stays.
        entries = self.list_entries(["fix/login", "feat/login-flow"])
        screen = FakeScreen(["/"] + list("login") + ["\x1b", "d", "d", "q"], (24, 100))
        picker = self.picker(screen, entries)
        picker.terminals, picker.remove = [], remove
        with patch.object(worktrees, "missing", return_value=False):
            drive(screen, picker.list_screen)
        self.assertEqual(self.search_text(screen.text()), "login")

    def test_folder_heading_sits_over_the_folder_names(self):
        entries = [{"name": "main", "worktree": "/w/repo", "main": True, "track": []},
                   {"name": "fix/a", "worktree": "/w/repo-login", "track": []},
                   {"name": "fix/b", "worktree": "/repo/.claude/worktrees/payments", "track": []}]
        widths = ui.column_widths(entries, 120)
        header = "".join(t for t, _ in ui.header_row(widths))
        rows = ["".join(t for t, _ in ui.entry_row(e, False, widths)) for e in entries]
        at = header.index("FOLDER")
        self.assertEqual([row[at:].split()[0] for row in rows], ["repo", "login", "payments"])
        self.assertEqual(rows[2][at - len("claude "):at], "claude ")  # The tool, in its own column.

    def test_remove_says_why_the_branch_is_kept(self):
        other = self.add("ahead", "ahead")
        git(other, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "ahead")
        entry = {**worktrees.worktrees(str(self.repo))[1], "name": "ahead"}
        self.assertEqual(screen_remove.kept_reason(entry, "main"), "The branch will be kept: it has commits not in main.")
        self.assertIn("default branch", screen_remove.kept_reason({"name": "main"}, "origin/main"))
        for size in ((16, 50), (22, 100)):
            screen = FakeScreen(["q"], size)
            picker = self.picker(screen)
            picker.session, picker.terminals, picker.entries = "s", [], [entry]
            picker.reload = lambda: None
            drive(screen, lambda: picker.remove(entry))
            text = " ".join(line.strip() for line in screen.text(0))
            with self.subTest(size=size):
                self.assertIn("The branch will be kept: it has commits not in main.", text)
                self.assertNotIn("Also delete the branch", text)

    def test_git_errors_lead_with_the_error_and_join_wrapped_sentences(self):
        message = ("Preparing worktree (new branch 'x')\n"
                   "fatal: cannot lock ref 'refs/heads/x': Unable to create '/r/.git/refs/heads/x.lock': File exists.\n"
                   "\n"
                   "Another git process seems to be running in this repository, e.g.\n"
                   "an editor opened by 'git commit'. Please make sure all processes\n"
                   "are terminated then try again. If it still fails, a git process\n"
                   "may have crashed in this repository earlier:\n"
                   "remove the file manually to continue.\n"
                   "hint: one\n"
                   "hint: two\n"
                   "\tindented.txt")
        tidy = ui.tidy_error(message).splitlines()
        self.assertTrue(tidy[0].startswith("fatal: cannot lock ref"))
        self.assertEqual(tidy[2], "Another git process seems to be running in this repository, e.g. an editor opened "
                                  "by 'git commit'. Please make sure all processes are terminated then try again. If "
                                  "it still fails, a git process may have crashed in this repository earlier: remove "
                                  "the file manually to continue.")
        self.assertEqual(tidy[3:], ["hint: one", "hint: two", "\tindented.txt", "", "Preparing worktree (new branch 'x')"])
        self.assertEqual(ui.tidy_error(ui.tidy_error(message)), ui.tidy_error(message))
        self.assertEqual(ui.tidy_error("please run:\n  gh auth login"), "please run:\n  gh auth login")
        screen = FakeScreen(["q"], (16, 50))
        drive(screen, lambda: ui.show_error(screen, message))
        rows = [line.strip() for line in screen.text(0)]
        self.assertIn(util.ICONS["error"] + " fatal: cannot lock ref", rows[1])
        self.assertIn("this repository, e.g. an editor opened by", " ".join(rows))  # Joined, then wrapped.

    def test_only_ui_names_colors(self):
        """Screens ask ui for a role (see ui.ROLES), never a color, so a color keeps one meaning."""
        here = Path(__file__).resolve().parent
        colors = "|".join(sorted(set(ui.ROLES.values()) - {"dim"}))
        known = set(ui.ROLES) | {"bold", "underline"}
        for path in sorted(here.glob("*.py")):
            if path.name in ("ui.py", "test_worktrees.py"):
                continue
            source = path.read_text()
            with self.subTest(module=path.name):
                self.assertNotRegex(source, rf"[\"']({colors})[\"']")
                self.assertNotRegex(source, r"COLOR_|color_pair")
                for name in re.findall(r"style\(\"(\w+)\"\)", source):
                    self.assertIn(name, known)
        # A color's own name gives nothing, so one that slips through shows plainly, not wrongly.
        with patch.dict(ui.COLORS, {"red": 1 << 20, "bold": 1 << 21}):
            self.assertEqual((ui.style("red"), ui.style("danger"), ui.style("bold")), (0, 1 << 20, 1 << 21))

    def test_keys_list_only_what_can_act(self):
        keys = ui.Keys().add("enter", "open").add("dd", "remove", rank=3, why="It's locked.")
        keys.add("tab", "", rank=1).add("j/k", "move", can=False)
        self.assertEqual(keys.listed(), ["enter"])
        self.assertEqual([t for t, *_ in keys.row()][::2], ["enter"])
        said = []
        self.assertTrue(keys.refused("dd", said.append))
        self.assertTrue(keys.refused("j/k", said.append) and keys.refused("x", said.append))
        self.assertFalse(keys.refused("enter", said.append) or keys.refused("tab", said.append))
        self.assertEqual(said, ["It's locked."])  # Only a refusal with a reason says anything.

    @staticmethod
    def footer_keys(frame):
        """The keys a frame's footer lists."""
        return [segment[0] for segment in frame[-1][::2]]

    def test_footers_list_only_keys_that_can_act(self):
        """The keys rule (see the README's design rules): a footer lists only the keys that would
        act now, and pressing one it leaves out is refused, saying why, or for enter on a
        confirmation or in New worktree, making the reason on screen stand out; a move with
        nowhere to go just doesn't move. Each scenario is a screen reached by some keys, then a
        key's name, the keys that press it, and whether it's refused there: the footer before the
        press lists the key exactly when the press isn't refused. tab can act without being
        listed, where nothing is cut. Every screen presses its moves and tab. Add scenarios as
        screens change."""
        said = []
        set_flash = ui.Flash.set

        def record(flash, text, tone="danger"):
            if tone == "attention":  # A key that can't act says why in this role.
                said.append(text)
            set_flash(flash, text, tone)

        acted = []

        def act(*args, **kwargs):
            acted.append(args)
            return True

        def entry(name, **state):
            return {"name": name, "worktree": "/w/" + name, "branch": "refs/heads/" + name, "dirty": False,
                    "open": False, "track": [], "time": 0, **state}

        everything = [entry("main", main=True, worktree=str(self.repo)), entry("fix/plain"),
                      entry("release/locked", locked=""), entry("docs/missing", prunable="gone"),
                      entry("fix/done", track=["remote deleted"]),
                      entry("fix/dirty-done", track=["remote deleted"], dirty=True),
                      entry("ext/unmounted", locked="", prunable="gone")]  # Locked, so kept though missing.
        few = [everything[0], everything[5]]  # Nothing to clean up.
        alone = [everything[0]]  # The main checkout only: nothing to move to.
        missing = patch.object(worktrees, "missing", side_effect=lambda e: "prunable" in e)

        def run_list(entries):
            def run(keys):
                screen = FakeScreen(keys + ["\x1b"] * 3 + ["q"] * 3, (24, 140))
                picker = self.picker(screen, [dict(e) for e in entries])
                picker.session, picker.client, picker.origin, picker.terminals = "s", None, "", []
                picker.remove = picker.new_screen = lambda *args, **kwargs: False
                for e in picker.entries:
                    picker.commits[(e["worktree"], None)] = ("Recent commits", [])
                with missing, patch.object(tabs, "open_tab"), patch.object(tabs, "open_split"):
                    drive(screen, picker.list_screen)
                return screen.frames, bool(said)
            return run

        def run_remove(keys, mode="remove", options=1):
            """Remove's confirmation, with Discard and maybe deleting the branch, or prune's or
            repair's: a key is refused when the screen says why, or, for enter, when the screen
            is cancelled after it rather than confirmed."""
            screen = FakeScreen(keys + ["q", "q", "q"], (24, 100))
            picker = self.picker(screen)
            picker.session = "s"
            if mode != "remove":
                run = lambda: picker.removal_options(entry("docs/x", prunable="gone"), [], [], None, [], mode=mode)
            else:
                choices = [{"id": "force", "checked": False, "label": [("Discard uncommitted changes", 0)]},
                           {"id": "delete_branch", "checked": False, "label": [("Also delete the branch", 0)]}]
                run = lambda: picker.removal_options(entry("fix/x"), ["?? a.txt"], [], "main" if options > 1 else None,
                                                     choices[:options])
            return screen.frames, bool(said) or drive(screen, run) is None

        def run_cleanup(entries=None):
            def run(keys):
                screen = FakeScreen(keys + ["q", "q", "q"], (24, 100))
                picker = self.picker(screen, entries or [entry("fix/dirty", track=["remote deleted"], dirty=True),
                                                         entry("fix/done", track=["remote deleted"])])
                picker.session, picker.terminals, picker.reload = "s", [], lambda: None
                with missing, patch.object(actions, "remove_many", return_value=([], [], 0, [])):
                    drive(screen, picker.cleanup)
                return screen.frames, bool(said)
            return run

        cwd, branches = self.setup_branches()

        def run_new(keys, branches=branches):
            screen = FakeScreen(keys + ["\x1b"] * 3 + ["q"] * 2, (24, 100))
            picker = self.picker(screen)
            picker.branches = lambda: branches
            picker.create_and_open = picker.open_checked_out = act
            with patch.object(worktrees, "setup_command", return_value=""):
                drive(screen, lambda: picker.new_screen(root=True))
            return screen.frames, bool(said) or not acted

        # Two bases, so there are two create rows.
        bases = [{"ref": "HEAD", "label": "origin/main", "note": "default branch"},
                 {"ref": "HEAD", "label": "main", "note": "current branch"}]
        run_new_two = lambda keys: run_new(keys, {**branches, "bases": bases})  # noqa: E731
        run_new_none = lambda keys: run_new(keys, {**branches, "suggestions": []})  # noqa: E731

        presses = {"enter": ["\n"], "s": ["s"], "dd": ["d", "d"], "c": ["c"], "j/k": ["j", "k"]}
        # The list's keys refused on each row: with every kind of worktree, then with nothing
        # cleanup can act on. dd prunes a missing folder, unless it's locked.
        refusals = [{"dd"}, set(), {"dd"}, {"enter", "s"}, set(), set(), {"enter", "s", "dd"}, {"dd", "c"}, {"c"}]
        rows = [(entries, i) for entries in (everything, few) for i in range(len(entries))]
        scenarios = [(f"list {entries[i]['name']}{' (few)' if entries is few else ''}", run_list(entries), ["j"] * i,
                      key, keys, key in refused) for (entries, i), refused in zip(rows, refusals)
                     for key, keys in presses.items()]
        scenarios += [
            ("list, the main checkout alone", run_list(alone), [], "j/k", ["j", "k"], True),
            ("list, the main checkout alone", run_list(alone), [], "dd", ["d", "d"], True),
            ("list filter on a missing folder", run_list(everything), ["/"] + list("docs"), "enter", ["\n"], True),
            ("list filter", run_list(everything), ["/"] + list("plain"), "enter", ["\n"], False),
            ("list filter, one match", run_list(everything), ["/"] + list("plain"), "↑/↓",
             [curses.KEY_DOWN, curses.KEY_UP], True),
            ("list filter, two matches", run_list(everything), ["/"] + list("fix"), "↑/↓",
             [curses.KEY_DOWN, curses.KEY_UP], False),
            ("list filter, typing", run_list(everything), ["/"] + list("plain"), "esc", ["\x1b"], False),
            ("list filter, done typing", run_list(everything), ["/"] + list("plain") + ["\x1b"], "esc", ["\x1b"],
             False),
            ("list filter, done typing on a missing folder", run_list(everything), ["/"] + list("docs") + ["\x1b"],
             "s", ["s"], True),
            ("remove, discard unchecked", run_remove, [], "enter", ["\n"], True),
            ("remove, discard checked", run_remove, [" "], "enter", ["\n"], False),
            ("remove, discard unchecked, toggled twice", run_remove, [" ", " "], "enter", ["\n"], True),
            ("remove, one option", run_remove, [], "j/k", ["j", "k"], True),
            ("remove, two options", lambda keys: run_remove(keys, options=2), [], "j/k", ["j", "k"], False),
            ("remove", run_remove, [], "tab", ["\t"], False),
            ("prune", lambda keys: run_remove(keys, "prune"), [], "enter", ["\n"], False),
            ("prune, no options", lambda keys: run_remove(keys, "prune"), [], "space", [" "], True),
            ("prune, no options", lambda keys: run_remove(keys, "prune"), [], "j/k", ["j", "k"], True),
            ("prune", lambda keys: run_remove(keys, "prune"), [], "tab", ["\t"], False),
            ("repair", lambda keys: run_remove(keys, "repair"), [], "enter", ["\n"], False),
            ("repair, no options", lambda keys: run_remove(keys, "repair"), [], "space", [" "], True),
            ("cleanup, on a dirty row", run_cleanup(), ["k"], "space", [" "], True),
            ("cleanup, on a clean row", run_cleanup(), [], "space", [" "], False),
            ("cleanup, one checked", run_cleanup(), [], "enter", ["\n"], False),
            ("cleanup, none checked", run_cleanup(), [" "], "enter", ["\n"], True),
            ("cleanup", run_cleanup(), [], "j/k", ["j", "k"], False),
            ("cleanup", run_cleanup(), [], "tab", ["\t"], False),
            ("cleanup, one finished, and its branch", run_cleanup([entry("fix/done", track=["remote deleted"])]), [],
             "j/k", ["j", "k"], False),
            ("cleanup, one missing, no branch to delete", run_cleanup([entry("docs/gone", prunable="gone")]), [],
             "j/k", ["j", "k"], True),
            ("cleanup, one missing, unchecked", run_cleanup([entry("docs/gone", prunable="gone")]), [],
             "enter", ["\n"], True),
            # New worktree opens in normal mode, where the list's motions move. Enter on the row
            # asking for a name starts typing there, which another test checks, since it's not
            # an action this one can see.
            ("new worktree, no text, on a branch", run_new, ["j"], "enter", ["\n"], False),
            ("new worktree, no text", run_new, [], "j/k", ["j", "k"], False),
            ("new worktree, no text", run_new, [], "tab", ["\t"], True),
            ("new worktree, no text, on a branch", run_new, ["j"], "tab", ["\t"], False),
            ("new worktree, no branches", run_new_none, [], "j/k", ["j", "k"], True),
            ("new worktree, gp on the create row", run_new, [], "gp", ["g", "p"], True),
            ("new worktree, ctrl+g on the create row", run_new, [], "ctrl+g", ["\x07"], True),
            # Typing, the moves are keys that can't be text.
            ("new worktree, typing, no text", run_new, ["i"], "enter", ["\n"], True),
            ("new worktree, typing, no text, on a branch", run_new, ["i", curses.KEY_DOWN], "enter", ["\n"], False),
            ("new worktree, typing, no text", run_new, ["i"], "↑/↓", [curses.KEY_DOWN, curses.KEY_UP], False),
            ("new worktree, typing, no text", run_new, ["/"], "tab", ["\t"], True),
            ("new worktree, typing, no text, on a branch", run_new, ["a", curses.KEY_DOWN], "tab", ["\t"], False),
            ("new worktree, typing, no branches", run_new_none, ["i"], "↑/↓", [curses.KEY_DOWN, curses.KEY_UP], True),
            ("new worktree, a new name", run_new_two, typing("fix it"), "enter", ["\n"], False),
            ("new worktree, a new name", run_new_two, typing("fix it"), "tab", ["\t"], False),
            ("new worktree, a new name from the second base", run_new_two, typing("fix it") + [curses.KEY_DOWN],
             "enter", ["\n"], False),
            ("new worktree, a branch", run_new, typing("existing"), "enter", ["\n"], False),
            ("new worktree, a branch, its create row", run_new, typing("existing") + [curses.KEY_UP], "enter",
             ["\n"], True),
            ("new worktree, checked out", run_new, typing("main"), "enter", ["\n"], False),
            ("new worktree, can't be created", run_new, typing("origin/new-idea"), "enter", ["\n"], True),
            ("new worktree, can't be created", run_new, typing("origin/new-idea"), "tab", ["\t"], True),
            ("new worktree, half typed", run_new, typing("fix/"), "enter", ["\n"], True),
            ("new worktree, half typed with a dash", run_new, typing("fix-"), "enter", ["\n"], True),
            ("new worktree, nothing usable", run_new, typing("///"), "enter", ["\n"], True),
            ("new worktree, typing, ctrl+g on the create row", run_new, ["i"], "ctrl+g", ["\x07"], True),
            # Done typing, the text stays, and the keys act on it.
            ("new worktree, a new name, done typing", run_new_two, typing("fix it") + ["\x1b"], "enter", ["\n"],
             False),
            ("new worktree, a new name, done typing", run_new_two, typing("fix it") + ["\x1b"], "j/k", ["j", "k"],
             False),
            ("new worktree, a branch, done typing", run_new, typing("existing") + ["\x1b"], "enter", ["\n"], False),
            ("new worktree, a branch's create row, done typing", run_new, typing("existing") + ["\x1b", "k"],
             "enter", ["\n"], True),
            ("new worktree, can't be created, done typing", run_new, typing("origin/new-idea") + ["\x1b"], "enter",
             ["\n"], True),
            ("new worktree, can't be created, done typing", run_new, typing("origin/new-idea") + ["\x1b"], "tab",
             ["\t"], True),
        ]

        def text(frame):
            return ["".join(segment[0] for segment in row) for row in frame]

        with patch.object(ui.Flash, "set", record):
            for name, run, before, key, keys, expected in scenarios:
                del said[:], acted[:]
                frames, refused = run(before + keys)
                listed = self.footer_keys(frames[len(before)])
                after = frames[len(before) + 1:]
                moves = key in ("j/k", "↑/↓")
                if moves:  # It acts when either way moves something.
                    refused = text(frames[len(before)]) == text(after[0]) == text(after[1])
                elif key == "tab":  # It acts when it shows the details.
                    refused = not any("— details" in line for frame in after[:1] for line in text(frame)[:2])
                with self.subTest(name, key=key):
                    self.assertEqual(refused, expected, (listed, said))
                    if key == "tab" and not refused:
                        pass  # Listed only when something's cut, which none of these are.
                    else:
                        self.assertEqual(key in listed, not refused, (listed, said))
                    if refused and key in ("tab", "space", "dd", "s", "c"):
                        self.assertTrue(said, "refused without saying why")
                    if key == "dd" and refused:  # Refused at the first d, not after a second.
                        self.assertFalse(any("press d again" in line for line in text(after[0])), text(after[0]))


    def test_enter_acts_only_on_a_frame_that_was_drawn(self):
        """Pasted text ending in a newline, such as "login\n", isn't drawn while it arrives; its
        enter draws the frame instead of acting on it, so the enter pressed after seeing it acts.
        The confirmations drop an enter that was waiting before they were drawn, too."""
        git(self.repo, "branch", "feat/login-flow")
        cwd, branches = self.setup_branches()

        def new(keys, text=""):
            screen = Typed(keys + [PAUSE] + LEAVE, (24, 100))
            picker = self.picker(screen)
            picker.branches = lambda: branches
            made = []
            picker.create_and_open = lambda cwd, plan: made.append(plan["branch"])
            with patch.object(worktrees, "setup_command", return_value=""):
                screen.drive(lambda: picker.new_screen(root=True, text=text))
            return screen, made

        # New worktree opens in normal mode, so a paste that starts typing, with i or /, as the
        # list's filter does, then names a branch and ends in a newline, types the name and
        # creates nothing: the frame its enter would act on wasn't drawn.
        for start in ("i", "/"):
            screen, made = new([start] + list("login") + ["\n"])
            with self.subTest(start=start):
                self.assertEqual(made, [])  # Nothing was checked out unseen,
                rows = screen.text(0)  # and the first frame drawn is the one enter would have acted on.
                self.assertEqual(self.search_text(rows), "login")
                self.assertIn("esc done", rows[-1])  # Still typing.
                self.assertTrue(any(line.startswith("› feat/login-flow") for line in rows), rows)
                self.assertIn("Checks out".ljust(screen_new.PLAN_COLUMN) + "feat/login-flow", rows)
        self.assertEqual(new(typing("login") + ["\n", PAUSE, "\n"])[1], ["feat/login-flow"])  # Seen, then pressed.
        self.assertEqual(new(typing("login") + ["\n", "\n"])[1], [])  # Every enter in a paste is dropped.
        # A name pasted without starting to type isn't text, and its newline is dropped all the same.
        screen, made = new(list("login") + ["\n"])
        self.assertEqual((made, self.search_text(screen.text(0))), ([], screen_new.IDLE))
        # Opened with text, as from the list's filter, in normal mode: an enter already waiting as
        # it opens, such as the rest of a paste, is dropped too; one pressed after it shows acts.
        self.assertEqual(new(["\n"], "login")[1], [])
        self.assertEqual(new([PAUSE, "\n"], "login")[1], ["feat/login-flow"])

        entry = {"name": "fix/x", "worktree": str(self.repo), "branch": "refs/heads/fix/x"}

        def remove(keys):
            screen = Typed(keys + [PAUSE, "q"], (24, 100))
            picker = self.picker(screen)
            picker.session = "s"
            options = [{"id": "force", "checked": False, "label": [("Discard uncommitted changes", 0)]}]
            return screen.drive(lambda: picker.removal_options(entry, ["?? a.txt"], [], None, options))

        self.assertIsNone(remove([" ", "\n"]))  # Checked and confirmed in one burst: the q after cancels.
        self.assertEqual(remove([" ", PAUSE, "\n"]), {"force"})
        finished = [{"name": "fix/done", "worktree": "/w/done", "track": ["remote deleted"], "dirty": False}]

        def clean(keys):
            screen = Typed(keys + [PAUSE, "q"], (24, 100))
            picker = self.picker(screen, finished)
            picker.session, picker.terminals, picker.reload = "s", [], lambda: None
            with patch.object(worktrees, "missing", return_value=False), \
                    patch.object(actions, "remove_many", return_value=(["fix/done"], [], 0, [])) as many:
                screen.drive(picker.cleanup)
            return many.called

        self.assertFalse(clean(["\n"]))  # As c opened it, such as from "c\n" typed at once.
        self.assertTrue(clean([PAUSE, "\n"]))

    def test_remove_keeps_a_branch_that_gained_commits_while_the_screen_was_open(self):
        other = self.add("merged", "merged")
        entry = {**worktrees.worktrees(str(self.repo))[1], "name": "merged"}
        self.assertEqual(worktrees.merged_into(str(self.repo), entry), "main")  # So the screen offered it.
        git(other, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "late")
        with patch.object(tabs, "close_panes"):
            deleted, problems = actions.remove_worktree(str(self.repo), "s", entry, [], delete_branch=True)
        self.assertFalse(other.exists())
        self.assertFalse(deleted)
        self.assertEqual(problems, ["its branch was kept: it has commits not in main now"])
        git(self.repo, "rev-parse", "--verify", "refs/heads/merged")  # The late commit is still there.
        # The confirmation's message says what was done, then that.
        screen = FakeScreen([" ", "\n"], (24, 100))
        again = self.add("merged-too", "merged-too")
        entry = {**next(e for e in worktrees.worktrees(str(self.repo)) if e.get("branch") == "refs/heads/merged-too"),
                 "name": "merged-too"}
        picker = self.picker(screen, [entry])
        picker.session, picker.terminals, picker.reload = "s", [], lambda: None

        def late(*args, **kwargs):  # A commit lands as the screen asks.
            git(again, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "late")
            return original(*args, **kwargs)

        original = screen_remove.RemovalScreens.removal_options
        with patch.object(screen_remove.RemovalScreens, "removal_options", late), patch.object(tabs, "close_panes"):
            drive(screen, lambda: picker.remove(entry))
        self.assertEqual(picker.flash.text,
                         "Removed merged-too, but its branch was kept: it has commits not in main now.")
        git(self.repo, "rev-parse", "--verify", "refs/heads/merged-too")

    def test_a_new_branch_starts_from_the_branch_its_row_names(self):
        cwd, branches = self.two_bases()
        current = branches["bases"][1]
        self.assertEqual((current["label"], current["ref"]), ("main", "refs/heads/main"))
        main = git(cwd, "rev-parse", "main").stdout
        # The main checkout moves to another branch after the screen loaded: the row still says
        # main, so the new branch starts there, not where HEAD is when enter is pressed.
        git(cwd, "checkout", "-q", "-b", "elsewhere")
        git(cwd, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "elsewhere")
        plan = branching.from_base(branching.plan_branch(cwd, branches, "from-current", None, branches["bases"][0]),
                                   current)
        branching.create_worktree(cwd, plan)
        self.assertEqual(git(cwd, "rev-parse", "from-current").stdout, main)
        # A tag named like the branch doesn't stand in for it.
        git(cwd, "tag", "main", "elsewhere")
        plan = branching.from_base(branching.plan_branch(cwd, branches, "after-tag", None, branches["bases"][0]),
                                   current)
        branching.create_worktree(cwd, plan)
        self.assertEqual(git(cwd, "rev-parse", "after-tag").stdout, main)
        # Detached, it's the commit, by its whole hash.
        git(cwd, "checkout", "-q", "--detach", "elsewhere")
        bases = branching.load_branches(cwd, worktrees.worktrees(cwd))["bases"]
        sha = git(cwd, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual((bases[-1]["ref"], bases[-1]["label"], bases[-1]["note"]), (sha, sha[:len(bases[-1]["label"])],
                                                                                      "current commit"))

    def test_a_worktree_whose_git_file_is_gone_is_broken_and_dd_repairs_it(self):
        lost, lost_too = self.add("lost", "fix/lost"), self.add("lost-too", "fix/lost-too")
        gone = self.add("gone", "fix/gone")
        for path in (lost, lost_too):
            (path / ".git").unlink()  # Git calls each prunable, though its folder is there.
        (lost / "work.txt").write_text("keep")
        shutil.rmtree(gone)
        entries = worktrees.base_entries(str(self.repo), details=False)
        named = {e["name"]: e for e in entries}
        self.assertTrue(all("prunable" in named[n] for n in ("fix/lost", "fix/lost-too", "fix/gone")))
        self.assertEqual([n for n, e in named.items() if worktrees.broken(e)], ["fix/lost", "fix/lost-too"])
        self.assertEqual([n for n, e in named.items() if worktrees.missing(e)], ["fix/gone"])  # Its folder is gone.
        self.assertIsNone(worktrees.is_dirty(named["fix/lost"]))  # Git can't read its status.
        for e in entries:
            e.update(dirty=worktrees.is_dirty(e), open=False, time=10 if e["name"] == "fix/lost" else 1)
        # Not cleanup's: Git refuses to remove it, and pruning would leave its folder.
        self.assertEqual([e["name"] for e in screen_remove.candidates(entries)], ["fix/gone"])
        # The list labels it, says what's wrong, and offers dd to repair it, but not enter or s.
        screen = FakeScreen(["j", "d", "q", "q"], (24, 140))
        picker = self.picker(screen, entries)
        picker.terminals = []
        drive(screen, picker.list_screen)
        rows = screen.text(1)
        self.assertTrue(any(line.startswith("› ") and "fix/lost " in line and "broken" in line for line in rows),
                        rows)
        self.assertIn("1 missing · 2 broken", rows[1])
        self.assertIn(screen_list.BROKEN, " ".join(" ".join(rows).split()))
        footer = self.footer_keys(screen.frames[1])
        self.assertTrue("dd" in footer and "enter" not in footer and "s" not in footer, footer)
        self.assertIn("dd repair", rows[-1])
        self.assertIn("c clean up", rows[-1])  # For the missing one, though not for these.
        self.assertIn("d… press d again to repair fix/lost", screen.text(2))
        # The confirmation lists the other one Git repairs at the same time; enter repairs both,
        # keeping the folder's files.
        screen = FakeScreen(["\n"], (24, 100))
        picker = self.picker(screen, entries)
        picker.reload = lambda: None
        drive(screen, lambda: picker.remove(named["fix/lost"]))
        rows = screen.text(0)
        self.assertTrue(rows[1].startswith("Repair worktree"))
        self.assertIn("ALSO REPAIRED (1)", " ".join(rows))
        self.assertIn("  fix/lost-too", rows)
        self.assertIn("enter repair", rows[-1])
        self.assertEqual(picker.flash.text, "Repaired fix/lost and 1 other worktree; Git can use them again.")
        self.assertEqual((lost / "work.txt").read_text(), "keep")
        listed = {e.get("branch"): e for e in worktrees.worktrees(str(self.repo))}
        self.assertEqual([b for b, e in listed.items() if "prunable" in e], ["refs/heads/fix/gone"])
        self.assertEqual(worktrees.changed_files({"worktree": str(lost)}), ["?? work.txt"])  # Git can use it.
        # A missing one is still pruned rather than repaired, and its folder coming back is refused.
        gone_entry = named["fix/gone"]
        gone.mkdir()
        self.assertTrue(worktrees.broken(gone_entry) and not worktrees.missing(gone_entry))
        with self.assertRaisesRegex(RuntimeError, "folder is back"):
            actions.prune_worktree(str(self.repo), gone_entry)

    def test_new_worktree_pins_the_plan_above_the_footer(self):
        git(self.repo, "branch", "feat/login-flow")
        cwd, branches = self.setup_branches()
        for size in ((32, 120), (22, 100), (16, 50)):
            # A branch selected with every branch listed, then with only the one the text matches.
            frames = self.new_worktree_frames([curses.KEY_DOWN] + typing("login") + LEAVE, branches, size,
                                              setup="make setup")
            planned = [f for f in frames if self.plan_start(f) is not None]
            gap = 1 if size[0] >= 22 else 0  # A blank row between the plan and the footer, with room.
            with self.subTest(size=size):
                # The branch, then i, the five letters, and esc, which stops typing; the next esc
                # clears the text, leaving no plan.
                self.assertEqual(len(planned), 8)
                for rows in planned:  # Wherever the list ends, the plan ends right above the footer.
                    self.assertTrue(rows[-2 - gap].startswith("Then runs"), rows)
                    self.assertEqual(rows[-2:-1], [""] if gap else [rows[-2]], rows)
                typed = next(f for f in frames if self.search_text(f) == "login")
                self.assertIn(screen_new.PROMPT + "login  1 of 3", typed)  # How many match, right after the text.

    def test_state_column_has_room_for_changes_and_stays_put_while_statuses_load(self):
        loading = [{"name": "main", "worktree": "/w/repo", "main": True, "track": [], "dirty": util.PENDING,
                    "open": util.PENDING},
                   {"name": "fix/a", "worktree": "/w/repo-a", "track": [], "dirty": util.PENDING, "open": util.PENDING},
                   {"name": "fix/b", "worktree": "/w/repo-b", "track": [], "dirty": util.PENDING, "open": util.PENDING}]
        loaded = [{**loading[0], "dirty": False, "open": False}, {**loading[1], "dirty": True, "open": False},
                  {**loading[2], "dirty": False, "open": False}]
        with patch.object(worktrees, "missing", return_value=False):
            # A row with a label doesn't add the loading mark, which would make the column wider.
            self.assertEqual([t for t, _ in ui.state_cell(loading[0])], [ui.label("main")[0]])
            self.assertEqual([t for t, _ in ui.state_cell(loading[1])], ["…"])
            before, after = ui.column_widths(loading, 120), ui.column_widths(loading[:1] + loaded[1:], 120)
            self.assertEqual(before["STATE"], ui.cell_width([ui.label("dirty")]))  # As wide as changes, no wider.
            self.assertEqual(before, after)  # Nothing shifts when changes arrive.


if __name__ == "__main__":
    unittest.main()
