import importlib.util
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

    def test_filter_matches_all_tokens_case_insensitively(self):
        entries = [{"name": "feature/Login", "worktree": "/w/login"}, {"name": "fix/search", "worktree": "/w/search"}]
        self.assertEqual(worktree.filter_entries(entries, "FEAT log"), entries[:1])
        self.assertEqual(worktree.filter_entries(entries, "w/sea"), entries[1:])
        self.assertEqual(worktree.filter_entries(entries, ""), entries)

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

    def test_plan_new_existing_remote_and_checked_out_branches(self):
        git(self.repo, "branch", "existing")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
        branches = worktree.load_branches(str(self.repo), worktree.worktrees(str(self.repo)))
        cwd = str(self.repo)

        new = worktree.plan_branch(cwd, branches, "test branch")
        self.assertEqual((new["branch"], new["summary"]), ("test-branch", "new branch from main"))
        self.assertNotIn("error", new)
        self.assertEqual(worktree.plan_branch(cwd, branches, "existing")["summary"], "existing branch")
        self.assertEqual(worktree.plan_branch(cwd, branches, "remote-only")["summary"], "track origin/remote-only")
        self.assertIn("Already checked out", worktree.plan_branch(cwd, branches, "main")["error"])
        self.assertEqual({s["ref"] for s in branches["suggestions"]}, {"existing", "origin/remote-only"})

    def test_create_worktree_for_each_plan(self):
        git(self.repo, "branch", "existing")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        git(self.repo, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
        cwd = str(self.repo)
        branches = worktree.load_branches(cwd, worktree.worktrees(cwd))
        for text in ("new branch", "existing", "remote-only"):
            with self.subTest(text=text):
                plan = worktree.plan_branch(cwd, branches, text)
                worktree.create_worktree(cwd, plan)
                head = git(plan["path"], "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()
                self.assertEqual(head, plan["branch"])
        upstream = git(self.repo, "rev-parse", "--abbrev-ref", "remote-only@{upstream}").stdout.decode().strip()
        self.assertEqual(upstream, "origin/remote-only")
        self.assertIn("Destination already exists", worktree.plan_branch(cwd, {**branches, "checked_out": {}, "local": set()}, "new branch")["error"])

    def test_removal_problems(self):
        dirty = self.add("dirty", "dirty")
        (dirty / "untracked").write_text("changes")
        clean = self.add("clean", "clean")
        entries = {e.get("branch"): e for e in worktree.worktrees(str(self.repo))}
        main, dirty_entry, clean_entry = (entries["refs/heads/" + b] for b in ("main", "dirty", "clean"))
        main["main"] = True
        self.assertIn("main worktree", worktree.removal_problem(main))
        self.assertIn("uncommitted or untracked", worktree.removal_problem(dirty_entry))
        self.assertIsNone(worktree.removal_problem(clean_entry))
        self.assertIn("locked", worktree.removal_problem({**clean_entry, "locked": ""}))
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
