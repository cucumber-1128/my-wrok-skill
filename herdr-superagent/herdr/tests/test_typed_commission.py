"""A numeric display ID does not change the kind already known by TAPD intake."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sa_state import StateError, Store


class TypedCommissionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = Store(self.root / "state")
        self.full = "1100000000000000001"

    def assignment(self, source=None, mode="project_step", ref=None):
        return dict(project_root=str(self.root), project_ref=ref or self.full[-7:],
                    skill_entry=str(self.root / "rules.md"), mode=mode, stop_at="handoff",
                    workspaces={}, evidence=[], **({"workflow_source": source} if source else {}))

    def test_short_or_full_numeric_ref_does_not_let_a_different_global_flow_take_a_tapd_item(self):
        for kind, wrong, ref in (("bug", "global-story", self.full[-7:]),
                                 ("story", "global-bug", self.full)):
            with self.subTest(kind=kind):
                tid = f"tapd:00000001:{kind}:{self.full}"
                with self.assertRaisesRegex(StateError, "global-"):
                    self.state.create_thread(tid, self.root, assignment=self.assignment(wrong, ref=ref))
                created = self.state.create_thread(tid, self.root, assignment=self.assignment("global-" + kind, ref=ref))
                self.assertEqual("RUNNING", created["status"])

    def test_reopen_checks_new_global_source_before_lifecycle_and_generation_mutate(self):
        tid = f"tapd:00000001:story:{self.full}"
        self.state.create_thread(tid, self.root, assignment=self.assignment("global-story"))
        self.state.set_thread_status(tid, "CANCELED")
        with self.assertRaisesRegex(StateError, "global-bug"):
            self.state.reopen_thread(tid, assignment=self.assignment("global-bug"))
        self.assertEqual(1, self.state.get_thread(tid)["generation"])
        self.assertEqual("CANCELED", self.state.get_thread(tid)["status"])
        self.assertEqual(2, self.state.reopen_thread(tid, assignment=self.assignment("global-story"))["generation"])

    def test_untyped_local_and_existing_project_read_only_contract_do_not_require_kind_metadata(self):
        self.state.create_thread("manual:delivery", self.root, assignment=self.assignment("global-story"))
        tid = f"tapd:00000001:bug:{self.full}"
        self.state.create_thread(tid, self.root, assignment=self.assignment(mode="read_only"))
        self.assertNotIn("workflow_source", self.state.get_thread(tid)["assignment"])
