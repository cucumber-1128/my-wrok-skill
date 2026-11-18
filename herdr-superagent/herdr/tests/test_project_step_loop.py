"""A local coordinator, two separate worktrees, and an early, evidenced handoff."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sa_project import load_assignment
from sa_runtime import Runtime
from sa_state import StateError, Store
from sa_watchdog import Driver
from test_runtime import FakeHc


class StageLoop(unittest.TestCase):
    def test_project_owner_returns_stage_with_open_task_and_driver_delivers_only_to_parent(self):
        with tempfile.TemporaryDirectory(prefix="herdr-project-step-") as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            rules = project / "rules.md"
            rules.write_text("Read the report and stop before release.", encoding="utf-8")
            report = project / "verification.md"
            report.write_text("Front end ready for review; integration run not requested.", encoding="utf-8")
            repos = {}
            for key in ("api", "web"):
                repo, wt = project / key, project / (key + "-task")
                repo.mkdir()
                def git(*args):
                    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                                          text=True, encoding="utf-8", timeout=20).stdout.strip()
                git("init", "-b", "main")
                git("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "base")
                git("worktree", "add", "-b", "approved", str(wt))
                repos[key] = (wt, git("rev-parse", "HEAD"))
            manifest = root / "assignment.json"
            manifest.write_text(json.dumps({
                "project_root": str(project), "project_ref": "project:37", "skill_entry": rules.name,
                "mode": "project_step", "stop_at": "Return before release",
                "workspaces": {k: str(v[0]) for k, v in repos.items()}, "evidence": [str(rules)],
            }), encoding="utf-8")
            tid, router = "project:37", "router-fixture"
            store, hc = Store(root / "state"), FakeHc()
            rt = Runtime(store, hc=hc)
            hc.add(router)
            store.create_thread(tid, project, parent=router, assignment=load_assignment(manifest))
            leader = rt.spawn("leader", "lead", project, thread_id=tid, parent=router)
            store.ack_agent("lead", leader["address"])
            for key, (wt, sha) in repos.items():
                store.create_task(tid, key, sha, workspace=key)
                worker = rt.spawn("dev-worker", key, wt, task="Follow the project step",
                                  thread_id=tid, parent=leader["address"], task_id=key)
                store.ack_agent(key, worker["address"])
            store.report(tid, "web", 1, 1, repos["web"][1], "DONE", summary="available for review")
            store.handoff_thread(tid, generation=1, spec_rev=1, summary="Stopped before release",
                                 evidence=[str(report)])
            with self.assertRaises(StateError):
                store.reopen_thread(tid, assignment=load_assignment(manifest))
            hc.calls.clear()
            Driver(rt, router=router).step()
            self.assertEqual([], store.list_agents(active=True))
            self.assertEqual(["PENDING", "DONE"], [t["phase"] for t in store.list_tasks(tid)])
            self.assertCountEqual([router], [args[1] for args in hc.calls if args[0] == "send"])
            notice, = store.list_events(kind="THREAD_HANDOFF")
            self.assertEqual("AWAITING_ACK", notice["status"])
            self.assertEqual("HANDOFF", notice["payload"]["status"])
            self.assertTrue(store.claim_event(notice["id"], router)["claimed"])
            store.complete_event(notice["id"], router)
            self.assertEqual([], store.list_events(kind="THREAD_DONE"))
            store.reopen_thread(tid, assignment=load_assignment(manifest))
            self.assertEqual([], store.list_tasks(tid))
