import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sa_engines import load_profile
from sa_runtime import Runtime
from sa_state import StateError, Store
from test_runtime import FakeHc


class WorkflowProfiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="herdr-harness-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.roles = self.root / "roles"
        self.roles.mkdir()
        for role in ("router", "leader", "scout", "reviewer", "tester"):
            (self.roles / (role + ".md")).write_text("---\ncommand: claude\n---\nRead and report.", encoding="utf-8")
        self.file = self.root / "my routes.json"
        self.data = {"name": "fixture", "roles": {
            "leader": {"harness": "codex", "model": "codex/gpt-6.2-astra"},
            "router": {"harness": "claude", "model": "auto/deepseek-v4.1-flash"},
            "scout": {"harness": "claude", "model": "auto/deepseek-v4.1-flash"},
            "reviewer": {"harness": "claude", "model": "auto/glm-5.3-flash"},
            "tester": {"harness": "claude", "model": "auto/deepseek-v4.1-flash"}},
            "purposes": {"browser": {"harness": "codex", "model": "codex/gpt-6.2-sol"}}}
        self.save()
        self.hc = FakeHc()
        self.store = Store(self.root / "state")
        self.runtime = Runtime(self.store, self.hc)
        env = patch.dict(os.environ, {"HC_TERMINAL_ID": ""})
        env.start()
        self.addCleanup(env.stop)

    def save(self):
        self.file.write_text(json.dumps(self.data), encoding="utf-8")

    def start(self, role, **kw):
        return self.runtime.spawn(role, kw.pop("name", role), self.root, ready_timeout=0,
                                  role_dir=self.roles, **kw)

    def command(self, name):
        matching = [c for c in self.hc.calls if c[0] == "run" and c[c.index("--name") + 1] == name]
        self.assertEqual(1, len(matching))
        return matching[0][matching[0].index("--") + 1:]

    def test_workflow_snapshot_reaches_children_after_profile_edit_from_cli_or_hc_parent(self):
        parent = self.start("router", workflow_profile=self.file)
        self.store.ack_agent("router", parent["address"])
        self.data["roles"]["scout"]["model"] = "new/foo"
        self.save()
        with patch.dict(os.environ, {"HC_TERMINAL_ID": parent["address"]}):
            child = self.start("scout", model="explicit/foo")
        self.assertEqual("explicit/foo", child["route"]["model"])
        self.assertEqual("explicit", child["route"]["model_source"])
        self.assertEqual("parent", child["route"]["origin"])
        self.assertEqual(["claude", "--model", "explicit/foo", "--add-dir", str(self.roles)], self.command("scout"))
        self.assertEqual("auto/deepseek-v4.1-flash",
                         self.store.agent("scout")["request"]["workflow_profile"]["roles"]["scout"]["model"])
        # A separate process may specify the parent's registered name instead of its address.
        self.file.unlink()
        review = self.start("reviewer", parent="router")
        self.assertEqual("auto/glm-5.3-flash", review["route"]["model"])
        self.assertEqual("parent", review["route"]["origin"])

    def test_codex_browser_uses_its_hc_mode_and_never_grants_extra_write_access(self):
        result = self.start("tester", workflow_profile=self.file, purpose="browser", no_wait=True)
        route = result["route"]
        self.assertEqual(("codex", "codex/gpt-6.2-sol", "browser"),
                         (route["harness"], route["model"], route["purpose"]))
        self.assertEqual(["codex", "--no-daemon", "--model", "codex/gpt-6.2-sol"], self.command("tester"))
        stored = self.store.agent("tester")["request"]
        self.assertEqual(route, json.loads(Path(stored["context_file"]).read_text(encoding="utf-8"))["route"])
        self.file.unlink()
        restarted = Runtime(Store(self.root / "state"), self.hc)
        sent = restarted.resume_start("tester")
        self.assertEqual(route, sent["route"])
        self.store.ack_agent("tester", result["address"])
        duplicate = restarted.resume_start("tester")
        self.assertEqual(route, duplicate["route"])
        self.assertEqual(1, len([c for c in self.hc.calls if c[0] == "send"]))

    def test_owner_propagates_profile_on_explicit_thread_and_no_parent(self):
        self.store.create_thread("work", self.root)
        leader = self.start("leader", thread_id="work", workflow_profile=self.file)
        self.assertEqual(["codex", "--no-daemon", "--model", "codex/gpt-6.2-astra"], self.command("leader"))
        self.store.ack_agent("leader", leader["address"])
        ordinary = self.start("tester", thread_id="work")
        self.assertEqual(("thread_owner", "claude", None),
                         (ordinary["route"]["origin"], ordinary["route"]["harness"], ordinary["route"]["purpose"]))
        self.assertIn("--add-dir", self.command("tester"))

    def test_browser_requires_both_testing_role_and_route_before_reservation(self):
        with self.assertRaisesRegex(StateError, "profile"):
            self.start("tester", purpose="browser")
        with self.assertRaisesRegex(StateError, "only valid"):
            self.start("leader", purpose="browser", workflow_profile=self.file)
        self.data["purposes"] = {}
        self.save()
        with self.assertRaisesRegex(StateError, "no route for purpose"):
            self.start("tester", purpose="browser", workflow_profile=self.file)
        self.assertEqual([], self.hc.calls)
        self.assertEqual([], self.store.list_threads())
        self.assertEqual([], self.store.list_agents())

    def test_malformed_profile_model_and_permission_conflicts_do_not_launch(self):
        (self.roles / "tester.md").write_text("---\ncommand: claude --permission-mode plan\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "permission"):
            self.start("tester", purpose="browser", workflow_profile=self.file)
        self.start("tester", name="safe", workflow_profile=self.file)
        self.assertIn("plan", self.command("safe"))
        with self.assertRaisesRegex(StateError, "model identifier"):
            self.start("reviewer", model="--unexpected", workflow_profile=self.file)
        self.data["roles"]["bogus"] = {"harness": "codex", "model": "x"}
        self.save()
        with self.assertRaisesRegex(StateError, "unknown workflow route"):
            self.start("router", workflow_profile=self.file)
        self.file.write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "workflow profile"):
            self.start("leader", workflow_profile=self.file)
        self.file.write_text('{"name":"a","roles":{"scout":{"model":"a","harness":"claude","env":{}}}}', encoding="utf-8")
        with self.assertRaisesRegex(StateError, "exactly"):
            load_profile(self.file)
        self.assertEqual(["safe"], [a["name"] for a in self.store.list_agents(active=True)])
        self.assertEqual([], self.store.list_threads())

    def test_no_profile_keeps_default_or_explicit_legacy_model(self):
        result = self.start("scout")
        self.assertEqual("legacy", result["route"]["origin"])
        self.assertIsNone(result["route"]["model"])
        self.assertEqual(["claude", "--add-dir", str(self.roles)], self.command("scout"))
        result = self.start("tester", model="legacy/route")
        self.assertEqual("legacy/route", result["route"]["model"])
        self.assertNotIn("workflow_profile", self.store.agent("tester")["request"])
