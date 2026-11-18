"""Durable local control state. Transport and model decisions live elsewhere."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path


class StateError(Exception):
    pass


TERMINAL = {"CLOSED", "FAILED", "CANCELED", "HANDOFF"}
LIFECYCLES = {"RUNNING", "WAITING_HUMAN", "PAUSED", "BLOCKED"} | TERMINAL
ACTIVE_AGENTS = {"RESERVED", "STARTING", "PENDING", "READY", "RUNNING", "BLOCKED", "UNCERTAIN"}
WRITERS = {"dev-worker", "implementer", "simplifier"}
DEFAULT_LIMITS = {
    "max_agents": 8, "max_flows": 2,
    "roles": {"leader": 2, "dev-worker": 3, "review-worker": 1, "test-worker": 2},
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def default_root():
    if os.environ.get("HERDR_STATE_DIR"):
        return Path(os.environ["HERDR_STATE_DIR"]).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_STATE_HOME")
    return Path(base or (Path.home() / ".local" / "state")) / "herdr-superagent"


def normalized_path(path):
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(path))))


def safe_key(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


class Store:
    def __init__(self, root=None, clock=time.time):
        self.root = Path(root or default_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db_path = self.root / "state.sqlite3"
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY, cwd TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
                    parent TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, generation INTEGER NOT NULL,
                    spec_rev INTEGER NOT NULL, state_version INTEGER NOT NULL DEFAULT 1,
                    brief TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
                    max_runtime_sec INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    assignment TEXT, handoff TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    thread_id TEXT NOT NULL REFERENCES threads(id), id TEXT NOT NULL,
                    generation INTEGER NOT NULL, attempt INTEGER NOT NULL, spec_rev INTEGER NOT NULL,
                    code_revision TEXT NOT NULL, phase TEXT NOT NULL, rejections INTEGER NOT NULL DEFAULT 0,
                    artifact_id TEXT NOT NULL DEFAULT '', environment_revision TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
                    decision_ref TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
                    last_gate TEXT NOT NULL DEFAULT 'PENDING', workspace TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(thread_id, id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
                    trusted INTEGER NOT NULL, payload TEXT NOT NULL, payload_ref TEXT NOT NULL,
                    status TEXT NOT NULL, recipient TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                    next_try REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                    lease_token TEXT NOT NULL DEFAULT '', claimed_by TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_due ON events(status, next_try);
                CREATE TABLE IF NOT EXISTS agents (
                    name TEXT PRIMARY KEY, role TEXT NOT NULL, cwd TEXT NOT NULL, thread_id TEXT NOT NULL,
                    address TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, request TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    entity TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
                );
            """)
            # Concurrent CLIs may first encounter the same older database. Inspect and
            # migrate under the write lock, never while a sibling can ALTER the same table.
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version >= 3:
                if version != 3:
                    raise StateError(f"unsupported state schema version: {version}")
                return
            db.execute("BEGIN IMMEDIATE")
            try:
                task_columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
                thread_columns = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
                if "last_gate" not in task_columns:
                    db.execute("ALTER TABLE tasks ADD COLUMN last_gate TEXT NOT NULL DEFAULT 'PENDING'")
                    db.execute("""UPDATE tasks SET last_gate=phase
                                  WHERE phase IN ('DONE','APPROVED','DEPLOYED','PASS')""")
                if "workspace" not in task_columns:
                    db.execute("ALTER TABLE tasks ADD COLUMN workspace TEXT NOT NULL DEFAULT ''")
                for key in ("assignment", "handoff"):
                    if key not in thread_columns:
                        db.execute(f"ALTER TABLE threads ADD COLUMN {key} TEXT")
                db.execute("PRAGMA user_version=3")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return contextlib.closing(db)

    @contextlib.contextmanager
    def _tx(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _row(self, row):
        if row is None:
            return None
        result = dict(row)
        for key in ("payload", "request", "evidence", "assignment", "handoff"):
            if key in result and result[key] is not None:
                result[key] = json.loads(result[key])
        if "trusted" in result:
            result["trusted"] = bool(result["trusted"])
        return result

    def _one(self, db, table, key, value):
        row = db.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
        if row is None:
            raise StateError(f"not_found: {table} {value}")
        return self._row(row)

    def _audit(self, db, entity, action, detail):
        db.execute("INSERT INTO audit(at,entity,action,detail) VALUES(?,?,?,?)",
                   (self.clock(), entity, action, canonical(detail)))

    def _limits(self, db):
        row = db.execute("SELECT value FROM settings WHERE key='limits'").fetchone()
        return json.loads(row[0]) if row else json.loads(canonical(DEFAULT_LIMITS))

    def limits(self):
        with self._connection() as db:
            return self._limits(db)

    def set_limits(self, **changes):
        with self._tx() as db:
            limits = self._limits(db)
            for key, value in changes.items():
                if key not in DEFAULT_LIMITS:
                    raise StateError(f"unknown limit: {key}")
                if key == "roles":
                    if not isinstance(value, dict):
                        raise StateError("roles must be an object")
                    for role, count in value.items():
                        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                            raise StateError(f"invalid role capacity: {role}")
                    limits["roles"].update(value)
                elif not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise StateError(f"invalid capacity: {key}")
                else:
                    limits[key] = value
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('limits',?)",
                       (canonical(limits),))
            self._audit(db, "limits", "update", limits)
            return limits

    @staticmethod
    def _write_roots(cwd, assignment):
        if not assignment:
            return {cwd}
        if assignment["mode"] == "read_only":
            return set()
        return {normalized_path(path) for path in assignment["workspaces"].values()}

    def _require_flow_slot(self, db, cwd, assignment):
        marks = ",".join("?" for _ in TERMINAL)
        active = db.execute(f"SELECT cwd,assignment FROM threads WHERE status NOT IN ({marks})",
                            tuple(TERMINAL)).fetchall()
        if len(active) >= self._limits(db)["max_flows"]:
            raise StateError("capacity: active flow limit reached")
        roots = self._write_roots(cwd, assignment)
        for other in active:
            other_project = json.loads(other["assignment"]) if other["assignment"] else None
            other_roots = self._write_roots(other["cwd"], other_project)
            if not assignment and not other_project:
                conflict = bool(roots & other_roots)  # retain the original legacy contract
            else:
                conflict = any(Path(a).is_relative_to(b) or Path(b).is_relative_to(a)
                               for a in roots for b in other_roots)
            if conflict:
                raise StateError("workspace is already owned by an active thread")

    @staticmethod
    def _require_matching_global_flow(thread_id, assignment):
        source = (assignment or {}).get("workflow_source")
        if source in ("global-story", "global-bug"):
            # Only the canonical intake identity is authoritative here. Legacy/local
            # thread names and numeric project_ref alone do not determine a kind.
            key = re.fullmatch(r"tapd:[0-9]+:(story|bug):[0-9]+", thread_id)
            if key and source != "global-" + key[1]:
                raise StateError(f"TAPD {key[1]} thread cannot select {source}")

    def create_thread(self, thread_id, cwd, *, owner="", parent="", brief="", max_runtime_sec=14400,
                      assignment=None):
        if not thread_id or len(thread_id) > 256 or any(ord(c) < 32 for c in thread_id):
            raise StateError("invalid thread id")
        self._require_matching_global_flow(thread_id, assignment)
        cwd = normalized_path(cwd)
        if not Path(cwd).is_dir() or max_runtime_sec < 1:
            raise StateError("workspace or runtime budget invalid")
        with self._tx() as db:
            if db.execute("SELECT 1 FROM threads WHERE id=?", (thread_id,)).fetchone():
                raise StateError("thread exists; use reopen or resume")
            self._require_flow_slot(db, cwd, assignment)
            now = self.clock()
            db.execute("""INSERT INTO threads
                (id,cwd,owner,parent,status,generation,spec_rev,brief,max_runtime_sec,created,updated,assignment)
                VALUES(?,?,?,?,'RUNNING',1,1,?,?,?,?,?)""",
                       (thread_id, cwd, owner, parent, brief, max_runtime_sec, now, now,
                        canonical(assignment) if assignment is not None else None))
            self._audit(db, thread_id, "create", {"cwd": cwd, "parent": parent})
        return self.get_thread(thread_id)

    def get_thread(self, thread_id):
        with self._connection() as db:
            return self._one(db, "threads", "id", thread_id)

    def list_threads(self):
        with self._connection() as db:
            return [self._row(row) for row in db.execute("SELECT * FROM threads ORDER BY created,id")]

    def assign_owner(self, thread_id, address, *, generation=None):
        if not address:
            raise StateError("owner address required")
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            self._assign_owner(db, thread, address, generation=generation)
        return self.get_thread(thread_id)

    def _assign_owner(self, db, thread, address, *, generation=None):
        thread_id = thread["id"]
        if generation is not None and generation != thread["generation"]:
            raise StateError("stale generation")
        if thread["status"] in TERMINAL:
            raise StateError("terminal thread must be reopened before assigning an owner")
        if thread["owner"] and thread["owner"] != address:
            db.execute("UPDATE threads SET generation=generation+1 WHERE id=?", (thread_id,))
            db.execute("""UPDATE tasks SET generation=generation+1,attempt=attempt+1,
                          phase='PENDING',last_gate='PENDING',artifact_id='',environment_revision='',
                          evidence='[]',summary='',decision_ref='',updated=? WHERE thread_id=?""",
                       (self.clock(), thread_id))
        db.execute("UPDATE threads SET owner=?,updated=? WHERE id=?",
                   (address, self.clock(), thread_id))
        self._audit(db, thread_id, "owner", {"owner": address})
        return self._one(db, "threads", "id", thread_id)

    def cancel_empty_thread(self, thread_id, reason):
        """Compensate only an automatic, ownerless start with no live reservations."""
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            agents = db.execute("SELECT status FROM agents WHERE thread_id=?", (thread_id,)).fetchall()
            if (thread["status"] != "RUNNING" or thread["owner"] or
                    any(row["status"] in ACTIVE_AGENTS for row in agents) or
                    db.execute("SELECT 1 FROM tasks WHERE thread_id=?", (thread_id,)).fetchone()):
                return thread
            db.execute("""UPDATE threads SET status='CANCELED',summary=?,state_version=state_version+1,
                          updated=? WHERE id=?""", (reason, self.clock(), thread_id))
            self._audit(db, thread_id, "cancel_empty_start", {"reason": reason})
        return self.get_thread(thread_id)

    def _notice(self, db, thread, kind, payload, recipient=None):
        recipient = recipient if recipient is not None else thread["parent"]
        if not recipient:
            return None
        return self._enqueue(db, "internal", thread["id"], payload, kind=kind,
                             trusted=True, recipient=recipient,
                             event_key=uuid.uuid4().hex)

    def set_thread_status(self, thread_id, status, *, generation=None, summary=""):
        status = status.upper()
        if status == "HANDOFF":
            raise StateError("use thread handoff with evidence and current versions")
        if status not in LIFECYCLES:
            raise StateError("unknown lifecycle status")
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            if generation is not None and generation != thread["generation"]:
                raise StateError("stale generation")
            if thread["status"] in TERMINAL and status != thread["status"]:
                raise StateError("terminal thread must be reopened explicitly")
            if status == "CLOSED":
                if thread["assignment"]:
                    raise StateError("a project assignment ends with thread handoff, not project acceptance")
                tasks = db.execute("SELECT * FROM tasks WHERE thread_id=?", (thread_id,)).fetchall()
                if not tasks or any(t["phase"] != "PASS" or
                                    t["generation"] != thread["generation"] or
                                    t["spec_rev"] != thread["spec_rev"] for t in tasks):
                    raise StateError("gate: all current task versions must PASS before closing")
            if status == thread["status"]:
                return thread
            db.execute("""UPDATE threads SET status=?,summary=?,state_version=state_version+1,
                          updated=? WHERE id=?""", (status, summary, self.clock(), thread_id))
            updated = self._one(db, "threads", "id", thread_id)
            kind = "THREAD_DONE" if status == "CLOSED" else f"THREAD_{status}"
            self._notice(db, updated, kind, {
                "thread_id": thread_id, "status": status, "generation": updated["generation"],
                "spec_rev": updated["spec_rev"], "summary": summary,
            })
            self._audit(db, thread_id, "status", {"from": thread["status"], "to": status})
        return self.get_thread(thread_id)

    def handoff_thread(self, thread_id, *, generation, spec_rev, summary, evidence):
        """Return this commission without advancing project acceptance or existing task gates."""
        summary = summary.strip()
        if not summary or not evidence:
            raise StateError("handoff needs a stage conclusion and existing evidence")
        paths = list(dict.fromkeys(str(Path(path).resolve()) for path in evidence))
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            if (generation, spec_rev) != (thread["generation"], thread["spec_rev"]):
                raise StateError("stale generation/spec revision for handoff")
            assignment = thread["assignment"]
            if not assignment:
                raise StateError("only project assignments can use handoff")
            receipt = {"thread_id": thread_id, "status": "HANDOFF", "generation": generation,
                       "spec_rev": spec_rev, "project_ref": assignment["project_ref"],
                       "stop_at": assignment["stop_at"], "summary": summary, "evidence": paths}
            if thread["status"] == "HANDOFF":
                if thread["handoff"] != receipt:
                    raise StateError("assignment was already handed off with a different conclusion")
                return thread
            if thread["status"] != "RUNNING":
                raise StateError("only a running assignment can hand off")
            for path in paths:
                if not Path(path).is_file():
                    raise StateError(f"handoff evidence file missing: {path}")
            now = self.clock()
            db.execute("""UPDATE threads SET status='HANDOFF',handoff=?,summary=?,state_version=state_version+1,
                          updated=? WHERE id=?""", (canonical(receipt), summary, now, thread_id))
            db.execute("""UPDATE events SET status='SUPERSEDED',updated=? WHERE thread_id=?
                          AND trusted=1 AND kind='TASK_RESULT' AND status='PENDING'""", (now, thread_id))
            self._notice(db, thread, "THREAD_HANDOFF", receipt)
            self._audit(db, thread_id, "handoff", receipt)
            return self._one(db, "threads", "id", thread_id)

    @staticmethod
    def _require_task_authority(thread):
        if thread["assignment"] and thread["assignment"]["mode"] == "read_only":
            raise StateError("read_only project assignment cannot advance tasks")

    def revise_thread(self, thread_id, *, generation=None):
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            if generation is not None and generation != thread["generation"]:
                raise StateError("stale generation")
            self._require_task_authority(thread)
            if thread["status"] in TERMINAL:
                raise StateError("terminal thread must be reopened")
            db.execute("UPDATE threads SET spec_rev=spec_rev+1,updated=? WHERE id=?",
                       (self.clock(), thread_id))
            db.execute("""UPDATE tasks SET attempt=attempt+1,spec_rev=spec_rev+1,phase='PENDING',last_gate='PENDING',
                          artifact_id='',environment_revision='',evidence='[]',summary='',decision_ref='',
                          updated=? WHERE thread_id=?""", (self.clock(), thread_id))
            self._audit(db, thread_id, "revise", {"spec_rev": thread["spec_rev"] + 1})
        return self.get_thread(thread_id)

    def reopen_thread(self, thread_id, *, assignment=None):
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            if thread["status"] not in TERMINAL:
                raise StateError("only a terminal thread can reopen")
            if thread["assignment"] is not None and assignment is None:
                raise StateError("new --assignment-file required when reopening a project assignment")
            if thread["assignment"] is None and assignment is not None:
                raise StateError("legacy threads must keep their existing contract; create a project assignment")
            self._require_matching_global_flow(thread_id, assignment)
            if assignment and (normalized_path(assignment["project_root"]) !=
                               normalized_path(thread["assignment"]["project_root"]) or
                               assignment["project_ref"] != thread["assignment"]["project_ref"]):
                raise StateError("reopen must continue the same project and project_ref")
            if assignment:
                reservations = db.execute("SELECT status FROM agents WHERE thread_id=?", (thread_id,))
                if any(row["status"] in ACTIVE_AGENTS for row in reservations):
                    raise StateError("previous assignment still has active sessions; let the driver reclaim "
                                     "them or reconcile with agent stop/abandon-start before reopening")
            self._require_flow_slot(db, thread["cwd"], assignment)
            db.execute("""UPDATE threads SET status='RUNNING',generation=generation+1,
                          spec_rev=spec_rev+1,state_version=state_version+1,owner='',summary='',handoff=NULL,
                          assignment=?,created=?,updated=? WHERE id=?""",
                       (canonical(assignment) if assignment else None, self.clock(), self.clock(), thread_id))
            if assignment:
                # A new authorization does not reassign the old tasks. Preserve the prior
                # receipt and its exact task snapshots before allowing original IDs to be reused.
                tasks = [self._row(r) for r in db.execute("SELECT * FROM tasks WHERE thread_id=?", (thread_id,))]
                self._audit(db, thread_id, "archive_assignment", {"generation": thread["generation"],
                            "assignment": thread["assignment"], "handoff": thread["handoff"], "tasks": tasks})
                db.execute("DELETE FROM tasks WHERE thread_id=?", (thread_id,))
            else:
                db.execute("""UPDATE tasks SET generation=generation+1,spec_rev=spec_rev+1,
                              attempt=attempt+1,phase='PENDING',last_gate='PENDING',artifact_id='',environment_revision='',
                              evidence='[]',decision_ref='',rejections=0,updated=? WHERE thread_id=?""",
                           (self.clock(), thread_id))
            self._audit(db, thread_id, "reopen", {"generation": thread["generation"] + 1})
        return self.get_thread(thread_id)

    @staticmethod
    def project_workspace(thread, key):
        workspaces = thread["assignment"]["workspaces"]
        if not key and len(workspaces) == 1:
            key = next(iter(workspaces))
        if key not in workspaces:
            raise StateError("choose a registered task --workspace from the project assignment")
        return key, workspaces[key]

    def create_task(self, thread_id, task_id, code_revision, *, workspace="", expected_version=None):
        self._validate_revision(code_revision)
        if not task_id:
            raise StateError("task id required")
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            self._require_task_authority(thread)
            if expected_version is not None and expected_version != (thread["generation"], thread["spec_rev"]):
                raise StateError("stale project assignment during task registration")
            if thread["assignment"]:
                workspace, _ = self.project_workspace(thread, workspace)
                if code_revision.startswith("patch:"):
                    raise StateError("a workspace-bound code task needs a Git revision")
            elif workspace:
                raise StateError("--workspace is only available for project assignments")
            if thread["status"] != "RUNNING":
                raise StateError("thread is not running")
            try:
                db.execute("""INSERT INTO tasks
                    (thread_id,id,generation,attempt,spec_rev,code_revision,phase,updated,workspace)
                    VALUES(?,?,?,1,?,?,'PENDING',?,?)""",
                           (thread_id, task_id, thread["generation"], thread["spec_rev"],
                            code_revision, self.clock(), workspace))
            except sqlite3.IntegrityError as exc:
                raise StateError("task exists; use retry for a new version") from exc
        return self.get_task(thread_id, task_id)

    @staticmethod
    def _validate_revision(revision):
        if not re.fullmatch(r"(?:[0-9a-fA-F]{40,64}|patch:[0-9a-fA-F]{64})", revision or ""):
            raise StateError("code revision must be a commit SHA or patch:SHA256")

    def get_task(self, thread_id, task_id):
        with self._connection() as db:
            row = db.execute("SELECT * FROM tasks WHERE thread_id=? AND id=?",
                             (thread_id, task_id)).fetchone()
            if row is None:
                raise StateError("task not_found")
            return self._row(row)

    def list_tasks(self, thread_id):
        with self._connection() as db:
            return [self._row(row) for row in db.execute(
                "SELECT * FROM tasks WHERE thread_id=? ORDER BY id", (thread_id,))]

    def retry_task(self, thread_id, task_id, code_revision, *, expected_version=None):
        self._validate_revision(code_revision)
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            self._require_task_authority(thread)
            if expected_version is not None and expected_version != (thread["generation"], thread["spec_rev"]):
                raise StateError("stale project assignment during task retry")
            if thread["assignment"] and code_revision.startswith("patch:"):
                raise StateError("a workspace-bound code task needs a Git revision")
            if thread["status"] != "RUNNING":
                raise StateError("thread is not running")
            count = db.execute("""UPDATE tasks SET attempt=attempt+1,spec_rev=?,generation=?,
                code_revision=?,phase='PENDING',last_gate='PENDING',artifact_id='',environment_revision='',evidence='[]',
                summary='',decision_ref='',updated=? WHERE thread_id=? AND id=?""",
                               (thread["spec_rev"], thread["generation"], code_revision,
                                self.clock(), thread_id, task_id)).rowcount
            if not count:
                raise StateError("task not_found")
            self._audit(db, f"{thread_id}/{task_id}", "retry", {"code_revision": code_revision})
        return self.get_task(thread_id, task_id)

    def report(self, thread_id, task_id, attempt, spec_rev, code_revision, kind, *,
               evidence=None, artifact_id="", environment_revision="", summary="", decision_ref="",
               base_revision=""):
        kind = kind.upper()
        evidence = evidence or []
        allowed = {
            "DONE": {"PENDING"}, "APPROVED": {"DONE", "BLOCKED"},
            "REJECTED": {"DONE"}, "DEPLOYED": {"APPROVED"}, "PASS": {"DEPLOYED"},
            "FAIL": {"DEPLOYED"}, "BLOCKED": {"PENDING", "DONE", "APPROVED", "DEPLOYED"},
        }
        with self._tx() as db:
            thread = self._one(db, "threads", "id", thread_id)
            self._require_task_authority(thread)
            if thread["assignment"] and code_revision.startswith("patch:"):
                raise StateError("a workspace-bound code task needs a Git revision")
            row = db.execute("SELECT * FROM tasks WHERE thread_id=? AND id=?",
                             (thread_id, task_id)).fetchone()
            if row is None:
                raise StateError("task not_found")
            task = self._row(row)
            if (attempt != task["attempt"] or spec_rev != task["spec_rev"] or
                    spec_rev != thread["spec_rev"] or
                    task["generation"] != thread["generation"]):
                raise StateError("stale task result: attempt/spec/code/generation mismatch")
            if base_revision and base_revision != task["code_revision"]:
                raise StateError("stale assigned base revision")
            if code_revision != task["code_revision"]:
                if kind != "DONE" or task["phase"] != "PENDING" or base_revision != task["code_revision"]:
                    raise StateError("stale code revision")
                self._validate_revision(code_revision)
            if thread["status"] != "RUNNING":
                raise StateError("thread is not running")
            if kind == "APPROVED" and task["rejections"] >= 2 and not decision_ref:
                raise StateError("decision required after two rejected reviews")
            if kind not in allowed or task["phase"] not in allowed[kind]:
                raise StateError(f"invalid transition: {task['phase']} -> {kind}")
            if kind == "APPROVED" and task["phase"] == "BLOCKED" and not decision_ref:
                raise StateError("decision required to approve a blocked task")
            if kind == "APPROVED" and task["last_gate"] not in {"DONE", "APPROVED", "DEPLOYED"}:
                raise StateError("DONE gate required before approval, including adjudicated work")
            if kind == "DEPLOYED" and (
                    not artifact_id or environment_revision != code_revision):
                raise StateError("version: deployment must identify an artifact and match code revision")
            if kind in {"PASS", "FAIL"} and not evidence:
                raise StateError("evidence required for test results")
            if kind in {"REJECTED", "BLOCKED", "FAIL"} and not summary:
                raise StateError("failure/blocker summary required")
            rejections = task["rejections"] + (1 if kind == "REJECTED" else 0)
            phase = "BLOCKED" if kind == "REJECTED" and rejections >= 2 else kind
            last_gate = kind if kind in {"DONE", "APPROVED", "DEPLOYED", "PASS"} else task["last_gate"]
            db.execute("""UPDATE tasks SET code_revision=?,phase=?,rejections=?,artifact_id=?,environment_revision=?,
                          evidence=?,summary=?,decision_ref=?,last_gate=?,updated=? WHERE thread_id=? AND id=?""",
                       (code_revision, phase, rejections, artifact_id or task["artifact_id"],
                        environment_revision or task["environment_revision"], canonical(evidence),
                        summary, decision_ref, last_gate, self.clock(), thread_id, task_id))
            self._notice(db, thread, "TASK_RESULT", {
                "thread_id": thread_id, "generation": thread["generation"], "task_id": task_id, "attempt": attempt,
                "spec_rev": spec_rev, "code_revision": code_revision, "kind": kind, "phase": phase,
                "evidence": evidence, "summary": summary,
            }, recipient=thread["owner"])
            self._audit(db, f"{thread_id}/{task_id}", "report", {"kind": kind, "attempt": attempt})
        return self.get_task(thread_id, task_id)

    def _enqueue(self, db, source, thread_id, payload, *, kind="EXTERNAL", trusted=False,
                 recipient="", event_key=None):
        encoded = canonical(payload)
        event_id = hashlib.sha256(
            f"{source}\0{thread_id}\0{kind}\0{event_key or encoded}".encode("utf-8")
        ).hexdigest()
        existing = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if existing:
            return self._row(existing)
        out_dir = self.root / "events"
        out_dir.mkdir(exist_ok=True)
        out_file = out_dir / f"{event_id}.json"
        temporary = out_dir / f".{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, out_file)
        finally:
            if temporary.exists():
                temporary.unlink()
        now = self.clock()
        db.execute("""INSERT INTO events
            (id,thread_id,source,kind,trusted,payload,payload_ref,status,recipient,created,updated)
            VALUES(?,?,?,?,?,?,?,'PENDING',?,?,?)""",
                   (event_id, thread_id, source, kind, int(trusted), encoded,
                    str(out_file), recipient, now, now))
        return self._one(db, "events", "id", event_id)

    def enqueue_event(self, source, thread_id, payload, *, event_key=None):
        if not source or not thread_id:
            raise StateError("source and namespaced thread id required")
        with self._tx() as db:
            return self._enqueue(db, source, thread_id, payload, event_key=event_key)

    def notify(self, kind, payload, *, recipient, thread_id="system", event_key=None):
        """For control-plane code only; the public enqueue CLI always creates EXTERNAL data."""
        with self._tx() as db:
            return self._enqueue(db, "internal", thread_id, payload, kind=kind,
                                 trusted=True, recipient=recipient, event_key=event_key)

    def get_event(self, event_id):
        with self._connection() as db:
            return self._one(db, "events", "id", event_id)

    def list_events(self, *, kind=None, statuses=None):
        sql, params = "SELECT * FROM events WHERE 1=1", []
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if statuses:
            sql += f" AND status IN ({','.join('?' for _ in statuses)})"
            params.extend(statuses)
        with self._connection() as db:
            return [self._row(row) for row in db.execute(sql + " ORDER BY created,id", params)]

    def _obsolete_project_result(self, db, event):
        if not event["trusted"] or event["kind"] != "TASK_RESULT":
            return False
        thread = self._one(db, "threads", "id", event["thread_id"])
        return bool(thread["assignment"] and (
            thread["status"] in TERMINAL or event["payload"].get("generation") != thread["generation"]))

    def acquire_delivery(self, event_id, *, lease_seconds=120):
        with self._tx() as db:
            event = self._one(db, "events", "id", event_id)
            now = self.clock()
            if event["status"] == "DELIVERING" and event["lease_until"] <= now:
                db.execute("""UPDATE events SET status='UNCERTAIN',last_error=?,updated=? WHERE id=?""",
                           ("dispatcher lease expired; reconcile before retry", now, event_id))
                return None
            if event["status"] != "PENDING" or event["next_try"] > now:
                return None
            if self._obsolete_project_result(db, event):
                db.execute("UPDATE events SET status='SUPERSEDED',updated=? WHERE id=?", (now, event_id))
                return None
            token = uuid.uuid4().hex
            db.execute("""UPDATE events SET status='DELIVERING',attempts=attempts+1,
                          lease_until=?,lease_token=?,updated=? WHERE id=?""",
                       (now + lease_seconds, token, now, event_id))
            return self._one(db, "events", "id", event_id)

    def finish_delivery(self, event_id, lease_token, recipient, outcome, error=""):
        statuses = {"sent": "AWAITING_ACK", "retry": "PENDING", "uncertain": "UNCERTAIN",
                    "dead": "DEAD", "defer": "PENDING"}
        if outcome not in statuses:
            raise StateError("invalid delivery outcome")
        with self._tx() as db:
            event = self._one(db, "events", "id", event_id)
            if event["lease_token"] != lease_token:
                raise StateError("delivery lease mismatch")
            if event["status"] in {"CLAIMED", "DONE"}:
                return event
            delay = min(300, 5 * 2 ** min(event["attempts"], 6))
            db.execute("""UPDATE events SET status=?,recipient=?,last_error=?,next_try=?,
                          lease_until=0,updated=? WHERE id=?""",
                       (statuses[outcome], recipient, error, self.clock() + delay,
                        self.clock(), event_id))
        return self.get_event(event_id)

    def retry_event(self, event_id):
        with self._tx() as db:
            event = self._one(db, "events", "id", event_id)
            if event["status"] not in {"UNCERTAIN", "DEAD", "AWAITING_ACK"}:
                raise StateError("event is not awaiting explicit retry reconciliation")
            db.execute("""UPDATE events SET status='PENDING',next_try=0,last_error='',
                          lease_until=0,lease_token='',updated=? WHERE id=?""",
                       (self.clock(), event_id))
            self._audit(db, event_id, "manual_retry", {"previous_status": event["status"]})
        return self.get_event(event_id)

    def recover_event(self, event_id, owner, decision_ref):
        if not decision_ref:
            raise StateError("recovery decision required")
        with self._tx() as db:
            event = self._one(db, "events", "id", event_id)
            if event["status"] != "CLAIMED":
                raise StateError("only a claimed event needs consumer recovery")
            if not owner or event["claimed_by"] != owner:
                raise StateError("previous event owner mismatch")
            db.execute("""UPDATE events SET status='PENDING',claimed_by='',recipient=CASE
                          WHEN trusted=0 THEN '' ELSE recipient END,next_try=0,last_error='',
                          lease_until=0,lease_token='',updated=? WHERE id=?""", (self.clock(), event_id))
            self._audit(db, event_id, "recover_unexecuted", {"owner": owner, "decision_ref": decision_ref})
        return self.get_event(event_id)

    def claim_event(self, event_id, owner):
        if not owner:
            raise StateError("event owner required")
        with self._tx() as db:
            event = self._one(db, "events", "id", event_id)
            if event["status"] in {"CLAIMED", "DONE"}:
                return {"claimed": False, "status": event["status"], "owner": event["claimed_by"]}
            if event["status"] == "SUPERSEDED" or self._obsolete_project_result(db, event):
                return {"claimed": False, "status": "SUPERSEDED", "owner": ""}
            if event["recipient"] and event["recipient"] != owner:
                raise StateError("event owner does not match recipient")
            db.execute("UPDATE events SET status='CLAIMED',claimed_by=?,updated=? WHERE id=?",
                       (owner, self.clock(), event_id))
            self._audit(db, event_id, "claim", {"owner": owner})
            return {"claimed": True, "event": self._one(db, "events", "id", event_id)}

    def complete_event(self, event_id, owner):
        with self._tx() as db:
            event = self._one(db, "events", "id", event_id)
            if event["claimed_by"] != owner:
                raise StateError("event owner mismatch")
            if event["status"] not in {"CLAIMED", "DONE"}:
                raise StateError("event must be claimed first")
            db.execute("UPDATE events SET status='DONE',updated=? WHERE id=?", (self.clock(), event_id))
            self._audit(db, event_id, "complete", {"owner": owner})
        return self.get_event(event_id)

    def reserve_agent(self, name, role, cwd, *, thread_id="", request=None):
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", name or ""):
            raise StateError("agent name must be 1-32 lowercase letters, digits, - or _")
        cwd = normalized_path(cwd)
        with self._tx() as db:
            active = [self._row(row) for row in db.execute(
                f"SELECT * FROM agents WHERE status IN ({','.join('?' for _ in ACTIVE_AGENTS)})",
                tuple(ACTIVE_AGENTS))]
            if any(a["name"] == name for a in active):
                raise StateError("agent name already reserved; resume instead")
            limits = self._limits(db)
            if len(active) >= limits["max_agents"]:
                raise StateError("capacity: global agent limit reached")
            bucket = {"implementer": "dev-worker", "simplifier": "dev-worker",
                      "reviewer": "review-worker", "tester": "test-worker"}.get(role, role)
            bucket_roles = {
                "dev-worker": {"dev-worker", "implementer", "simplifier"},
                "review-worker": {"review-worker", "reviewer"},
                "test-worker": {"test-worker", "tester"},
            }.get(bucket, {role})
            if sum(a["role"] in bucket_roles for a in active) >= limits["roles"].get(bucket, 1):
                raise StateError(f"capacity: role limit reached for {bucket}")
            if role in WRITERS:
                scope = normalized_path((request or {}).get("workspace_root") or cwd)
                for other in active:
                    if other["role"] not in WRITERS:
                        continue
                    other_scope = normalized_path(other["request"].get("workspace_root") or other["cwd"])
                    try:
                        overlaps = os.path.commonpath([scope, other_scope]) in {scope, other_scope}
                    except ValueError:
                        overlaps = False
                    if overlaps:
                        raise StateError("workspace already has a writable agent")
            if thread_id:
                thread = self._one(db, "threads", "id", thread_id)
                if thread["status"] != "RUNNING":
                    raise StateError("thread is not running")
                if request and (request.get("generation") != thread["generation"] or
                                request.get("spec_rev") != thread["spec_rev"]):
                    raise StateError("stale startup thread context")
            now = self.clock()
            db.execute("""INSERT OR REPLACE INTO agents
                (name,role,cwd,thread_id,address,status,request,created,updated,last_error)
                VALUES(?,?,?,?,'','RESERVED',?,?,?,'')""",
                       (name, role, cwd, thread_id, canonical(request or {}), now, now))
            self._audit(db, name, "reserve", {"role": role, "thread_id": thread_id})
        return self.agent(name)

    def agent(self, name):
        with self._connection() as db:
            return self._one(db, "agents", "name", name)

    def list_agents(self, *, active=False):
        with self._connection() as db:
            rows = [self._row(row) for row in db.execute("SELECT * FROM agents ORDER BY created,name")]
        return [row for row in rows if not active or row["status"] in ACTIVE_AGENTS]

    def update_agent(self, name, *, status=None, address=None, error=None, request=None):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            db.execute("""UPDATE agents SET status=?,address=?,last_error=?,request=?,updated=?
                          WHERE name=?""",
                       (status or agent["status"], agent["address"] if address is None else address,
                        agent["last_error"] if error is None else error,
                        canonical(agent["request"] if request is None else request), self.clock(), name))
        return self.agent(name)

    def _save_agent(self, db, agent):
        db.execute("""UPDATE agents SET address=?,status=?,request=?,last_error=?,updated=? WHERE name=?""",
                   (agent["address"], agent["status"], canonical(agent["request"]),
                    agent["last_error"], self.clock(), agent["name"]))

    @staticmethod
    def _clear_start_lease(request):
        request.pop("start_lease_token", None)
        request.pop("start_lease_until", None)

    def _require_start_current(self, db, agent, *, pending=True, bind_owner=False):
        if not agent["thread_id"]:
            return None
        thread = self._one(db, "threads", "id", agent["thread_id"])
        request = agent["request"]
        if pending and thread["status"] != "RUNNING":
            raise StateError("thread is not running; pending task retained")
        if ("generation" in request and request["generation"] != thread["generation"] or
                (pending or agent["role"] != "leader") and "spec_rev" in request and
                request["spec_rev"] != thread["spec_rev"]):
            raise StateError("stale startup thread context")
        if (agent["role"] == "leader" and not bind_owner and agent["address"] and
                thread["owner"] != agent["address"]):
            raise StateError("stale leader owner")
        row = None
        if request.get("task_id"):
            row = db.execute("SELECT * FROM tasks WHERE thread_id=? AND id=?",
                             (agent["thread_id"], request["task_id"])).fetchone()
            if (not row or row["attempt"] != request.get("attempt") or
                    row["spec_rev"] != request.get("spec_rev") or
                    pending and row["code_revision"] != request.get("code_revision")):
                raise StateError("stale startup task context")
        if pending and thread["assignment"]:
            if request.get("assignment") != thread["assignment"]:
                raise StateError("stale project assignment at startup")
            from sa_project import validate_project_start
            validate_project_start(thread["assignment"], agent["role"], agent["cwd"],
                                   task_id=request.get("task_id", ""),
                                   workspace_key=row["workspace"] if row else "")
        return thread

    def agent_context_error(self, name):
        with self._connection() as db:
            try:
                self._require_start_current(db, self._one(db, "agents", "name", name), pending=False)
            except StateError as exc:
                return str(exc)
        return ""

    def complete_launch(self, name, address, *, lease_token=None, preserve_lease=False):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            request = agent["request"]
            if agent["status"] not in ACTIVE_AGENTS or not address:
                raise StateError("startup reservation is no longer active")
            if lease_token:
                if request.get("start_lease_token") != lease_token:
                    raise StateError("startup lease mismatch")
            elif (request.get("delivery") not in {"CREATING", "UNCERTAIN"} or
                  request.get("uncertain_stage", "launch") != "launch" or
                  request.get("start_lease_until", 0) > self.clock()):
                raise StateError("only an uncertain launch can be adopted")
            thread = self._require_start_current(db, agent, bind_owner=True)
            if agent["role"] == "leader" and thread:
                if thread["owner"] and thread["owner"] != address and not request.get("replace_owner"):
                    raise StateError("thread already has an owner; explicit replacement required")
                thread = self._assign_owner(db, thread, address, generation=request.get("generation"))
                request.update(generation=thread["generation"], spec_rev=thread["spec_rev"])
            agent.update(address=address, status="PENDING", last_error="")
            request.update(address=address, delivery="PENDING")
            request.pop("uncertain_stage", None)
            if not preserve_lease:
                self._clear_start_lease(request)
            self._save_agent(db, agent)
            self._audit(db, name, "launch_registered", {"address": address})
        return self.agent(name)

    def fail_launch(self, name, lease_token, error, *, uncertain):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            request = agent["request"]
            if request.get("start_lease_token") != lease_token:
                raise StateError("startup lease mismatch")
            request.update(delivery="UNCERTAIN" if uncertain else "FAILED", uncertain_stage="launch")
            self._clear_start_lease(request)
            agent.update(status="UNCERTAIN" if uncertain else "FAILED", last_error=error)
            self._save_agent(db, agent)
        return self.agent(name)

    def acquire_start(self, name, *, lease_seconds=180):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            request, now = agent["request"], self.clock()
            if agent["status"] not in ACTIVE_AGENTS:
                raise StateError("startup reservation is no longer active")
            delivery = request.get("delivery")
            if delivery == "DELIVERED":
                return None
            if request.get("start_lease_until", 0) > now:
                return None
            if delivery in {"CREATING", "SENDING"}:
                request.update(delivery="UNCERTAIN",
                               uncertain_stage="launch" if delivery == "CREATING" else "send")
                self._clear_start_lease(request)
                agent.update(status="UNCERTAIN", last_error="startup lease expired; reconcile before retry")
                self._save_agent(db, agent)
                return None
            if delivery == "UNCERTAIN" or agent["status"] == "UNCERTAIN":
                return None
            if delivery != "PENDING":
                raise StateError("startup is not pending")
            self._require_start_current(db, agent)
            request.update(start_lease_token=uuid.uuid4().hex, start_lease_until=now + lease_seconds)
            self._save_agent(db, agent)
            return agent

    def start_sending(self, name, lease_token):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            if agent["request"].get("start_lease_token") != lease_token:
                raise StateError("startup lease mismatch")
            self._require_start_current(db, agent)
            agent["request"]["delivery"] = "SENDING"
            self._save_agent(db, agent)
        return self.agent(name)

    def finish_start(self, name, lease_token, delivery, *, status=None, error=""):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            request = agent["request"]
            if request.get("start_lease_token") != lease_token:
                raise StateError("startup lease mismatch")
            if request.get("delivery") == "DELIVERED" and agent["status"] == "RUNNING":
                delivery, status, error = "DELIVERED", "RUNNING", ""
            request["delivery"] = delivery
            if delivery == "UNCERTAIN":
                request["uncertain_stage"] = "send"
            else:
                request.pop("uncertain_stage", None)
            self._clear_start_lease(request)
            agent.update(status=status or agent["status"], last_error=error)
            self._save_agent(db, agent)
        return self.agent(name)

    def retry_start(self, name):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            request = agent["request"]
            if request.get("start_lease_until", 0) > self.clock():
                raise StateError("startup still has an active lease")
            if not agent["address"] or request.get("uncertain_stage") == "launch":
                raise StateError("uncertain creation: use adopt-start or abandon-start after inspection")
            if request.get("delivery") not in {"SENDING", "UNCERTAIN"}:
                raise StateError("only an uncertain send needs explicit retry reconciliation")
            self._require_start_current(db, agent)
            request["delivery"] = "PENDING"
            request.pop("uncertain_stage", None)
            self._clear_start_lease(request)
            agent.update(status="PENDING", last_error="")
            self._save_agent(db, agent)
            self._audit(db, name, "retry_start", {"address": agent["address"]})
        return self.agent(name)

    def abandon_start(self, name):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            request = agent["request"]
            if (agent["address"] or request.get("delivery") not in {"CREATING", "UNCERTAIN"} or
                    request.get("uncertain_stage", "launch") != "launch" or
                    request.get("start_lease_until", 0) > self.clock()):
                raise StateError("only a confirmed uncreated startup can be abandoned; use stop for known terminals")
            request["delivery"] = "FAILED"
            self._clear_start_lease(request)
            agent.update(status="FAILED", last_error="operator confirmed no terminal was created")
            self._save_agent(db, agent)
            self._audit(db, name, "abandon_start", {})
        return self.agent(name)

    def release_agent(self, name, reason="confirmed finished"):
        return self.update_agent(name, status="FINISHED", error=reason)

    def ack_agent(self, name, owner):
        with self._tx() as db:
            agent = self._one(db, "agents", "name", name)
            if not owner or agent["address"] != owner:
                raise StateError("startup owner does not match the registered terminal")
            request = agent["request"]
            if (request.get("delivery") not in {"SENDING", "DELIVERED", "UNCERTAIN"} or
                    request.get("uncertain_stage") == "launch"):
                raise StateError("startup task has not been delivered")
            if agent["status"] not in ACTIVE_AGENTS:
                raise StateError("startup reservation is no longer active")
            self._require_start_current(db, agent)
            if request.get("acknowledged"):
                return dict(agent, accepted=False)
            request["acknowledged"] = True
            agent["status"], request["delivery"] = "RUNNING", "DELIVERED"
            request.pop("uncertain_stage", None)
            self._save_agent(db, agent)
            self._audit(db, name, "ack", {"owner": owner})
        return dict(self.agent(name), accepted=True)

    def flow_dir(self, thread_id):
        path = self.root / "flows" / safe_key(thread_id)
        path.mkdir(parents=True, exist_ok=True)
        return path
