"""Durable front door for a TAPD batch. The calling conversation is its Router."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sqlite3
import sys
import uuid
from pathlib import Path

from sa_state import StateError, Store, canonical


_SHELL_WORDS = re.compile(
    r"super[\s_-]*agent|批处理|工作流|批量|这些|这一?批|这[几一]个|一[批波些个]|"
    r"麻烦|我想|请|帮我|给我|我要|需要|启[用动]|打开|模式|来|一下|下列|下面|以下|"
    r"提交|实现|开发|进行|处理|修复|修改|修理|修|做|执行|需求|缺陷|问题|bugs?|附件|文[件档]|表格|excel|"
    r"tapd|链接|地址|\s|[，。；：、,.!?！？:;/\\'“”‘’()（）【】\[\]<>-]", re.I)


def prepare(text, files, kind="auto", *, sheet=None, first_row=None, last_row=None):
    from sa_intake import parse_intake
    opts = {k: v for k, v in (("sheet", sheet), ("first_row", first_row), ("last_row", last_row))
            if v is not None}
    result = parse_intake(text, files, kind, **opts)
    # Only a user-authored description can create a local (non-TAPD) request.
    # An empty/malformed attachment or link is never silently converted to one.
    if not result["items"] and not result["unresolved"] and not files:
        clean = text.strip()
        if (not re.search(r"https?://|(?:^|[^\w])\S+\.(?:xls[xmb]?|csv)(?:\b|$)", clean, re.I)
                and _SHELL_WORDS.sub("", clean).strip()):
            result["items"].append({"key": "local:" + _id([kind, clean]), "kind": kind,
                                    "description": clean, "sources": [{"type": "user_text"}]})
    return result


def _id(value):
    # Stable request identity for deduplication, not a certificate of file contents.
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:32]


class Inbox:
    """Own only intake tables. Reuse Herdr's state directory, transactions and audit."""

    def __init__(self, state_dir=None):
        self.store = Store(state_dir)
        with self.store._connection() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS intake_batches (
                id TEXT PRIMARY KEY, manifest TEXT NOT NULL, profile TEXT NOT NULL,
                created REAL NOT NULL
              );
              CREATE TABLE IF NOT EXISTS intake_items (
                batch_id TEXT NOT NULL REFERENCES intake_batches(id),
                item_key TEXT NOT NULL, seq INTEGER NOT NULL,
                status TEXT NOT NULL, claim_id TEXT, token TEXT, note TEXT,
                evidence TEXT NOT NULL DEFAULT '[]', thread_id TEXT, generation INTEGER,
                PRIMARY KEY (batch_id,item_key)
              );
              CREATE UNIQUE INDEX IF NOT EXISTS intake_one_consumer
                ON intake_items(item_key) WHERE status='CLAIMED';
              CREATE INDEX IF NOT EXISTS intake_lookup ON intake_items(item_key,status);
            """)

    @staticmethod
    def _item(db, batch_id, key):
        row = db.execute("SELECT * FROM intake_items WHERE batch_id=? AND item_key=?",
                         (batch_id, key)).fetchone()
        if row is None:
            raise StateError("No such item in the specified batch")
        return row

    @staticmethod
    def _public(row):
        return {"status": row["status"], "claim_id": row["claim_id"], "note": row["note"],
                "evidence": json.loads(row["evidence"]), "thread_id": row["thread_id"],
                "generation": row["generation"]}

    def submit(self, manifest, profile):
        if not manifest.get("items"):
            return {"state": "NEEDS_INPUT", "batch_id": None, **manifest}
        batch_id = "batch-" + _id([manifest, profile])
        with self.store._tx() as db:
            existing = db.execute("SELECT 1 FROM intake_batches WHERE id=?", (batch_id,)).fetchone()
            if not existing:
                db.execute("INSERT INTO intake_batches VALUES(?,?,?,?)",
                           (batch_id, canonical(manifest), profile, self.store.clock()))
                db.executemany("INSERT INTO intake_items(batch_id,item_key,seq,status) VALUES(?,?,?,'QUEUED')",
                               [(batch_id, item["key"], i) for i, item in enumerate(manifest["items"])])
        return dict(self.show(batch_id), reused=bool(existing))

    def show(self, batch_id):
        with self.store._connection() as db:
            batch = db.execute("SELECT * FROM intake_batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                raise StateError("No such batch")
            result = json.loads(batch["manifest"])
            receipts = {r["item_key"]: self._public(r) for r in db.execute(
                "SELECT * FROM intake_items WHERE batch_id=?", (batch_id,))}
        counts = {}
        for item in result["items"]:
            item.update(receipts[item["key"]])
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        if counts.get("CLAIMED"):
            state = "IN_PROGRESS"
        elif counts.get("QUEUED"):
            state = "QUEUED"
        elif counts.get("BLOCKED") or result["unresolved"]:
            state = "NEEDS_ATTENTION"
        elif counts.get("FAILED"):
            state = "FINISHED_WITH_FAILURES"
        else:
            state = "HANDOFF"
        return dict(result, state=state, batch_id=batch_id, counts=counts,
                    workflow_profile=batch["profile"], router="current_session")

    def claim(self, batch_id, key):
        with self.store._tx() as db:
            r = self._item(db, batch_id, key)
            if r["status"] != "QUEUED":
                return dict(self._public(r), claimed=False)
            active = db.execute("SELECT * FROM intake_items WHERE item_key=? AND status='CLAIMED'",
                                (key,)).fetchone()
            if active:
                return {"claimed": False, "status": "QUEUED", "active_batch": active["batch_id"],
                        "active_claim_id": active["claim_id"], "thread_id": active["thread_id"]}
            # A pre-existing manually created Herdr thread has its own executing owner and
            # may have never gone through this inbox. Do not start a second writer.
            if key.startswith("tapd:") and r["thread_id"] != key:
                prior_thread = db.execute("SELECT status,generation FROM threads WHERE id=?", (key,)).fetchone()
                if prior_thread and prior_thread["status"] not in {"HANDOFF", "CLOSED", "FAILED", "CANCELED"}:
                    return {"claimed": False, "status": "QUEUED", "active_thread": key,
                            "thread_status": prior_thread["status"], "generation": prior_thread["generation"]}
            previous = db.execute("""SELECT i.* FROM intake_items i JOIN intake_batches b ON b.id=i.batch_id
                                     WHERE i.item_key=? AND i.status='HANDOFF' ORDER BY b.created DESC LIMIT 1""",
                                  (key,)).fetchone()
            token, owner = secrets.token_urlsafe(24), uuid.uuid4().hex
            db.execute("""UPDATE intake_items SET status='CLAIMED',claim_id=?,token=?
                          WHERE batch_id=? AND item_key=?""", (owner, token, batch_id, key))
            self.store._audit(db, batch_id, "intake_claim", {"key": key, "claim_id": owner})
            result = dict(self._public(self._item(db, batch_id, key)), claimed=True, claim_token=token)
            if previous:
                result["previous_handoff"] = dict(self._public(previous), batch_id=previous["batch_id"])
            return result

    @staticmethod
    def _evidence(paths):
        out = []
        for p in paths or ():
            path = Path(p).resolve()
            if not path.is_file():
                raise StateError(f"Evidence file does not exist: {path}")
            if str(path) not in out:
                out.append(str(path))
        return out

    def report(self, batch_id, key, token, status, note, evidence=(), thread_id=None):
        """CLAIMED with -ThreadId links the active Herdr thread without releasing the claim."""
        if status not in {"CLAIMED", "BLOCKED", "HANDOFF", "FAILED"} or not note.strip():
            raise StateError("Report requires a supported status and a nonempty note")
        refs = self._evidence(evidence)
        with self.store._tx() as db:
            r = self._item(db, batch_id, key)
            if not token or not secrets.compare_digest(token, r["token"] or ""):
                raise StateError("Claim token mismatch; do not change or repeat another Router's work")
            if r["status"] != "CLAIMED":
                if (r["status"] == status and r["note"] == note and (json.loads(r["evidence"]) == refs or
                        status == "HANDOFF" and not refs and r["thread_id"])
                        and (not thread_id or r["thread_id"] == thread_id)):
                    return dict(self._public(r), changed=False)
                raise StateError("Item is no longer claimed")
            if r["thread_id"] and thread_id and thread_id != r["thread_id"]:
                raise StateError("Item already linked to a different thread")
            tid, gen = thread_id or r["thread_id"], r["generation"]
            thread = self.store._one(db, "threads", "id", tid) if tid else None
            if thread:
                # Only the workspace/kind/full-id key uniquely ties a TAPD thread to this
                # source across projects. A friendly name, short number or local directory
                # is not enough. Differently named project workflows can provide file evidence.
                if key.startswith("tapd:") and tid != key:
                    raise StateError("Thread ID does not match this TAPD object's workspace, kind and full ID")
                if key.startswith("tapd:") and thread.get("assignment"):
                    project_ref = thread["assignment"]["project_ref"]
                    full_id = key.rsplit(":", 1)[1]
                    if project_ref not in {key, full_id} and not (
                            project_ref.isascii() and project_ref.isdigit()
                            and 5 <= len(project_ref) < len(full_id) and full_id.endswith(project_ref)):
                        raise StateError("Thread project_ref belongs to a different TAPD object")
                if not r["thread_id"] and (status != "CLAIMED" or
                        thread["status"] in {"CLOSED", "HANDOFF", "FAILED", "CANCELED"}):
                    raise StateError("Bind a nonterminal thread to the claimed item before handoff")
                if gen is not None and gen != thread["generation"]:
                    raise StateError("Linked thread generation changed; reconcile before reporting")
                gen = thread["generation"]
            if status == "HANDOFF":
                if thread and thread["status"] not in {"HANDOFF", "CLOSED"}:
                    raise StateError("Linked thread has not handed off or closed")
                if not refs and not thread:
                    raise StateError("HANDOFF needs actual evidence or a matching completed thread")
                if thread and not refs and thread.get("handoff"):
                    refs = self._evidence(thread["handoff"]["evidence"])
            if status == "CLAIMED" and not tid:
                raise StateError("Use CLAIMED only to link an existing thread")
            db.execute("""UPDATE intake_items SET status=?,note=?,evidence=?,thread_id=?,generation=?
                          WHERE batch_id=? AND item_key=?""", (status, note, canonical(refs), tid, gen, batch_id, key))
            self.store._audit(db, batch_id, "intake_report", {"key": key, "status": status,
                              "claim_id": r["claim_id"], "note": note, "evidence": refs, "thread_id": tid})
            return dict(self._public(self._item(db, batch_id, key)), changed=True)

    def retry(self, batch_id, key, note):
        if not note.strip():
            raise StateError("Retry needs a reason the blocker has been resolved")
        with self.store._tx() as db:
            r = self._item(db, batch_id, key)
            if r["status"] != "BLOCKED":
                raise StateError("Only a reconciled BLOCKED item can be returned to the queue")
            db.execute("""UPDATE intake_items SET status='QUEUED',claim_id=NULL,token=NULL,note=?
                          WHERE batch_id=? AND item_key=?""", (note, batch_id, key))
            self.store._audit(db, batch_id, "intake_retry", {"key": key, "reason": note})
            return self._public(self._item(db, batch_id, key))

    def takeover(self, batch_id, key, prior, stopped, note, evidence):
        refs = self._evidence(evidence)
        if not prior or not stopped or not note.strip() or not refs:
            raise StateError("Takeover needs prior claim ID, confirmed stopped, decision and verified evidence")
        with self.store._tx() as db:
            r = self._item(db, batch_id, key)
            if r["status"] != "CLAIMED" or r["claim_id"] != prior:
                raise StateError("Stale prior claim; verify the current owner before taking over")
            owner, token = uuid.uuid4().hex, secrets.token_urlsafe(24)
            db.execute("UPDATE intake_items SET claim_id=?,token=?,note=?,evidence=? WHERE batch_id=? AND item_key=?",
                       (owner, token, note, canonical(refs), batch_id, key))
            self.store._audit(db, batch_id, "intake_takeover", {"key": key, "from": prior, "to": owner,
                              "decision": note, "evidence": refs})
            return dict(self._public(self._item(db, batch_id, key)), claimed=True, claim_token=token)


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir")
    ap.add_argument("--text", default="")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--kind", choices=("auto", "story", "bug"), default="auto")
    ap.add_argument("--sheet")
    ap.add_argument("--first-row", type=int)
    ap.add_argument("--last-row", type=int)
    ap.add_argument("--workflow-profile", choices=("delivery", "delivery-swapped"), default="delivery")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--batch")
    ap.add_argument("--item")
    act = ap.add_mutually_exclusive_group()
    act.add_argument("--claim", action="store_true")
    act.add_argument("--status", choices=("CLAIMED", "HANDOFF", "BLOCKED", "FAILED"))
    act.add_argument("--retry", action="store_true")
    act.add_argument("--takeover", action="store_true")
    ap.add_argument("--claim-token", default="")
    ap.add_argument("--prior-claim", default="")
    ap.add_argument("--confirmed-stopped", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--evidence", action="append", default=[])
    ap.add_argument("--thread-id")
    return ap


def run(a):
    action = a.claim or a.status or a.retry or a.takeover
    if a.batch:
        if a.preview or a.text or a.file or a.sheet or a.first_row or a.last_row or a.kind != "auto":
            raise StateError("Use either batch lookup/advance or new intake, not both")
        if bool(a.item) != bool(action):
            raise StateError("An item and one action are required together")
        if (a.claim_token and not a.status or a.thread_id and not a.status or
            (a.prior_claim or a.confirmed_stopped) and not a.takeover or
            a.evidence and not (a.status or a.takeover) or a.note and not (a.status or a.retry or a.takeover)):
            raise StateError("Option does not apply to the requested batch action")
        inbox = Inbox(a.state_dir)
        if a.claim:
            return inbox.claim(a.batch, a.item)
        if a.status:
            return inbox.report(a.batch, a.item, a.claim_token, a.status, a.note, a.evidence, a.thread_id)
        if a.retry:
            return inbox.retry(a.batch, a.item, a.note)
        if a.takeover:
            return inbox.takeover(a.batch, a.item, a.prior_claim, a.confirmed_stopped, a.note, a.evidence)
        return inbox.show(a.batch)
    if action or a.item or a.claim_token or a.prior_claim or a.confirmed_stopped or a.note or a.evidence or a.thread_id:
        raise StateError("Specify the batch and an item before advancing its state")
    manifest = prepare(a.text, a.file, a.kind, sheet=a.sheet, first_row=a.first_row, last_row=a.last_row)
    if a.preview or not manifest["items"]:
        return dict(manifest, state="PREVIEW" if manifest["items"] else "NEEDS_INPUT",
                    workflow_profile=a.workflow_profile, router="current_session", batch_id=None)
    return Inbox(a.state_dir).submit(manifest, a.workflow_profile)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        from sa_runtime import ITERATION_POLICY_FILE
        result = run(parser().parse_args())
        # Host-as-Router receives the same installed authority as a spawned role.
        # Add it to the response, not the user manifest or the stable batch identity.
        result["iteration_policy_file"] = str(ITERATION_POLICY_FILE)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, StateError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": {"message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
