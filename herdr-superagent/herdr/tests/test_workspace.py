import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sa_runtime import Runtime
from sa_state import StateError, Store
from sa_workspace import prepare_workspace, snapshot_revision, validate_isolation
from test_runtime import FakeHc


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="herdr-git-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Herdr test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "app.txt").write_text("baseline\n", encoding="utf-8")
        self.git("add", "app.txt")
        self.git("-c", "commit.gpgsign=false", "commit", "-m", "fixture")
        self.git("branch", "feature/approved")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def prepare(self, **kwargs):
        return prepare_workspace(
            self.repo, "feature/approved", "gitlab:project:mr:42",
            self.root / "state", **kwargs,
        )

    def test_prepares_and_reuses_approved_branch_without_changing_main_checkout(self):
        prepared = self.prepare()
        path = Path(prepared["cwd"])
        self.assertNotEqual(self.repo, path)
        self.assertEqual("main", self.git("branch", "--show-current"))
        self.assertEqual("feature/approved", self.git("branch", "--show-current", cwd=path))
        self.assertTrue(validate_isolation(path)["isolated"])
        self.assertEqual(str(path), self.prepare()["cwd"])
        self.assertEqual(prepared["head"], snapshot_revision(path))

    def test_dirty_main_checkout_is_preserved_while_preparing_an_isolated_branch(self):
        user_file = self.repo / "keep-user-work.txt"
        user_file.write_text("unrelated work", encoding="utf-8")
        (self.repo / "app.txt").write_text("unfinished user change", encoding="utf-8")
        before = self.git("status", "--porcelain=v1")
        prepared = self.prepare()
        self.assertEqual(before, self.git("status", "--porcelain=v1"))
        self.assertEqual("unrelated work", user_file.read_text("utf-8"))
        self.assertEqual("unfinished user change", (self.repo / "app.txt").read_text("utf-8"))
        self.assertEqual("main", self.git("branch", "--show-current"))
        self.assertEqual("baseline\n", (Path(prepared["cwd"]) / "app.txt").read_text("utf-8"))
        self.assertEqual(prepared["head"], snapshot_revision(prepared["cwd"]))

    def test_missing_platform_branch_is_not_silently_created(self):
        with self.assertRaisesRegex(StateError, "approved|existing"):
            prepare_workspace(self.repo, "feature/missing", "manual:project:new", self.root / "state")
        self.assertNotIn("feature/missing", self.git("branch", "--list"))

    def test_normal_main_checkout_fails_isolation_validation(self):
        with self.assertRaisesRegex(StateError, "isolated"):
            validate_isolation(self.repo)

    def test_snapshot_rejects_uncommitted_modifications(self):
        prepared = self.prepare()
        (Path(prepared["cwd"]) / "app.txt").write_text("uncommitted", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "dirty"):
            snapshot_revision(prepared["cwd"])

    def test_existing_nonworktree_destination_is_preserved(self):
        destination = self.root / "existing"
        destination.mkdir()
        keep = destination / "keep.txt"
        keep.write_text("mine", encoding="utf-8")
        with self.assertRaises(StateError):
            self.prepare(destination=destination)
        self.assertEqual("mine", keep.read_text("utf-8"))

    def test_two_subdirectories_of_the_same_worktree_share_a_writer_reservation(self):
        prepared = self.prepare()
        cwd = Path(prepared["cwd"])
        (cwd / "src").mkdir()
        (cwd / "tests").mkdir()
        roles = self.root / "roles"
        roles.mkdir()
        (roles / "dev-worker.md").write_text("---\ncommand: claude\n---\nDeveloper.", encoding="utf-8")
        runtime = Runtime(Store(self.root / "runtime-state"), hc=FakeHc())
        runtime.spawn("dev-worker", "dev-a", cwd / "src", role_dir=roles, no_wait=True)
        with self.assertRaisesRegex(StateError, "workspace"):
            runtime.spawn("dev-worker", "dev-b", cwd / "tests", role_dir=roles, no_wait=True)


if __name__ == "__main__":
    unittest.main()
