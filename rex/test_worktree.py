import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch

spec = importlib.util.spec_from_file_location("worktree", Path(__file__).with_name("worktree.py"))
worktree = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worktree)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo with spaces"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "initial")
        home = patch.object(worktree.Path, "home", return_value=Path(self.tmp.name) / "home")
        home.start()
        self.addCleanup(home.stop)

    def add(self, name, branch):
        path = Path(self.tmp.name) / name
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path))
        return path

    def test_porcelain_paths_and_stable_destination(self):
        other = self.add("feature checkout", "feature/test")
        entries = worktree.worktrees(str(self.repo))
        self.assertEqual([os.path.realpath(e["worktree"]) for e in entries], [os.path.realpath(self.repo), os.path.realpath(other)])
        self.assertEqual(entries[1]["branch"], "refs/heads/feature/test")
        self.assertEqual(worktree.new_path(str(self.repo), "another/test"), worktree.new_path(str(other), "another/test"))
        self.assertNotEqual(worktree.new_path(str(self.repo), "a/b"), worktree.new_path(str(self.repo), "a-b"))

    def test_entries_have_status_and_current_worktree(self):
        other = self.add("other", "other")
        (other / "untracked").write_text("changes")
        nested = other / "nested"
        nested.mkdir()
        with patch.object(worktree, "terminal_cwds", return_value=[({"window_id": "w"}, str(other))]):
            entries = worktree.load_entries(str(nested), "session:1")
        main, current = entries
        self.assertTrue(main["main"])
        self.assertFalse(main["current"] or main["dirty"] or main["open"])
        self.assertTrue(current["current"] and current["dirty"] and current["open"])
        self.assertEqual(current["name"], "other")

    def test_line_editing_at_cursor(self):
        edit, c = worktree.edit_text, worktree.curses
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
                    raise worktree.curses.error()
                return self.keys.pop(0)
            def timeout(self, ms):
                pass
        unget = lambda screen: (lambda char: screen.keys.insert(0, char))
        with patch.dict(worktree.STATE, {"session": "session:1"}):
            screen = Screen(["\x1b", "[", "I"])
            self.assertIsNone(worktree.read_key(screen))
            screen = Screen(["\x1b", "[", "O"])
            with patch.object(worktree, "picker_focused", return_value=True):
                self.assertIsNone(worktree.read_key(screen))  # The whole app lost focus.
            screen = Screen(["\x1b", "[", "O"])
            with patch.object(worktree, "picker_focused", return_value=False), self.assertRaises(worktree.FocusLost):
                worktree.read_key(screen)
            screen = Screen(["\x1b"])
            self.assertEqual(worktree.read_key(screen), "\x1b")
            screen = Screen(["\x1b", "x"])
            with patch.object(worktree.curses, "unget_wch", unget(screen)):
                self.assertEqual(worktree.read_key(screen), "\x1b")
                self.assertEqual(worktree.read_key(screen), "x")

    def test_tracking_labels(self):
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.repo, "branch", "--set-upstream-to=origin/main", "main")
        git(self.repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "ahead")
        git(self.repo, "branch", "deleted")
        git(self.repo, "config", "branch.deleted.remote", "origin")
        git(self.repo, "config", "branch.deleted.merge", "refs/heads/deleted")
        tracks = worktree.tracking(str(self.repo))
        self.assertEqual(tracks["refs/heads/main"], ["1 ahead"])
        self.assertEqual(tracks["refs/heads/deleted"], ["remote deleted"])
        entry = {"worktree": str(self.repo), "main": True, "open": True, "dirty": True, "track": ["1 ahead", "2 behind"]}
        labels = [t for t, _ in worktree.badges(entry)]
        self.assertEqual([t[2:] for t in labels], ["1 ahead", "2 behind", "main checkout", "tab open", "uncommitted"])
        self.assertEqual(worktree.badges_width(entry), len(" · ".join(labels)))

    def test_columns_align_and_hide_empty_ones(self):
        entries = [{"name": "main", "worktree": "/w/repo", "main": True, "open": True, "dirty": False, "track": []},
                   {"name": "a-very-long-branch-name-that-goes-past-forty-characters", "worktree": "/w/x",
                    "open": False, "dirty": True, "track": ["2 behind"]}]
        widths = worktree.column_widths(entries)
        self.assertEqual(widths["PR"], 0)
        self.assertEqual(widths["BRANCH"], 40)
        self.assertEqual(widths["SYNC"], len("x 2 behind"))
        rows = [worktree.entry_row(e, False, widths) for e in entries]
        text = ["".join(t for t, _ in row) for row in rows]
        header = "".join(t for t, _ in worktree.header_row(widths))
        self.assertNotIn("PR", header)
        self.assertTrue(text[1][4:].startswith("a-very-long-branch-name-that-goes-past-…"))
        # Every column starts at the same position in each row and in the header.
        for label in ("SYNC", "STATE"):
            self.assertIn(label, header)
        self.assertEqual(text[0].index(worktree.ICONS["main"]), text[1].index(worktree.ICONS["dirty"]))
        self.assertEqual(header.index("STATE"), text[0].index(worktree.ICONS["main"]))

    def test_default_branch_follows_the_remote(self):
        cwd = str(self.repo)
        self.assertEqual(worktree.default_branch(cwd), "main")
        git(self.repo, "remote", "add", "upstream", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/upstream/trunk", "HEAD")
        git(self.repo, "symbolic-ref", "refs/remotes/upstream/HEAD", "refs/remotes/upstream/trunk")
        self.assertEqual(worktree.default_branch(cwd), "upstream/trunk")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
        self.assertEqual(worktree.default_branch(cwd), "origin/develop")

    def test_source_label(self):
        self.assertEqual(worktree.source_label({"kind": "local", "name": "a", "ref": "a"}), "local")
        self.assertEqual(worktree.source_label({"kind": "remote", "name": "feat/a", "ref": "origin/feat/a"}), "remote")
        self.assertEqual(worktree.source_label({"kind": "remote", "name": "a", "ref": "upstream/a"}), "upstream")

    def test_vim_motions(self):
        motion = worktree.motion
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
                normalized = worktree.normalize_branch(source)
                self.assertEqual(normalized, expected)
                if normalized:
                    git(self.repo, "check-ref-format", "--branch", normalized)

    def setup_branches(self):
        git(self.repo, "branch", "existing")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
        cwd = str(self.repo)
        return cwd, worktree.load_branches(cwd, worktree.worktrees(cwd))

    def suggestion(self, branches, ref):
        return next(s for s in branches["suggestions"] if s["ref"] == ref)

    def test_plan_new_and_existing_branches(self):
        cwd, branches = self.setup_branches()
        new = worktree.plan_branch(cwd, branches, "test branch")
        self.assertEqual((new["branch"], new["action"]), ("test-branch", "create new branch test-branch from main"))
        self.assertNotIn("error", new)
        local = worktree.plan_branch(cwd, branches, "", self.suggestion(branches, "existing"))
        self.assertEqual(local["action"], "check out existing branch existing")
        remote = worktree.plan_branch(cwd, branches, "", self.suggestion(branches, "origin/remote-only"))
        self.assertEqual(remote["action"], "check out origin/remote-only as local branch remote-only, tracking it")
        # A typed name only ever creates a new branch; existing ones are picked from the list.
        self.assertIn("Already checked out", worktree.plan_branch(cwd, branches, "main")["error"])
        self.assertIn("select it under Existing branches", worktree.plan_branch(cwd, branches, "existing")["error"])
        self.assertIn("origin/remote-only exists", worktree.plan_branch(cwd, branches, "remote-only")["error"])

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
        branches = worktree.load_branches(str(self.repo), worktree.worktrees(str(self.repo)), now)
        self.assertEqual([s["ref"] for s in branches["suggestions"]], ["new-local", "origin/remote-only", "old-local"])

    def test_create_worktree_for_each_plan(self):
        cwd, branches = self.setup_branches()
        plans = [worktree.plan_branch(cwd, branches, "new branch"),
                 worktree.plan_branch(cwd, branches, "", self.suggestion(branches, "existing")),
                 worktree.plan_branch(cwd, branches, "", self.suggestion(branches, "origin/remote-only"))]
        for plan in plans:
            with self.subTest(branch=plan["branch"]):
                worktree.create_worktree(cwd, plan)
                head = git(plan["path"], "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()
                self.assertEqual(head, plan["branch"])
        upstream = git(self.repo, "rev-parse", "--abbrev-ref", "remote-only@{upstream}").stdout.decode().strip()
        self.assertEqual(upstream, "origin/remote-only")
        self.assertIn("Destination already exists", worktree.plan_branch(cwd, {**branches, "checked_out": {}, "local": set()}, "new branch")["error"])

    def test_removal_problems_and_changed_files(self):
        dirty = self.add("dirty", "dirty")
        (dirty / "untracked").write_text("changes")
        clean = self.add("clean", "clean")
        entries = {e.get("branch"): e for e in worktree.worktrees(str(self.repo))}
        main, dirty_entry, clean_entry = (entries["refs/heads/" + b] for b in ("main", "dirty", "clean"))
        main["main"] = True
        self.assertIn("main worktree", worktree.removal_problem(main))
        self.assertIsNone(worktree.removal_problem(dirty_entry))
        self.assertIn("locked", worktree.removal_problem({**clean_entry, "locked": ""}))
        self.assertIn("press p to prune", worktree.removal_problem({**clean_entry, "prunable": ""}))
        self.assertEqual(worktree.changed_files(dirty_entry), ["?? untracked"])
        self.assertEqual(worktree.changed_files(clean_entry), [])
        self.assertTrue(dirty.exists() and clean.exists())

    def test_remove_worktree_then_close_tabs(self):
        other = self.add("clean", "clean")
        entry = worktree.worktrees(str(self.repo))[1]
        windows = [{"window_id": "window:1"}]
        with patch.object(worktree, "close_worktree_tabs") as close:
            worktree.remove_worktree(str(self.repo), "session:1", entry, windows)
        self.assertFalse(other.exists())
        close.assert_called_once_with("session:1", windows)
        git(self.repo, "rev-parse", "--verify", "refs/heads/clean")

    def test_failed_git_removal_leaves_tabs_open(self):
        other = self.add("dirty", "dirty")
        (other / "untracked").write_text("changes")
        entry = worktree.worktrees(str(self.repo))[1]
        with patch.object(worktree, "close_worktree_tabs") as close, self.assertRaises(RuntimeError):
            worktree.remove_worktree(str(self.repo), "session:1", entry, [{"window_id": "window:1"}])
        close.assert_not_called()

    def test_force_remove_and_delete_merged_branch(self):
        other = self.add("merged", "merged")
        (other / "untracked").write_text("changes")
        entry = {**worktree.worktrees(str(self.repo))[1], "name": "merged"}
        self.assertEqual(worktree.merged_into(str(self.repo), entry), "main")
        with patch.object(worktree, "close_worktree_tabs"):
            worktree.remove_worktree(str(self.repo), "session:1", entry, [], force=True, delete_branch=True)
        self.assertFalse(other.exists())
        self.assertFalse(subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--verify", "--quiet", "refs/heads/merged"]).returncode == 0)

    def test_unmerged_branch_is_not_offered_for_deletion(self):
        other = self.add("ahead", "ahead")
        git(other, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "ahead")
        entry = {**worktree.worktrees(str(self.repo))[1], "name": "ahead"}
        self.assertIsNone(worktree.merged_into(str(self.repo), entry))

    def test_prune_missing_worktree(self):
        other = self.add("gone", "gone")
        subprocess.run(["rm", "-rf", str(other)], check=True)
        stale = [e for e in worktree.worktrees(str(self.repo)) if "prunable" in e]
        self.assertEqual(len(stale), 1)
        worktree.prune_worktrees(str(self.repo))
        self.assertEqual(len(worktree.worktrees(str(self.repo))), 1)
        git(self.repo, "rev-parse", "--verify", "refs/heads/gone")

    def test_recent_commits_prefers_commits_not_in_base(self):
        description, commits = worktree.recent_commits(str(self.repo))
        self.assertEqual((description, [c[1] for c in commits]), ("Recent commits", ["initial"]))
        self.assertEqual(commits[0][2], "Test")
        other = self.add("feature", "feature")
        for message in ("one", "two"):
            git(other, "-c", "user.name=Dev", "-c", "user.email=d@e", "commit", "-q", "--allow-empty", "-m", message)
        description, commits = worktree.recent_commits(str(other), base="main")
        self.assertEqual(description, "2 commits not in main")
        self.assertEqual([(c[1], c[2]) for c in commits], [("two", "Dev"), ("one", "Dev")])
        self.assertEqual(worktree.recent_commits(str(self.repo / "missing")), ("", []))

    def test_ages(self):
        now = 1_000_000_000
        cases = {now: "now", now - 120: "2m", now - 3 * 3600: "3h", now - 2 * 86400: "2d", now - 15 * 86400: "2w",
                 now - 70 * 86400: "2mo", now - 400 * 86400: "1y", None: ""}
        for stamp, expected in cases.items():
            self.assertEqual(worktree.short_age(stamp, now), expected)
        head = git(self.repo, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertIn(head, worktree.commit_times(str(self.repo), [head]))
        self.assertEqual(worktree.base_entries(str(self.repo))[0]["time"], worktree.commit_times(str(self.repo), [head])[head])

    def test_pull_requests_keep_newest_per_branch(self):
        mine = b'[{"number": 5, "state": "MERGED", "headRefName": "a", "isDraft": false}]'
        everyone = b'[{"number": 9, "state": "OPEN", "headRefName": "a", "isDraft": true}, {"number": 7, "state": "CLOSED", "headRefName": "b", "isDraft": false}]'
        with patch.object(worktree, "run", side_effect=[mine, everyone]):
            prs = worktree.pull_requests("/repo")
        self.assertEqual({k: v["number"] for k, v in prs.items()}, {"a": 9, "b": 7})
        labels = [t for t, _ in worktree.badges({"worktree": "/nonexistent-but-ok", "pr": prs["a"], "dirty": False, "open": False})]
        self.assertIn(worktree.ICONS["pr"] + " #9 draft", labels)
        with patch.object(worktree, "run", side_effect=RuntimeError("not a GitHub repo")):
            self.assertEqual(worktree.pull_requests("/repo"), {})

    def test_open_in_background_and_split(self):
        with patch.object(worktree, "windows_for_path", return_value=[]), patch.object(worktree, "repo_name", return_value="repo"), \
                patch.object(worktree, "run", return_value=b'{"window_id": "window:bg"}') as run, patch.dict(worktree.STATE, {"navigated": False}):
            self.assertEqual(worktree.open_tab("session:1", {"worktree": "/w/t", "branch": "refs/heads/t"}, focus=False), "window:bg")
            run.assert_called_once_with("rex", "window", "new", "-s", "session:1", "repo - t", "-c", "/w/t", "--focus=false", "--json")
            self.assertFalse(worktree.STATE["navigated"])
        with patch.object(worktree, "windows_for_path", return_value=[{"window_id": "window:1"}]), patch.object(worktree, "run") as run:
            self.assertIsNone(worktree.open_tab("session:1", {"worktree": "/w/t"}, focus=False))
            run.assert_not_called()
        with patch.object(worktree, "run") as run, patch.dict(worktree.STATE, {"navigated": False}):
            worktree.open_split("session:1", {"worktree": "/w/t", "branch": "refs/heads/t"}, "block:origin")
            run.assert_called_once_with("rex", "split", "-s", "session:1", "-b", "block:origin", "--split=right", "-c", "/w/t", "--label", "t")
            self.assertTrue(worktree.STATE["navigated"])
        with self.assertRaises(RuntimeError):
            worktree.open_split("session:1", {"worktree": "/w/t"}, "")

    def test_fuzzy_filter_ranks_and_highlights(self):
        entries = [{"name": "fix/login-bug", "worktree": "/w/a"}, {"name": "feature/login", "worktree": "/w/b"},
                   {"name": "main", "worktree": "/w/c"}]
        ranked = [{"name": "l-x-o-x-g", "worktree": "/w/d"}, {"name": "logs", "worktree": "/w/e"}]
        self.assertEqual([e["name"] for e in worktree.filter_entries(ranked, "log")], ["logs", "l-x-o-x-g"])
        self.assertEqual([e["name"] for e in worktree.filter_entries(entries, "flog")], ["fix/login-bug", "feature/login"])
        self.assertEqual(worktree.filter_entries(entries, "zzz"), [])
        score, name_hits, path_hits = worktree.match_entry(entries[2], "mn")
        self.assertEqual(name_hits, {0, 3})
        self.assertIsNotNone(worktree.match_entry(entries[2], "w/c"))  # Matches the full path...
        self.assertEqual(worktree.match_entry({"name": "x", "worktree": "/w/sinch-email"}, "inch")[2], {1, 2, 3, 4})  # ...highlights the folder.

    def test_worktree_sources(self):
        home = str(worktree.Path.home())
        cases = {home + "/.herdr/worktrees/r/a": "herdr", home + "/.t3/worktrees/r/a": "t3",
                 home + "/.local/share/rex/worktrees/r/a": "rex", "/repo/.claude/worktrees/a": "claude", "/repo-a": ""}
        for path, source in cases.items():
            self.assertEqual(worktree.worktree_source({"worktree": path}), source)

    def test_new_branch_from_default_base_does_not_track(self):
        git(self.repo, "checkout", "-q", "-b", "feature")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "feature work")
        cwd = str(self.repo)
        branches = worktree.load_branches(cwd, worktree.worktrees(cwd))
        self.assertEqual([b["label"] for b in branches["bases"]], ["main", "feature"])
        plan = worktree.plan_branch(cwd, branches, "from main", None, branches["bases"][0])
        self.assertEqual(plan["action"], "create new branch from-main from main")
        worktree.create_worktree(cwd, plan)
        self.assertEqual(git(cwd, "rev-parse", "from-main").stdout, git(cwd, "rev-parse", "main").stdout)
        upstream = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "from-main@{upstream}"], capture_output=True)
        self.assertNotEqual(upstream.returncode, 0)

    def test_setup_command_types_into_new_tab(self):
        git(self.repo, "config", "rex.worktreeSetup", "make setup")
        self.assertEqual(worktree.setup_command(str(self.repo)), "make setup")
        with patch.object(worktree, "windows_for_path", return_value=[]), patch.object(worktree, "repo_name", return_value="repo"), \
                patch.object(worktree, "run", return_value=b'{"window_id": "window:new"}') as run:
            worktree.open_tab("session:1", {"worktree": "/tmp/test", "branch": "refs/heads/topic"}, None, "make setup")
        args = run.call_args_list[0][0]
        self.assertEqual(args[:6], ("rex", "api", "call", "-s", "session:1", "session.new_window"))
        options = json.loads(args[6])["layout"]["block"]["options"]
        self.assertEqual(options, {"cwd": "/tmp/test", "initial_input": "make setup\n"})

    def test_close_picker_window_last(self):
        own = {"window_id": "window:own", "layers": [{"blocks": [{"block_id": "block:picker"}]}]}
        other = {"window_id": "window:other", "layers": []}
        with patch.dict(os.environ, {"REX_BLOCK": "block:picker"}), patch.object(worktree, "run") as run:
            worktree.close_worktree_tabs("session:1", [own, other])
            self.assertEqual(run.call_args_list, [
                call("rex", "window", "close", "-s", "session:1", "window:other"),
                call("rex", "window", "close", "-s", "session:1", "window:own"),
            ])

    def test_windows_for_path_matches_nested_paths_once(self):
        window, other = {"window_id": "window:1"}, {"window_id": "window:2"}
        terminals = [(window, "/w/repo/src"), (window, "/w/repo"), (other, "/w/repository")]
        self.assertEqual(worktree.windows_for_path("session:1", "/w/repo", terminals), [window])

    def test_restore_focus_only_when_no_tab_was_opened(self):
        with patch.dict(worktree.STATE, {"navigated": False}), patch.object(worktree, "run") as run:
            worktree.restore_focus("session:1", "block:origin")
            run.assert_called_once_with("rex", "focus", "-s", "session:1", "block:origin")
        with patch.dict(worktree.STATE, {"navigated": True}), patch.object(worktree, "run") as run:
            worktree.restore_focus("session:1", "block:origin")
            run.assert_not_called()
        with patch.dict(worktree.STATE, {"navigated": False}), patch.object(worktree, "run", side_effect=RuntimeError("gone")):
            worktree.restore_focus("session:1", "block:closed")

    def test_open_focuses_existing_window(self):
        with patch.object(worktree, "windows_for_path", return_value=[{"window_id": "window:1"}]), patch.object(worktree, "run") as run:
            self.assertEqual(worktree.open_tab("session:1", {"worktree": "/tmp/test", "branch": "refs/heads/topic"}), "window:1")
            run.assert_called_once_with("rex", "focus", "-s", "session:1", "window:1")

    def test_open_creates_tab_and_shows_it_in_app(self):
        with patch.object(worktree, "windows_for_path", return_value=[]), patch.object(worktree, "repo_name", return_value="dotfiles"), patch.object(worktree, "run", return_value=b'{"window_id": "window:new"}') as run:
            worktree.open_tab("session:1", {"worktree": "/tmp/test", "branch": "refs/heads/topic"}, "client:app")
            self.assertEqual(run.call_args_list, [
                call("rex", "window", "new", "-s", "session:1", "dotfiles - topic", "-c", "/tmp/test", "--json"),
                call("rex", "-C", "client:app", "do", "session.select", "session_id=session:1", "window_id=window:new"),
            ])

    def test_open_reports_app_switch_failure(self):
        def fake_run(*args):
            if "-C" in args:
                raise RuntimeError("Turn on Remote Control")
            return b"{}"
        with patch.object(worktree, "windows_for_path", return_value=[{"window_id": "window:1"}]), patch.object(worktree, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "Couldn't switch to the tab: Turn on Remote Control"):
                worktree.open_tab("session:1", {"worktree": "/tmp/test"}, "client:app")

    def test_repo_name_comes_from_main_worktree(self):
        other = self.add("elsewhere", "topic")
        self.assertEqual(worktree.repo_name(str(other)), "repo with spaces")
        self.assertEqual(worktree.tab_label({"worktree": str(other), "branch": "refs/heads/topic"}), "repo with spaces - topic")


if __name__ == "__main__":
    unittest.main()
