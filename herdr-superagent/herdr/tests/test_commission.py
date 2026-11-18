from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import sa
from sa_state import StateError, Store

REV = "c" * 40


class CommissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="herdr-commission-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.project = self.root / "project"
        self.project.mkdir()
        self.skill = self.project / "AGENTS.md"
        self.skill.write_text("Project rules.", encoding="utf-8")
        self.evidence = self.project / "verify.md"
        self.evidence.write_text("16/18; 2 skipped.", encoding="utf-8")
        self.store = Store(self.state)
        self.id = "project:T-004"
        self.config = self.root / "assignment.json"

    def commission(self, mode="read_only", **changes):
        data = dict(project_root=str(self.project), skill_entry=str(self.skill), project_ref="T-004",
                    mode=mode, stop_at="report current verification without further tests",
                    evidence=[str(self.evidence)], workspaces={})
        data.update(changes)
        return data

    def create(self, assignment=None):
        return self.store.create_thread(self.id, self.project, parent="router", owner="controller",
                                        assignment=assignment if assignment is not None else self.commission())

    def finish(self, **kwargs):
        args = dict(generation=1, spec_rev=1, summary="16/18; 2 skipped", evidence=[self.evidence])
        args.update(kwargs)
        return self.store.handoff_thread(self.id, **args)

    def cli(self, *args):
        return sa.run(sa.parser().parse_args(["--state-dir", str(self.state), *map(str, args)]))

    def test_cli_round_trip_returns_stage_not_requirement_acceptance(self):
        self.config.write_text(json.dumps(self.commission()), encoding="utf-8")
        self.cli("thread", "create", "--id", self.id, "--cwd", self.project, "--parent", "router",
                 "--assignment-file", self.config)
        args = ("thread", "handoff", "--id", self.id, "--generation", 1, "--spec-rev", 1,
                "--summary", "16/18; 2 skipped", "--evidence", self.evidence)
        done = self.cli(*args)
        self.assertEqual(done, self.cli(*args))
        self.assertEqual(done, self.cli("thread", "list")[0])
        self.assertEqual(done, self.cli("thread", "get", "--id", self.id))
        self.assertEqual("HANDOFF", done["status"])
        self.assertEqual("report current verification without further tests", done["handoff"]["stop_at"])
        self.assertEqual([self.evidence], list(map(Path, done["handoff"]["evidence"])))
        self.assertEqual([], self.cli("task", "list", "--thread", self.id))
        (notice,) = self.cli("event", "list")
        self.assertTrue(notice["trusted"])
        self.assertEqual(("THREAD_HANDOFF", "router", done["handoff"]),
                         (notice["kind"], notice["recipient"], notice["payload"]))

    def test_only_real_evidence_and_current_running_assignment_can_handoff(self):
        self.create()
        for extra in (dict(evidence=[self.root / "nonexistent"]), dict(evidence=[]), dict(summary=" "),
                      dict(spec_rev=2), dict(generation=0)):
            with self.subTest(extra=extra), self.assertRaises(StateError):
                self.finish(**extra)
        with self.assertRaises(StateError):
            self.store.set_thread_status(self.id, "HANDOFF")
        with self.assertRaises(StateError):
            self.store.set_thread_status(self.id, "CLOSED")
        self.assertEqual([], self.store.list_events())
        self.store.set_thread_status(self.id, "PAUSED")
        with self.assertRaises(StateError):
            self.finish()
        self.store.set_thread_status(self.id, "RUNNING")
        self.finish()
        with self.assertRaises(StateError):
            self.finish(summary="claim all passed")
        self.assertEqual(1, len(self.store.list_events(kind="THREAD_HANDOFF")))
        with self.assertRaises(StateError):
            self.store.reopen_thread(self.id)
        self.store.set_limits(max_flows=1)
        self.store.create_thread("unrelated:work", self.project)

    def test_readonly_context_forbids_code_progress_mutations(self):
        self.create()
        with self.assertRaisesRegex(StateError, "read_only"):
            self.store.create_task(self.id, "T001", REV)
        with self.assertRaisesRegex(StateError, "read_only"):
            self.store.retry_task(self.id, "T001", REV)
        with self.assertRaisesRegex(StateError, "read_only"):
            self.store.report(self.id, "T001", 1, 1, REV, "BLOCKED", summary="not assigned")
        with self.assertRaisesRegex(StateError, "read_only"):
            self.store.revise_thread(self.id)

    def test_active_write_scopes_use_registered_roots_not_coordinator_cwd(self):
        self.store.set_limits(max_flows=4)
        repo_a, repo_b = self.project / "a", self.project / "b"
        for repo in (repo_a, repo_b):
            repo.mkdir()
        a = self.commission("project_step", workspaces={"repo": str(repo_a)})
        b = self.commission("project_step", workspaces={"repo": str(repo_b)})
        self.create(a)
        self.store.create_thread("other", self.project, assignment=b)
        self.store.create_thread("progress", self.project, assignment=self.commission())
        with self.assertRaisesRegex(StateError, "workspace"):
            self.store.create_thread("overlap", self.project, assignment=a)
        with self.assertRaisesRegex(StateError, "workspace"):
            self.store.create_thread("legacy", repo_a)
        self.finish()
        self.store.create_thread("overlap", self.project, assignment=a)
        with self.assertRaisesRegex(StateError, "workspace"):
            self.store.reopen_thread(self.id, assignment=a)

    def test_reopen_requires_fresh_scope_keeps_evidence_and_fences_old_work(self):
        self.create(self.commission("project_step", workspaces={"old": str(self.root / "old-worktree")}))
        self.store.create_task(self.id, "T001", REV, workspace="old")
        self.store.report(self.id, "T001", 1, 1, REV, "DONE")
        conclusion = self.finish()
        self.assertEqual("DONE", self.store.get_task(self.id, "T001")["phase"])
        self.assertEqual("SUPERSEDED", self.store.list_events(kind="TASK_RESULT")[0]["status"])
        with self.assertRaises(StateError):
            self.store.reopen_thread(self.id, assignment=self.commission(project_ref="different requirement"))
        new_scope = self.commission("project_step", stop_at="prepare second patch", workspaces={"new": str(self.root/"new")})
        reopened = self.store.reopen_thread(self.id, assignment=new_scope)
        self.assertEqual((2, 2, None), (reopened["generation"], reopened["spec_rev"], reopened["handoff"]))
        self.assertEqual([], self.store.list_tasks(self.id))
        with self.assertRaises(StateError):
            self.store.create_task(self.id, "T001", REV, workspace="old")
        self.store.create_task(self.id, "T001", REV, workspace="new")
        with self.assertRaisesRegex(StateError, "stale"):
            self.store.report(self.id, "T001", 1, 1, REV, "DONE")
        self.assertEqual(conclusion["handoff"], self.store.list_events(kind="THREAD_HANDOFF")[0]["payload"])
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            archive = json.loads(db.execute("SELECT detail FROM audit WHERE action='archive_assignment'").fetchone()[0])
        self.assertEqual("DONE", archive["tasks"][0]["phase"])
        self.assertEqual(conclusion["assignment"], archive["assignment"])
        with self.assertRaisesRegex(StateError, "stale"):
            self.finish()

    def test_immediate_reopen_waits_for_all_old_sessions_to_be_reclaimed(self):
        scope = self.commission("project_step", workspaces={"ui": str(self.root)})
        self.create(scope)
        self.store.create_task(self.id, "T001", REV)
        self.store.reserve_agent("worker", "dev-worker", self.root, thread_id=self.id)
        self.finish()
        with self.assertRaisesRegex(StateError, "active sessions"):
            self.store.reopen_thread(self.id, assignment=scope)
        self.assertEqual("HANDOFF", self.store.get_thread(self.id)["status"])
        self.store.release_agent("worker")
        self.store.reopen_thread(self.id, assignment=scope)
        self.assertEqual(2, self.store.create_task(self.id, "T001", REV)["generation"])

    def test_delayed_inflight_result_cannot_reopen_work_on_closed_commission(self):
        self.create(self.commission("project_step", workspaces={"old": str(self.root)}))
        self.store.create_task(self.id, "T001", REV)
        self.store.report(self.id, "T001", 1, 1, REV, "DONE")
        (event,) = self.store.list_events(kind="TASK_RESULT")
        lease = self.store.acquire_delivery(event["id"])
        self.finish()
        self.store.finish_delivery(event["id"], lease["lease_token"], "controller", "uncertain")
        current = self.store.get_event(event["id"])
        self.assertEqual("UNCERTAIN", current["status"])
        self.assertFalse(self.store.claim_event(event["id"], "controller")["claimed"])
        self.store.reopen_thread(self.id, assignment=self.commission())
        self.assertFalse(self.store.claim_event(event["id"], "controller")["claimed"])
        self.store.retry_event(event["id"])
        self.assertIsNone(self.store.acquire_delivery(event["id"]))
        self.assertEqual("SUPERSEDED", self.store.get_event(event["id"])["status"])

    def test_v2_migration_preserves_legacy_timestamps_gate_and_unsent_event(self):
        # A v2 fixture derived from an actual initialized database retaining its original rows.
        before = self.store.create_thread("legacy", self.project, parent="router")
        self.store.create_task("legacy", "T001", REV)
        self.store.report("legacy", "T001", 1, 1, REV, "DONE")
        e = self.store.enqueue_event("job", "legacy", {"id": 3})
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            # Rebuild the two tables in the v2 shape; no DROP COLUMN/version dependency.
            for table, omit in (("threads", {"assignment", "handoff"}), ("tasks", {"workspace"})):
                cols = db.execute(f"PRAGMA table_info({table})").fetchall()
                cols = [c for c in cols if c[1] not in omit]
                ddl = [f"{c[1]} {c[2]}" + (" NOT NULL" if c[3] else "") +
                       (f" DEFAULT {c[4]}" if c[4] is not None else "") for c in cols]
                pk = ",".join(c[1] for c in sorted(cols, key=lambda c:c[5]) if c[5])
                names = ",".join(c[1] for c in cols)
                db.execute(f"CREATE TABLE old_{table} ({','.join(ddl)},PRIMARY KEY({pk}))")
                db.execute(f"INSERT INTO old_{table} SELECT {names} FROM {table}")
                db.execute(f"DROP TABLE {table}")
                db.execute(f"ALTER TABLE old_{table} RENAME TO {table}")
            db.execute("PRAGMA user_version=2")
        self.store = Store(self.state)
        self.assertEqual(before, self.store.get_thread("legacy"))
        self.assertEqual("DONE", self.store.get_task("legacy", "T001")["phase"])
        self.assertEqual("PENDING", self.store.get_event(e["id"])["status"])
        with self.assertRaisesRegex(StateError, "gate"):
            self.store.set_thread_status("legacy", "CLOSED")
        with self.assertRaisesRegex(StateError, "only project"):
            self.store.handoff_thread("legacy", generation=1, spec_rev=1, summary="inspected",
                                      evidence=[self.evidence])
        self.store.report("legacy", "T001", 1, 1, REV, "APPROVED")
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            self.assertEqual(3, db.execute("PRAGMA user_version").fetchone()[0])

    def test_report_binds_each_task_to_its_exact_worktree_while_legacy_still_accepts_sibling(self):
        def git(repo, *args):
            return subprocess.run(["git", "-C", str(repo), *map(str,args)], check=True,
                                  capture_output=True, text=True).stdout.strip()
        repos, roots, shas = {}, {}, {}
        for name in ("service", "ui"):
            repo, work = self.project / name, self.project / ("wt-" + name)
            repo.mkdir()
            git(repo, "init", "-b", "main")
            git(repo, "config", "user.name", "Local test")
            git(repo, "config", "user.email", "local@example.invalid")
            git(repo, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", name)
            git(repo, "worktree", "add", "-b", "approved", str(work))
            repos[name], roots[name], shas[name] = repo, str(work), git(work, "rev-parse", "HEAD")
        self.create(self.commission("project_step", workspaces=roots))
        for key in ("service", "ui"):
            self.cli("task", "create", "--thread", self.id, "--id", key, "--revision", shas[key],
                     "--workspace", key)
        with self.assertRaisesRegex(StateError, "baseline revision"):
            self.cli("task", "create", "--thread", self.id, "--id", "wrong-sha", "--workspace", "service",
                     "--revision", shas["ui"])
        with self.assertRaisesRegex(StateError, "baseline revision"):
            self.cli("task", "retry", "--thread", self.id, "--id", "service", "--revision", shas["ui"])
        common = ("--thread", self.id, "--id", "service", "--attempt", 1,
                  "--spec-rev", 1, "--revision", shas["service"])
        for wrong in (roots["ui"], repos["service"]):
            with self.assertRaisesRegex(StateError, "assigned worktree"):
                self.cli("task", "report", *common, "--kind", "DONE", "--cwd", wrong)
        for key in ("service", "ui"):
            self.assertEqual("DONE", self.cli("task", "report", "--thread", self.id, "--id", key,
                "--attempt", 1, "--spec-rev", 1, "--revision", shas[key], "--kind", "DONE")["phase"])
        self.finish()
        self.store.create_thread("old", roots["service"])
        self.store.create_task("old", "old-task", shas["service"])
        self.assertEqual("DONE", self.cli("task", "report", "--thread", "old", "--id", "old-task",
            "--attempt", 1, "--spec-rev", 1, "--revision", shas["service"], "--kind", "DONE",
            "--cwd", repos["service"])["phase"])


if __name__ == "__main__":
    unittest.main()
