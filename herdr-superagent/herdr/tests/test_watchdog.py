import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sa_process import TransportError
from sa_runtime import Runtime
from sa_state import Store
from sa_watchdog import Driver
from test_runtime import FakeHc


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="herdr-watch-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 1000.0
        self.store = Store(self.root / "state", clock=lambda: self.now)
        self.cwd = self.root / "work"
        self.cwd.mkdir()
        self.hc = FakeHc()
        self.runtime = Runtime(self.store, hc=self.hc, clock=lambda: self.now)

    def registered_leader(self, *, max_runtime_sec=14400):
        self.store.create_thread("manual:project:42", self.cwd, owner="leader",
                                 parent="router", max_runtime_sec=max_runtime_sec)
        self.store.reserve_agent("leader", "leader", self.cwd, thread_id="manual:project:42",
                                 request={"delivery": "DELIVERED", "generation": 1, "spec_rev": 1})
        self.store.update_agent("leader", address="leader", status="RUNNING")
        self.hc.add("leader")
        self.hc.add("router")

    def test_transient_get_failure_does_not_remove_supervision(self):
        self.registered_leader()
        self.hc.get_errors["leader"] = [TransportError("temporary", kind="unavailable")]
        driver = Driver(self.runtime, leader="leader", grace_sec=0)
        driver.step()
        driver.step()
        self.assertEqual(2, len([c for c in self.hc.calls if c[:2] == ["get", "leader"]]))
        self.assertIn("leader", driver.targets)
        self.assertEqual(1, len([c for c in self.hc.calls if c[:2] == ["send", "leader"]]))

    def test_finished_watchdog_does_not_disable_still_live_leader(self):
        self.registered_leader()
        self.hc.add("watchdog", state="finished")
        driver = Driver(self.runtime, leader="leader", watchdog="watchdog", grace_sec=0)
        driver.step()
        self.assertTrue(any(c[:2] == ["get", "leader"] for c in self.hc.calls))
        self.assertTrue(any(c[:2] == ["send", "leader"] for c in self.hc.calls))

    def test_closed_in_markdown_note_does_not_stop_running_flow(self):
        state = self.root / "flow.md"
        state.write_text("status: RUNNING\n\nAfter acceptance set status: CLOSED.\n", encoding="utf-8")
        self.hc.add("leader")
        driver = Driver(self.runtime, leader="leader", state_file=state, grace_sec=0)
        result = driver.step()
        self.assertFalse(result["should_exit"])
        self.assertTrue(any(c[:2] == ["get", "leader"] for c in self.hc.calls))

    def test_closed_status_is_parsed_as_a_field(self):
        state = self.root / "flow.json"
        state.write_text(json.dumps({"status": "CLOSED", "note": "complete"}), encoding="utf-8")
        self.hc.add("leader")
        result = Driver(self.runtime, leader="leader", state_file=state).step()
        self.assertTrue(result["should_exit"])
        self.assertFalse(any(c[0] == "send" for c in self.hc.calls))

    def test_waiting_human_and_paused_flows_are_not_checkpointed(self):
        for status in ("WAITING_HUMAN", "PAUSED", "BLOCKED"):
            with self.subTest(status=status):
                state = self.root / "flow.json"
                state.write_text(json.dumps({"status": status}), encoding="utf-8")
                self.hc.calls.clear()
                self.hc.add("leader")
                Driver(self.runtime, leader="leader", state_file=state, grace_sec=0).step()
                self.assertFalse(any(c[0] == "send" for c in self.hc.calls))

    def test_blocked_terminal_never_receives_checkpoint_or_force(self):
        self.registered_leader()
        self.hc.terminals["leader"]["status"] = {"state": "blocked"}
        Driver(self.runtime, leader="leader", grace_sec=0).step()
        self.assertFalse(any(c[:2] == ["send", "leader"] for c in self.hc.calls))
        self.assertFalse(any("--force" in c for c in self.hc.calls))

    def test_runtime_budget_pauses_flow_and_interrupts_only_owned_running_agent(self):
        self.registered_leader(max_runtime_sec=10)
        self.hc.terminals["leader"]["status"] = {"state": "working"}
        self.hc.add("unrelated-user-agent", status="working")
        self.now += 11
        Driver(self.runtime, leader="leader").step()
        self.assertEqual("PAUSED", self.store.get_thread("manual:project:42")["status"])
        self.assertTrue(any(c[:2] == ["interrupt", "leader"] for c in self.hc.calls))
        self.assertFalse(any("unrelated-user-agent" in c for c in self.hc.calls))
        self.assertFalse(any(c[0] in {"kill", "rm", "server"} for c in self.hc.calls))

    def test_idle_checkpoint_is_cooled_down(self):
        self.registered_leader()
        driver = Driver(self.runtime, leader="leader", grace_sec=60)
        driver.step()
        self.now += 61
        driver.step()
        driver.step()
        self.assertEqual(1, len([c for c in self.hc.calls if c[:2] == ["send", "leader"]]))

    def test_semantic_watchdog_tick_is_skipped_without_active_work(self):
        self.hc.add("watchdog")
        Driver(self.runtime, watchdog="watchdog", watchdog_interval_sec=1).step()
        self.assertFalse(any(c[0] == "send" for c in self.hc.calls))

    def test_explicit_name_and_registered_address_are_not_supervised_twice(self):
        self.registered_leader()
        thread = self.store.assign_owner("manual:project:42", "t7")
        self.store.update_agent("leader", address="t7",
                                 request={"delivery": "DELIVERED", "generation": thread["generation"],
                                          "spec_rev": thread["spec_rev"]})
        self.hc.add("t7")
        Driver(self.runtime, leader="leader", grace_sec=0).step()
        sends = [c for c in self.hc.calls if c[0] == "send" and c[1] in {"leader", "t7"}]
        self.assertEqual(1, len(sends))

    def test_canceled_flow_reclaims_only_its_registered_sessions(self):
        self.registered_leader()
        self.store.set_thread_status("manual:project:42", "CANCELED")
        self.hc.add("unrelated-user-agent")
        Driver(self.runtime, leader="leader").step()
        self.assertEqual("FINISHED", self.store.agent("leader")["status"])
        self.assertTrue(any(c[:2] == ["kill", "leader"] for c in self.hc.calls))
        self.assertFalse(any("unrelated-user-agent" in c for c in self.hc.calls))

    def test_replaced_owner_is_not_checkpointed_or_allowed_to_keep_working(self):
        self.registered_leader()
        self.store.assign_owner("manual:project:42", "replacement")
        self.hc.terminals["leader"]["status"] = {"state": "working"}
        Driver(self.runtime, leader="leader", grace_sec=0).step()
        self.assertTrue(any(c[:2] == ["interrupt", "leader"] for c in self.hc.calls))
        self.assertFalse(any(c[:2] == ["send", "leader"] for c in self.hc.calls))
        self.hc.calls.clear()
        self.hc.terminals["leader"]["status"] = {"state": "idle"}
        Driver(self.runtime, leader="leader", grace_sec=0).step()
        self.assertFalse(any(c[:2] == ["send", "leader"] for c in self.hc.calls))

    def test_uncertain_start_produces_a_durable_alert(self):
        self.store.reserve_agent("scout", "scout", self.cwd,
                                 request={"delivery": "UNCERTAIN", "uncertain_stage": "launch"})
        self.store.update_agent("scout", status="UNCERTAIN")
        self.hc.add("scout")
        self.hc.add("router")
        Driver(self.runtime).step()
        alerts = self.store.list_events(kind="ALERT")
        self.assertTrue(any("startup" in event["payload"]["problem"] for event in alerts))

    def test_empty_observation_does_not_release_capacity_or_stop_supervision(self):
        self.registered_leader()
        original_call = self.hc.call

        def malformed_get(args, timeout=60):
            return {} if args[:2] == ["get", "leader"] else original_call(args, timeout)

        self.hc.call = malformed_get
        driver = Driver(self.runtime, leader="leader")
        driver.step()
        driver.step()
        self.assertNotEqual("FINISHED", self.store.agent("leader")["status"])
        self.assertFalse(driver.targets["leader"].get("finished", False))
        self.assertEqual(2, driver.targets["leader"]["failures"])

    def test_leader_is_not_checkpointed_before_its_startup_ack(self):
        self.registered_leader()
        self.store.update_agent("leader", status="READY")
        Driver(self.runtime, leader="leader", grace_sec=0).step()
        self.assertFalse(any(c[:2] == ["send", "leader"] for c in self.hc.calls))

    def test_uncertain_unverified_name_never_controls_an_unrelated_terminal(self):
        self.store.reserve_agent("leader", "leader", self.cwd, request={
            "delivery": "UNCERTAIN", "uncertain_stage": "launch",
            "context_file": str(self.root / "expected-instance.json"),
        })
        self.store.update_agent("leader", status="UNCERTAIN")
        self.hc.add("leader", state="finished")
        self.hc.add("router")
        Driver(self.runtime, leader="leader", grace_sec=0).step()
        self.assertEqual("UNCERTAIN", self.store.agent("leader")["status"])
        self.assertFalse(any(c[0] in {"kill", "rm", "interrupt"} for c in self.hc.calls))


if __name__ == "__main__":
    unittest.main()
