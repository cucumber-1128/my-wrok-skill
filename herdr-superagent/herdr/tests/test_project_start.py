import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sa_project import load_assignment
from sa_runtime import Runtime
from sa_state import StateError, Store
from test_runtime import FakeHc


class ProjectStarts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="herdr-project-start-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "工程规则"
        self.project.mkdir()
        self.entry = self.project / "stage.md"
        self.entry.write_text("Use the existing tasks and stop for approval.", encoding="utf-8")
        self.report = self.project / "review.md"
        self.report.write_text("May commit; 16 of 18 checks executed.", encoding="utf-8")
        self.path = self.root / "stage.json"
        self.store = Store(self.root / "state")
        self.hc = FakeHc()
        self.runtime = Runtime(self.store, self.hc)
        self.tid = "test:project:0000042"
        env = patch.dict(os.environ, {"HC_TERMINAL_ID": ""})
        env.start()
        self.addCleanup(env.stop)

    def manifest(self, mode="read_only", **changes):
        data = dict(project_root=self.project.name, skill_entry=self.entry.name, mode=mode,
                    project_ref="0000042", stop_at="可提交；不要提交", evidence=[self.report.name],
                    workspaces={"frontend": "archived/前端"})
        data.update(changes)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return load_assignment(self.path)

    def bind(self, assignment):
        return self.store.create_thread(self.tid, self.project, assignment=assignment, parent="parent")

    def start(self, role, cwd=None, task_id="", **kwargs):
        return self.runtime.spawn(role, kwargs.pop("name", role), cwd or self.project,
                                  ready_timeout=0, task_id=task_id, thread_id=self.tid, **kwargs)

    def git_worktree(self):
        main = self.root / "main"
        work = self.root / "mapped" / "backend"
        main.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(main), *args], capture_output=True,
                                  check=True, text=True).stdout.strip()
        git("init", "-b", "main")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture")
        git("worktree", "add", "-b", "feature/approved", str(work))
        return main, work, git("rev-parse", "HEAD")

    def test_paths_resolve_from_manifest_and_project_not_caller_cwd(self):
        data = self.manifest()
        self.assertEqual(self.project, Path(data["project_root"]))
        self.assertEqual(self.entry, Path(data["skill_entry"]))
        self.assertEqual([self.report], [Path(p) for p in data["evidence"]])
        self.assertEqual(self.project / "archived/前端", Path(data["workspaces"]["frontend"]))
        for changes in ({"skill_entry": "../wrong.md"}, {"evidence": ["unknown.log"]},
                        {"mode": "project_step"}, {"stop_at": ""}, {"workspaces": []}):
            with self.subTest(changes=changes), self.assertRaises(StateError):
                self.manifest(**changes)
        with self.assertRaisesRegex(StateError, "JSON"):
            self.path.write_text("{", encoding="utf-8")
            load_assignment(self.path)

    def test_readonly_supports_archived_mappings_and_no_execution_roles(self):
        data = self.manifest()
        self.bind(data)
        for role in ("dev-worker", "implementer", "simplifier", "test-worker", "tester"):
            with self.subTest(role=role), self.assertRaisesRegex(StateError, "read_only"):
                self.start(role)
        self.assertEqual([], self.hc.calls)
        leader = self.start("leader")
        row = self.store.agent("leader")
        context = json.loads(Path(row["request"]["context_file"]).read_text(encoding="utf-8"))
        self.assertEqual(data, context["assignment"])
        self.assertEqual(["frontend"], context["unavailable_workspaces"])
        self.assertEqual(self.project, Path(context["cwd"]))
        self.assertTrue(self.store.ack_agent("leader", leader["address"])["accepted"])
        msg = next(c[2] for c in self.hc.calls if c[0] == "send")
        self.assertIn("Read existing records only", msg)
        self.assertNotIn("do not launch subagents", msg)
        self.assertIn("manual-only", msg)
        run = next(c for c in self.hc.calls if c[0] == "run")
        self.assertNotIn(str(self.project), run[run.index("--"):])

    def test_project_coordinator_inherits_scope_and_leaf_cannot_subdelegate(self):
        self.bind(self.manifest())
        leader = self.start("leader")
        self.store.ack_agent("leader", leader["address"])
        with patch.dict(os.environ, {"HC_TERMINAL_ID": leader["address"]}):
            with self.assertRaisesRegex(StateError, "read_only"):
                self.runtime.spawn("tester", "tester", self.project, parent="other")
            with self.assertRaisesRegex(StateError, "different thread"):
                self.runtime.spawn("reviewer", "reviewer", self.project, thread_id="other")
            result = self.runtime.spawn("scout", "leaf", self.project, ready_timeout=0)
        self.assertEqual(self.tid, self.store.agent("leaf")["thread_id"])
        self.assertEqual("read_only", self.store.agent("leaf")["request"]["assignment"]["mode"])
        self.assertIn("do not launch subagents", self.hc.calls[-1][2])
        self.store.ack_agent("leaf", result["address"])
        with patch.dict(os.environ, {"HC_TERMINAL_ID": result["address"]}):
            with self.assertRaisesRegex(StateError, "coordinator"):
                self.runtime.spawn("dev-worker", "undisclosed", self.project, parent="other")
        self.assertEqual(2, sum(c[0] == "run" for c in self.hc.calls))

    def test_project_writer_requires_actual_mapped_worktree_and_original_task(self):
        main, work, head = self.git_worktree()
        (work / "sub").mkdir()
        data = self.manifest(mode="project_step", workspaces={"backend": str(work), "old": str(main)})
        self.bind(data)
        self.store.create_task(self.tid, "T-01", head, workspace="backend")
        self.store.create_task(self.tid, "T-02", head, workspace="old")
        for cwd, task in ((work, ""), (self.project, "T-01"), (main, "T-01"),
                          (work / "sub", "T-01"), (main, "T-02")):
            with self.subTest(cwd=cwd, task=task), self.assertRaises(StateError):
                self.start("dev-worker", cwd, task)
        self.assertFalse(self.hc.calls)
        result = self.start("dev-worker", work, "T-01")
        self.assertTrue(self.store.ack_agent("dev-worker", result["address"])["accepted"])
        request = self.store.agent("dev-worker")["request"]
        context = json.loads(Path(request["context_file"]).read_text(encoding="utf-8"))
        self.assertEqual(data, context["assignment"])
        self.assertEqual("backend", context["workspace_key"])
        self.assertEqual("T-01", context["task_id"])
        self.assertEqual(head, context["code_revision"])
        self.assertEqual(work, Path(context["cwd"]))
        run = next(c for c in self.hc.calls if c[0] == "run")
        self.assertEqual(work, Path(run[run.index("--cwd") + 1]))
        self.assertNotIn(str(self.project), run[run.index("--"):])

    def test_released_project_caller_address_does_not_capture_legacy_start(self):
        self.bind(self.manifest())
        old = self.start("scout")
        self.runtime.stop_agent("scout")
        with patch.dict(os.environ, {"HC_TERMINAL_ID": old["address"]}):
            result = self.runtime.spawn("tester", "standalone", self.project, ready_timeout=0)
        self.assertEqual("READY", result["status"])
        record = self.store.agent("standalone")
        self.assertEqual("", record["thread_id"])
        self.assertNotIn("assignment", record["request"])

    def test_missing_entry_before_resume_or_ack_cannot_start_project_work(self):
        self.bind(self.manifest())
        self.start("scout", no_wait=True)
        held = self.project / "moved.md"
        self.entry.rename(held)
        with self.assertRaisesRegex(StateError, "skill_entry"):
            self.runtime.resume_start("scout", ready_timeout=0)
        self.assertFalse(any(c[0] == "send" for c in self.hc.calls))
        held.rename(self.entry)
        result = self.runtime.resume_start("scout", ready_timeout=0)
        self.entry.rename(held)
        with self.assertRaisesRegex(StateError, "skill_entry"):
            self.store.ack_agent("scout", result["address"])

    def test_handoff_routes_stage_conclusion_and_later_external_not_old_results(self):
        main, work, head = self.git_worktree()
        data = self.manifest(mode="project_step", workspaces={"backend": str(work)})
        self.bind(data)
        self.store.assign_owner(self.tid, "old-leader")
        self.store.create_task(self.tid, "T-01", head, workspace="backend")
        self.store.report(self.tid, "T-01", 1, 1, head, "DONE")
        self.store.handoff_thread(self.tid, generation=1, spec_rev=1, summary="可提交",
                                   evidence=[self.report])
        self.store.enqueue_event("external", self.tid, {"feedback": "later"})
        self.hc.add("parent")
        self.hc.add("router")
        self.hc.add("old-leader")
        self.runtime.dispatch()
        sends = [c for c in self.hc.calls if c[0] == "send"]
        self.assertEqual({"parent", "router"}, {c[1] for c in sends})
        self.assertIn("THREAD_HANDOFF", next(c[2] for c in sends if c[1] == "parent"))
        self.assertFalse(any("TASK_RESULT" in c[2] for c in sends))
        self.store.reopen_thread(self.tid, assignment=data)
        self.store.assign_owner(self.tid, "new-leader")
        self.hc.add("new-leader")
        self.hc.calls.clear()
        self.runtime.dispatch()
        self.assertFalse(any(c[0] == "send" for c in self.hc.calls))
