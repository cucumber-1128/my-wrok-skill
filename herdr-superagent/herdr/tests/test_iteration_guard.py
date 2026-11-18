"""Iteration policy handoff and local workspace guards. No external platform calls."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from sa_runtime import Runtime
from sa_state import StateError, Store
from test_runtime import FakeHc
import sa_workspace


class PolicyStarts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="herdr-policy-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "state")
        self.hc = FakeHc()
        self.runtime = Runtime(self.store, self.hc)
        env = patch.dict(os.environ, {"HC_TERMINAL_ID": ""})
        env.start()
        self.addCleanup(env.stop)

    def spawn(self, role, no_wait=False):
        self.runtime.spawn(role, role, self.root, no_wait=no_wait, ready_timeout=0)
        request = self.store.agent(role)["request"]
        return request, Path(request["context_file"])

    def test_legacy_start_and_coordinator_receive_the_same_iteration_authority(self):
        for role in ("reviewer", "leader"):
            with self.subTest(role=role):
                request, context = self.spawn(role)
                self.assertEqual(SCRIPTS.parent / "references" / "iteration-policy.md",
                                 Path(request["iteration_policy_file"]))
                self.assertEqual(request["iteration_policy_file"],
                                 json.loads(context.read_text("utf-8"))["iteration_policy_file"])
                msg = self.hc.calls[-1][2]
                self.assertIn("qe CLI only", msg)
                self.assertIn("never create a branch or MR directly", msg)
                self.assertIn("req-closeloop", msg)
                self.assertIn("bug-closeloop", msg)
                self.assertIn("user's explicit confirmation" if role == "leader" else
                              "to your coordinator", msg)

    def test_preupgrade_pending_context_is_upgraded_before_send_and_never_resent(self):
        request, context = self.spawn("scout", no_wait=True)
        request.pop("iteration_policy_file", None)
        self.store.update_agent("scout", request=request)
        context.write_text(json.dumps(request), encoding="utf-8")
        self.runtime.resume_start("scout")
        updated = context.read_bytes()
        self.assertEqual(SCRIPTS.parent / "references" / "iteration-policy.md",
                         Path(json.loads(updated)["iteration_policy_file"]))
        self.assertIn("context.iteration_policy_file", self.hc.calls[-1][2])
        self.runtime.resume_start("scout")
        self.assertEqual(updated, context.read_bytes())
        self.assertEqual(1, sum(call[0] == "send" for call in self.hc.calls))


class NoBranchCreation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="herdr-no-branch-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.branch = "feature/approved"
        self.git("init", "-b", "main")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    def prepare(self):
        return sa_workspace.prepare_workspace(self.repo, self.branch, "ticket", self.root / "state",
                                              destination=self.root / "worker")

    def test_indirect_branch_never_attaches_the_main_checkout_branch(self):
        self.git("symbolic-ref", f"refs/heads/{self.branch}", "refs/heads/main")
        with self.assertRaisesRegex(StateError, "indirect|symbolic"):
            self.prepare()
        self.assertFalse((self.root / "worker").exists())
        self.assertEqual("main", self.git("branch", "--show-current"))

    def test_local_branch_deleted_after_validation_is_not_recreated_from_remote(self):
        self.git("branch", self.branch)
        self.git("remote", "add", "origin", str(self.root / "private-nonexistent"))
        self.git("update-ref", f"refs/remotes/origin/{self.branch}", "HEAD")
        original = sa_workspace.git
        def expire_before_add(cwd, *args, **kwargs):
            if args[:2] == ("worktree", "add"):
                self.git("branch", "-D", self.branch)
            return original(cwd, *args, **kwargs)
        with patch.object(sa_workspace, "git", side_effect=expire_before_add):
            with self.assertRaises(StateError):
                self.prepare()
        self.assertEqual("", self.git("branch", "--list", self.branch))
        self.assertEqual("main", self.git("branch", "--show-current"))
        self.assertFalse((self.root / "worker").exists())

    def test_failed_switch_preserves_new_user_files(self):
        self.git("branch", self.branch)
        self.git("worktree", "add", str(self.root / "already-occupied"), self.branch)
        original = sa_workspace.git
        def write_before_switch(cwd, *args, **kwargs):
            if args[:1] == ("switch",):
                (Path(cwd) / "keep.txt").write_text("mine", encoding="utf-8")
            return original(cwd, *args, **kwargs)
        with patch.object(sa_workspace, "git", side_effect=write_before_switch):
            with self.assertRaisesRegex(StateError, "preserved at"):
                self.prepare()
        self.assertEqual("mine", (self.root / "worker" / "keep.txt").read_text("utf-8"))
