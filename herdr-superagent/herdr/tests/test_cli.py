import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="herdr-cli-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.cwd = self.root / "work"
        self.cwd.mkdir()
        self.snapshot = self.cwd / "snapshot.txt"
        self.snapshot.write_text("immutable input\n", encoding="utf-8")
        self.revision = "patch:" + hashlib.sha256(self.snapshot.read_bytes()).hexdigest()

    def command(self, *args, good=True):
        completed = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "sa.py"), "--state-dir", str(self.state), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(0 if good else 1, completed.returncode, completed.stdout + completed.stderr)
        return json.loads(completed.stdout if good else completed.stderr)

    def setup_task(self):
        self.command("thread", "create", "--id", "manual:project:42", "--cwd", self.cwd, "--parent", "router")
        self.command("task", "create", "--thread", "manual:project:42", "--id", "dev-1",
                     "--revision", self.revision)

    def report(self, kind, *extra, good=True):
        return self.command(
            "task", "report", "--thread", "manual:project:42", "--id", "dev-1",
            "--attempt", "1", "--spec-rev", "1", "--revision", self.revision,
            "--kind", kind, "--snapshot-file", self.snapshot, *extra, good=good,
        )

    def test_report_gate_round_trip_and_router_notification(self):
        self.setup_task()
        self.report("DONE")
        self.report("APPROVED")
        deployment = self.root / "deployment.json"
        deployment.write_text(json.dumps({
            "artifact_id": "local-17", "code_revision": self.revision,
            "environment_revision": self.revision, "environment": "http://local.invalid",
            "verified_at": "2026-11-16T00:00:00Z",
        }), encoding="utf-8")
        self.report("DEPLOYED", "--deployment-file", deployment)
        evidence = self.root / "case-1.txt"
        evidence.write_text("actual assertion: expected=actual\n", encoding="utf-8")
        self.report("PASS", "--evidence", evidence)
        result = self.command("thread", "status", "--id", "manual:project:42",
                              "--status", "CLOSED", "--generation", "1")
        self.assertEqual("CLOSED", result["status"])
        notices = self.command("event", "list")
        self.assertEqual("THREAD_DONE", notices[0]["kind"])
        self.assertEqual("router", notices[0]["recipient"])

    def test_missing_evidence_and_missing_deployment_manifest_fail(self):
        self.setup_task()
        self.report("DONE")
        self.report("APPROVED")
        failed = self.report("DEPLOYED", "--artifact-id", "old", "--environment-revision",
                             self.revision, good=False)
        self.assertIn("deployment", failed["error"]["message"])
        task = self.command("task", "get", "--thread", "manual:project:42", "--id", "dev-1")
        self.assertEqual("APPROVED", task["phase"])

    def test_modified_snapshot_cannot_be_reported_as_old_code(self):
        self.setup_task()
        self.snapshot.write_text("changed without rebinding revision", encoding="utf-8")
        error = self.report("DONE", good=False)
        self.assertIn("snapshot", error["error"]["message"])

    def test_done_can_bind_actual_new_snapshot_using_the_assigned_base(self):
        self.setup_task()
        self.snapshot.write_text("completed output", encoding="utf-8")
        actual = "patch:" + hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        result = self.command(
            "task", "report", "--thread", "manual:project:42", "--id", "dev-1",
            "--attempt", "1", "--spec-rev", "1", "--revision", actual,
            "--base-revision", self.revision, "--snapshot-file", self.snapshot, "--kind", "DONE",
        )
        self.assertEqual(actual, result["code_revision"])

    def test_blocked_report_is_possible_before_dirty_work_has_a_final_snapshot(self):
        self.setup_task()
        self.snapshot.write_text("unfinished change", encoding="utf-8")
        result = self.report("BLOCKED", "--summary", "contract needs owner decision")
        self.assertEqual("BLOCKED", result["phase"])

    def test_state_power_shell_entrypoint_preserves_arguments_and_exit_code(self):
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(SCRIPTS / "sa-state.ps1"),
             "--state-dir", str(self.state), "thread", "create", "--id", "manual:项目:42",
             "--cwd", str(self.cwd)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("manual:项目:42", json.loads(completed.stdout)["id"])
        failed = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(SCRIPTS / "sa-state.ps1"),
             "--state-dir", str(self.state), "thread", "status", "--id", "manual:项目:42",
             "--status", "CLOSED", "--generation", "1"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(1, failed.returncode)
        self.assertIn("gate", json.loads(failed.stderr)["error"]["message"])

    def test_spawn_wrapper_returns_failure_without_touching_hc_for_missing_role(self):
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(SCRIPTS / "sa-spawn.ps1"),
             "-Role", "nonexistent-test-role", "-Name", "test", "-Cwd", str(self.cwd),
             "-StateDir", str(self.state)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(1, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("role file", json.loads(completed.stderr)["error"]["message"])

    def test_trigger_rejects_a_nonobject_source_with_structured_error(self):
        config = self.root / "bad-sources.json"
        config.write_text('{"sources":[7]}', encoding="utf-8")
        error = self.command("trigger", "--config", config, "--once", good=False)
        self.assertIn("source", error["error"]["message"])

    def test_claimed_event_can_be_recovered_only_with_a_real_decision_file(self):
        payload = self.root / "payload.json"
        payload.write_text('{"iid":42}', encoding="utf-8")
        event = self.command("event", "enqueue", "--source", "source", "--thread", "source:42",
                             "--payload-file", payload)
        self.command("event", "claim", "--id", event["id"], "--owner", "old-owner")
        decision = self.root / "decision.txt"
        self.command("event", "recover", "--id", event["id"], "--owner", "old-owner",
                     "--confirmed-not-executed", "--decision-file", decision, good=False)
        decision.write_text("Checked logs and target state: no action occurred.", encoding="utf-8")
        result = self.command("event", "recover", "--id", event["id"], "--owner", "old-owner",
                              "--confirmed-not-executed", "--decision-file", decision)
        self.assertEqual("PENDING", result["status"])


if __name__ == "__main__":
    unittest.main()
