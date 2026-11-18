"""Exercise the actual intake inbox and the PowerShell front door; no model or TAPD calls."""
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from sa_activate import Inbox
from sa_state import StateError

W = "00000001"
ID = "1100000000000000003"
URL = f"https://www.tapd.cn/{W}/prong/stories/view/{ID}"
KEY = f"tapd:{W}:story:{ID}"


class Activation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="herdr-intake-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.evidence = self.root / "report.md"
        self.evidence.write_text("Scoped outcome; release not requested.", encoding="utf-8")
        self.manifest = {"kind": "auto", "instruction": "check scope", "files": [], "unresolved": [],
                         "items": [{"key": KEY, "workspace_id": W, "kind": "story",
                                    "id": ID, "url": URL, "sources": [{"type": "text"}]}]}

    def cli(self, *args):
        cmd = ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / "sa-start.ps1"),
               "-StateDir", str(self.state), *map(str, args)]
        result = subprocess.run(cmd, cwd=self.root, env=dict(os.environ, HERDR_PYTHON=sys.executable),
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_public_preview_and_cached_receipt_do_not_launch_herdr(self):
        word = "启用SuperAgent模式 做需求，" + URL
        out = self.cli("-Text", word, "-Kind", "story", "-Preview")
        self.assertEqual("PREVIEW", out["state"])
        self.assertEqual([KEY], [i["key"] for i in out["items"]])
        self.assertFalse(self.state.exists())
        batch = self.cli("-Text", word, "-Kind", "story", "-WorkflowProfile", "delivery-swapped")
        self.assertEqual({"QUEUED": 1}, batch["counts"])
        self.assertEqual("delivery-swapped", batch["workflow_profile"])
        self.assertFalse(batch["reused"])
        repeat = self.cli("-Text", word, "-Kind", "story", "-WorkflowProfile", "delivery-swapped")
        self.assertEqual(batch["batch_id"], repeat["batch_id"])
        self.assertTrue(repeat["reused"])
        first = self.cli("-Batch", batch["batch_id"], "-Item", KEY, "-Claim")
        authority = SCRIPTS.parent / "references" / "iteration-policy.md"
        self.assertTrue(authority.is_file())
        for receipt in (out, batch, repeat, first):
            self.assertEqual(authority, Path(receipt["iteration_policy_file"]))
        self.assertTrue(first["claimed"])
        self.assertTrue(first["claim_token"])
        self.assertFalse(self.cli("-Batch", batch["batch_id"], "-Item", KEY, "-Claim")["claimed"])
        self.cli("-Batch", batch["batch_id"], "-Item", KEY, "-Status", "HANDOFF", "-Note", "Scope returned",
                 "-ClaimToken", first["claim_token"], "-Evidence", self.evidence)
        view = self.cli("-Batch", batch["batch_id"])
        self.assertEqual("HANDOFF", view["state"])
        self.assertNotIn(first["claim_token"], json.dumps(view))
        ib = Inbox(self.state)
        self.assertFalse(ib.store.list_agents())
        self.assertFalse(ib.store.list_threads())
        self.assertFalse(ib.store.list_events())

    def test_no_input_needs_details_and_substantive_prose_does_not_invent_tapd_id(self):
        empty = self.cli("-Text", "启用superagent模式，来修一波bug")
        self.assertEqual("NEEDS_INPUT", empty["state"])
        self.assertFalse(self.state.exists())
        missing = self.cli("-Text", "启用superagent模式，附件里有缺陷", "-File", self.root / "missing.xlsx")
        self.assertEqual("NEEDS_INPUT", missing["state"])
        self.assertTrue(missing["unresolved"])
        self.assertFalse(self.state.exists())
        free = self.cli("-Text", "启用superagent模式，修复审批保存后金额归零")
        self.assertEqual("QUEUED", free["state"])
        self.assertTrue(free["items"][0]["key"].startswith("local:"))
        self.assertNotIn("id", free["items"][0])

    def test_two_batches_cannot_claim_the_same_tapd_item_concurrently(self):
        ib = Inbox(self.state)
        a = ib.submit(self.manifest, "delivery")["batch_id"]
        b = ib.submit(dict(self.manifest, instruction="another scope"), "delivery")["batch_id"]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda batch: ib.claim(batch, KEY), (a, b)))
        win = 0 if responses[0]["claimed"] else 1
        self.assertEqual(1, sum(bool(r["claimed"]) for r in responses))
        self.assertEqual([a, b][win], responses[1-win]["active_batch"])
        self.assertEqual("QUEUED", ib.show([a,b][1-win])["state"])
        ib.report([a,b][win], KEY, responses[win]["claim_token"], "HANDOFF", "done", [self.evidence])
        next_scope = ib.claim([a,b][1-win], KEY)
        self.assertTrue(next_scope["claimed"])
        self.assertEqual("done", next_scope["previous_handoff"]["note"])

    def test_takeover_is_fenced_and_cannot_mark_unproven_completion(self):
        ib = Inbox(self.state)
        b = ib.submit(self.manifest, "delivery")["batch_id"]
        first = ib.claim(b, KEY)
        with self.assertRaisesRegex(StateError, "evidence"):
            ib.takeover(b, KEY, first["claim_id"], True, "reconciled", [])
        with self.assertRaisesRegex(StateError, "prior"):
            ib.takeover(b, KEY, "stale", True, "reconciled", [self.evidence])
        new = ib.takeover(b, KEY, first["claim_id"], True, "reconciled", [self.evidence])
        with self.assertRaisesRegex(StateError, "token"):
            ib.report(b, KEY, first["claim_token"], "HANDOFF", "stale output", [self.evidence])
        with self.assertRaisesRegex(StateError, "evidence"):
            ib.report(b, KEY, new["claim_token"], "HANDOFF", "just claiming")
        ib.report(b, KEY, new["claim_token"], "BLOCKED", "needs a scope decision")
        self.assertEqual("NEEDS_ATTENTION", ib.show(b)["state"])
        self.assertEqual("QUEUED", ib.retry(b, KEY, "received the answer")["status"])

    def test_receipt_for_valid_rows_keeps_unresolved_rows_visible(self):
        ib = Inbox(self.state)
        b = ib.submit(dict(self.manifest, unresolved=[{"source":{"sheet":"B","cell":"A9"},
                      "reason":"unsupported_url"}]), "delivery")["batch_id"]
        first = ib.claim(b, KEY)
        ib.report(b, KEY, first["claim_token"], "HANDOFF", "done", [self.evidence])
        view = ib.show(b)
        self.assertEqual("NEEDS_ATTENTION", view["state"])
        self.assertEqual("A9", view["unresolved"][0]["source"]["cell"])

    def test_existing_thread_binding_requires_real_completion_and_current_version(self):
        ib = Inbox(self.state)
        b = ib.submit(self.manifest, "delivery")["batch_id"]
        token = ib.claim(b, KEY)["claim_token"]
        assignment = {"project_root": str(self.root), "project_ref": ID, "mode": "read_only",
                      "stop_at":"return scope", "skill_entry": str(self.evidence), "workspaces":{},
                      "evidence":[str(self.evidence)]}
        t = ib.store.create_thread(KEY,self.root,assignment=assignment)
        ib.report(b, KEY, token, "CLAIMED", "scoped handoff", thread_id=t["id"])
        with self.assertRaisesRegex(StateError, "handed off"):
            ib.report(b, KEY, token, "HANDOFF", "too early", [self.evidence])
        ib.store.handoff_thread(t["id"],generation=1,spec_rev=1,summary="scope inspected",
                                evidence=[str(self.evidence)])
        ib.report(b, KEY, token, "HANDOFF", "scope returned", thread_id=t["id"])
        self.assertEqual("HANDOFF", ib.show(b)["state"])
        self.assertEqual([str(self.evidence.resolve())], ib.show(b)["items"][0]["evidence"])
        self.assertFalse(ib.report(b, KEY, token, "HANDOFF", "scope returned")["changed"])

    def test_other_ticket_or_late_terminal_link_cannot_supply_completion_evidence(self):
        ib = Inbox(self.state)
        batch_id = ib.submit(self.manifest, "delivery")["batch_id"]
        token = ib.claim(batch_id, KEY)["claim_token"]
        data = {"project_root":str(self.root), "project_ref":"7777777", "mode":"read_only",
                "stop_at":"inspect", "skill_entry":str(self.evidence),"workspaces":{},"evidence":[]}
        other = ib.store.create_thread("tapd:88888888:story:" + ID, self.root, assignment=data)
        ib.store.handoff_thread(other["id"], generation=1, spec_rev=1, summary="someone else's release",
                                evidence=[str(self.evidence)])
        with self.assertRaisesRegex(StateError, "does not match"):
            ib.report(batch_id, KEY, token, "HANDOFF", "unrelated", thread_id=other["id"])
        same = ib.store.create_thread(KEY, self.root, assignment=data)
        with self.assertRaisesRegex(StateError, "different TAPD object"):
            ib.report(batch_id, KEY, token, "CLAIMED", "mismatch", thread_id=same["id"])
        self.assertEqual("IN_PROGRESS", ib.show(batch_id)["state"])

    def test_preexisting_terminal_thread_is_not_an_implicit_handoff(self):
        ib = Inbox(self.state)
        batch_id = ib.submit(self.manifest, "delivery")["batch_id"]
        token = ib.claim(batch_id, KEY)["claim_token"]
        data = {"project_root":str(self.root), "project_ref":ID[-7:], "mode":"read_only",
                "stop_at":"inspect", "skill_entry":str(self.evidence),"workspaces":{},"evidence":[]}
        ib.store.create_thread(KEY, self.root, assignment=data)
        ib.store.handoff_thread(KEY, generation=1, spec_rev=1, summary="older phase",
                                evidence=[str(self.evidence)])
        with self.assertRaisesRegex(StateError, "Bind a nonterminal"):
            ib.report(batch_id, KEY, token, "HANDOFF", "unverified", thread_id=KEY)
        with self.assertRaisesRegex(StateError, "Bind a nonterminal"):
            ib.report(batch_id, KEY, token, "CLAIMED", "linked too late", thread_id=KEY)

    def test_identically_named_active_thread_started_outside_inbox_is_not_reassigned(self):
        ib = Inbox(self.state)
        batch_id = ib.submit(self.manifest, "delivery")["batch_id"]
        ib.store.create_thread(KEY, self.root, owner="existing-leader")
        receipt = ib.claim(batch_id, KEY)
        self.assertFalse(receipt["claimed"])
        self.assertEqual(KEY, receipt["active_thread"])
        self.assertEqual("QUEUED", ib.show(batch_id)["state"])


if __name__ == "__main__":
    unittest.main()
