"""JSON command line for the local herdr control plane (Python standard library)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from sa_process import TransportError
from sa_runtime import Runtime
from sa_state import LIFECYCLES, StateError, Store
from sa_watchdog import Driver
from sa_workspace import prepare_workspace, repository_info, snapshot_revision, validate_isolation


def output(value, *, error=False):
    stream = sys.stderr if error else sys.stdout
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), file=stream, flush=True)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--state-dir", help="persistent state; default HERDR_STATE_DIR/LOCALAPPDATA")
    commands = root.add_subparsers(dest="command", required=True)

    thread = commands.add_parser("thread", help="thread lifecycle and requirement revision")
    actions = thread.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    create.add_argument("--id", required=True)
    create.add_argument("--cwd", required=True)
    create.add_argument("--assignment-file", help="local project workflow and current authorized endpoint")
    create.add_argument("--parent", default="")
    create.add_argument("--owner", default="")
    create.add_argument("--brief", default="")
    create.add_argument("--max-runtime-sec", type=int, default=14400)
    for name in ("get", "status", "revise", "reopen", "owner", "handoff"):
        action = actions.add_parser(name)
        action.add_argument("--id", required=True)
        if name in {"status", "revise", "owner", "handoff"}:
            action.add_argument("--generation", type=int, required=True)
        if name == "reopen":
            action.add_argument("--assignment-file", help="new explicit authorization for a project assignment")
        if name == "handoff":
            action.add_argument("--spec-rev", type=int, required=True)
            action.add_argument("--summary", required=True)
            action.add_argument("--evidence", action="append", required=True)
        if name == "status":
            action.add_argument("--status", required=True, choices=sorted(LIFECYCLES - {"HANDOFF"}))
            action.add_argument("--summary", default="")
        if name == "owner":
            action.add_argument("--owner", required=True)
    actions.add_parser("list")

    task = commands.add_parser("task", help="versioned development/review/build/test gates")
    actions = task.add_subparsers(dest="action", required=True)
    for name in ("create", "get", "retry", "report"):
        action = actions.add_parser(name)
        action.add_argument("--thread", required=True)
        action.add_argument("--id", required=True)
        if name in {"create", "retry", "report"}:
            action.add_argument("--revision", required=True)
        if name == "create":
            action.add_argument("--workspace", default="", help="workspace key in this project assignment")
        if name == "report":
            action.add_argument("--attempt", type=int, required=True)
            action.add_argument("--spec-rev", type=int, required=True)
            action.add_argument("--kind", required=True,
                                choices=["DONE", "APPROVED", "REJECTED", "DEPLOYED", "PASS", "FAIL", "BLOCKED"])
            action.add_argument("--cwd", help="actual source worktree; default thread cwd")
            action.add_argument("--snapshot-file", help="immutable snapshot for patch:SHA256 revisions")
            action.add_argument("--base-revision", default="", help="assigned base for first DONE of new code")
            action.add_argument("--evidence", action="append", default=[])
            action.add_argument("--artifact-id", default="")
            action.add_argument("--environment-revision", default="")
            action.add_argument("--deployment-file")
            action.add_argument("--summary", default="")
            action.add_argument("--decision-file")
    listed = actions.add_parser("list")
    listed.add_argument("--thread", required=True)

    event = commands.add_parser("event", help="outbox claims and explicit reconciliation")
    actions = event.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    enqueue = actions.add_parser("enqueue")
    enqueue.add_argument("--source", required=True)
    enqueue.add_argument("--thread", required=True)
    enqueue.add_argument("--payload-file", required=True)
    for name in ("get", "claim", "done", "retry", "recover"):
        action = actions.add_parser(name)
        action.add_argument("--id", required=True)
        if name in {"claim", "done", "recover"}:
            action.add_argument("--owner", required=True)
        if name == "retry":
            action.add_argument("--confirmed-not-delivered", action="store_true", required=True)
        if name == "recover":
            action.add_argument("--confirmed-not-executed", action="store_true", required=True)
            action.add_argument("--decision-file", required=True)

    agent = commands.add_parser("agent", help="registered sessions only")
    actions = agent.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    for name in ("get", "ack", "retry-start", "adopt-start", "abandon-start", "stop"):
        action = actions.add_parser(name)
        action.add_argument("--name", required=True)
        if name == "ack":
            action.add_argument("--owner", required=True)
        if name == "retry-start":
            action.add_argument("--confirmed-not-delivered", action="store_true", required=True)
        if name == "adopt-start":
            action.add_argument("--address", required=True)
        if name == "abandon-start":
            action.add_argument("--confirmed-not-created", action="store_true", required=True)

    limits = commands.add_parser("limits", help="transactional global and role capacity")
    actions = limits.add_subparsers(dest="action", required=True)
    actions.add_parser("get")
    limits_set = actions.add_parser("set")
    limits_set.add_argument("--max-agents", type=int)
    limits_set.add_argument("--max-flows", type=int)
    limits_set.add_argument("--roles", type=json.loads)

    workspace = commands.add_parser("workspace", help="approved branch in an isolated worktree")
    actions = workspace.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--repo", required=True)
    prepare.add_argument("--branch", required=True)
    prepare.add_argument("--thread", required=True)
    prepare.add_argument("--destination")
    snapshot = commands.add_parser("snapshot", help="clean HEAD; fails on uncommitted changes")
    snapshot.add_argument("--cwd", required=True)

    profile = commands.add_parser("profile", help="inspect the local model and harness map without launching")
    show = profile.add_subparsers(dest="action", required=True).add_parser("show")
    show.add_argument("--workflow-profile", required=True, metavar="NAME_OR_PATH",
                      help="built-in 'delivery', 'delivery-swapped', or local profile JSON")

    spawn = commands.add_parser("spawn", help="checked role startup or durable pending resume")
    spawn.add_argument("--role")
    spawn.add_argument("--name", required=True)
    spawn.add_argument("--cwd", default=str(Path.cwd()))
    spawn.add_argument("--task", default="")
    spawn.add_argument("--model", default="")
    spawn.add_argument("--workflow-profile", metavar="NAME_OR_PATH",
                       help="opt into a workflow model/harness map; descendants inherit the selection")
    spawn.add_argument("--purpose", choices=["browser"],
                       help="select the browser route for a tester or test-worker")
    spawn.add_argument("--role-dir")
    spawn.add_argument("--ready-timeout", type=float, default=90)
    spawn.add_argument("--no-wait", action="store_true")
    spawn.add_argument("--resume", action="store_true")
    spawn.add_argument("--thread", default="")
    spawn.add_argument("--task-id", default="")
    spawn.add_argument("--parent", default="")
    spawn.add_argument("--replace-owner", action="store_true")

    trigger = commands.add_parser("trigger", help="poll sources and dispatch the durable outbox")
    trigger.add_argument("--config", required=True)
    trigger.add_argument("--once", action="store_true")
    trigger.add_argument("--interval-sec", type=float, default=5)
    watchdog = commands.add_parser("watchdog", help="supervise registered/explicit terminals only")
    watchdog.add_argument("--leader", default="")
    watchdog.add_argument("--watchdog", default="")
    watchdog.add_argument("--router", default="router")
    watchdog.add_argument("--state-file")
    watchdog.add_argument("--interval-sec", type=float, default=30)
    watchdog.add_argument("--watchdog-interval-sec", type=float, default=0)
    watchdog.add_argument("--grace-sec", type=float, default=60)
    watchdog.add_argument("--once", action="store_true")
    return root


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def decision_file(path):
    path = Path(path)
    if not path.is_file() or not path.read_text(encoding="utf-8-sig").strip():
        raise StateError("decision file must contain the actual adjudication/recovery decision")
    return str(path.resolve())


def require_generation(store, thread_id, generation):
    if store.get_thread(thread_id)["generation"] != generation:
        raise StateError("stale generation")


def task_report(store, args):
    thread = store.get_thread(args.thread)
    cwd = args.cwd or thread["cwd"]
    if thread["assignment"]:
        if thread["assignment"]["mode"] == "read_only":
            raise StateError("read_only project assignment cannot advance tasks")
        task = store.get_task(args.thread, args.id)
        bound = thread["assignment"]["workspaces"].get(task["workspace"])
        if not bound or args.revision.startswith("patch:"):
            raise StateError("a project code report needs its assigned worktree and Git revision")
        cwd = args.cwd or bound
        if not Path(cwd).resolve().is_relative_to(Path(bound).resolve()):
            raise StateError("report cwd is outside its assigned worktree")
        info = validate_isolation(cwd)
        if Path(info["root"]) != Path(bound).resolve():
            raise StateError("report cwd must belong to the assigned worktree")
    if args.kind == "BLOCKED":
        return store.report(args.thread, args.id, args.attempt, args.spec_rev, args.revision,
                            "BLOCKED", summary=args.summary)
    if args.revision.startswith("patch:"):
        if not args.snapshot_file or not Path(args.snapshot_file).is_file():
            raise StateError("snapshot file required for patch revisions")
        actual = "patch:" + hashlib.sha256(Path(args.snapshot_file).read_bytes()).hexdigest()
        if actual != args.revision:
            raise StateError("snapshot version mismatch")
    else:
        if not thread["assignment"]:
            info, original = repository_info(cwd), repository_info(thread["cwd"])
            if not info or not original or info["common_dir"] != original["common_dir"]:
                raise StateError("report cwd must belong to the thread repository")
        if snapshot_revision(cwd) != args.revision:
            raise StateError("code version does not match clean worktree HEAD")
    for evidence in args.evidence:
        if not Path(evidence).is_file():
            raise StateError(f"evidence file missing: {evidence}")
    artifact_id, environment_revision = args.artifact_id, args.environment_revision
    if args.kind == "DEPLOYED":
        if not args.deployment_file:
            raise StateError("deployment-file required: verify the running build version first")
        deployment = read_json(args.deployment_file)
        if (not isinstance(deployment, dict) or deployment.get("code_revision") != args.revision or
                deployment.get("environment_revision") != args.revision or
                not deployment.get("environment") or not deployment.get("verified_at") or
                not deployment.get("artifact_id")):
            raise StateError("deployment manifest version/environment/artifact evidence invalid")
        if artifact_id and artifact_id != deployment["artifact_id"]:
            raise StateError("deployment artifact mismatch")
        artifact_id, environment_revision = deployment["artifact_id"], deployment["environment_revision"]
    decision_ref = ""
    if args.decision_file:
        decision_ref = decision_file(args.decision_file)
    return store.report(
        args.thread, args.id, args.attempt, args.spec_rev, args.revision, args.kind,
        evidence=[str(Path(p).resolve()) for p in args.evidence], artifact_id=artifact_id,
        environment_revision=environment_revision, summary=args.summary, decision_ref=decision_ref,
        base_revision=args.base_revision,
    )


def run(args):
    if args.command == "snapshot":
        return {"code_revision": snapshot_revision(args.cwd), "cwd": str(Path(args.cwd).resolve())}
    if args.command == "profile":
        from sa_engines import load_profile
        return load_profile(args.workflow_profile)
    if args.command == "spawn" and args.resume and (
            args.workflow_profile is not None or args.purpose is not None or args.model):
        raise StateError("--resume uses the stored route; remove --workflow-profile, --purpose and --model")
    store = Store(args.state_dir)
    if args.command == "thread":
        assignment = None
        if getattr(args, "assignment_file", None):
            from sa_project import load_assignment
            assignment = load_assignment(args.assignment_file)
        if args.action == "create":
            return store.create_thread(args.id, args.cwd, parent=args.parent, owner=args.owner,
                                       brief=args.brief, max_runtime_sec=args.max_runtime_sec, assignment=assignment)
        if args.action == "get":
            return store.get_thread(args.id)
        if args.action == "list":
            return store.list_threads()
        if args.action == "reopen":
            return store.reopen_thread(args.id, assignment=assignment)
        if args.action == "handoff":
            return store.handoff_thread(args.id, generation=args.generation, spec_rev=args.spec_rev,
                                        summary=args.summary, evidence=args.evidence)
        require_generation(store, args.id, args.generation)
        if args.action == "status":
            return store.set_thread_status(args.id, args.status, generation=args.generation, summary=args.summary)
        if args.action == "revise":
            return store.revise_thread(args.id, generation=args.generation)
        return store.assign_owner(args.id, args.owner, generation=args.generation)
    if args.command == "task":
        if args.action in {"create", "retry"}:
            thread = store.get_thread(args.thread)
            version = None
            if thread["assignment"]:
                if thread["assignment"]["mode"] == "read_only":
                    raise StateError("read_only project assignment cannot advance tasks")
                key = args.workspace if args.action == "create" else store.get_task(args.thread, args.id)["workspace"]
                _, bound = store.project_workspace(thread, key)
                info = validate_isolation(bound)
                if Path(info["root"]) != Path(bound) or snapshot_revision(bound) != args.revision:
                    raise StateError("assigned worktree must match the clean task baseline revision")
                version = (thread["generation"], thread["spec_rev"])
            if args.action == "create":
                return store.create_task(args.thread, args.id, args.revision, workspace=args.workspace,
                                         expected_version=version)
            return store.retry_task(args.thread, args.id, args.revision, expected_version=version)
        if args.action == "get":
            return store.get_task(args.thread, args.id)
        if args.action == "list":
            return store.list_tasks(args.thread)
        return task_report(store, args)
    if args.command == "event":
        if args.action == "list":
            return store.list_events()
        if args.action == "enqueue":
            return store.enqueue_event(args.source, args.thread, read_json(args.payload_file))
        if args.action == "get":
            return store.get_event(args.id)
        if args.action == "claim":
            return store.claim_event(args.id, args.owner)
        if args.action == "done":
            return store.complete_event(args.id, args.owner)
        if args.action == "recover":
            return store.recover_event(args.id, args.owner, decision_file(args.decision_file))
        return store.retry_event(args.id)
    if args.command == "agent":
        if args.action == "list":
            return store.list_agents()
        if args.action == "get":
            return store.agent(args.name)
        if args.action == "ack":
            return store.ack_agent(args.name, args.owner)
        if args.action == "stop":
            return Runtime(store).stop_agent(args.name)
        if args.action == "adopt-start":
            return Runtime(store).adopt_start(args.name, args.address)
        if args.action == "abandon-start":
            return Runtime(store).abandon_start(args.name)
        return store.retry_start(args.name)
    if args.command == "limits":
        if args.action == "get":
            return store.limits()
        return store.set_limits(**{name: getattr(args, name) for name in ("max_agents", "max_flows", "roles")
                                  if getattr(args, name) is not None})
    if args.command == "workspace":
        return prepare_workspace(args.repo, args.branch, args.thread, store.root, destination=args.destination)
    runtime = Runtime(store)
    if args.command == "spawn":
        if args.resume:
            if args.no_wait:
                raise StateError("--resume and --no-wait cannot be combined")
            return runtime.resume_start(args.name, ready_timeout=args.ready_timeout)
        if not args.role:
            raise StateError("--role required for new startup")
        return runtime.spawn(args.role, args.name, args.cwd, task=args.task, model=args.model,
                             role_dir=args.role_dir, ready_timeout=args.ready_timeout, no_wait=args.no_wait,
                             thread_id=args.thread, task_id=args.task_id, parent=args.parent,
                             replace_owner=args.replace_owner, workflow_profile=args.workflow_profile,
                             purpose=args.purpose)
    if args.command == "trigger":
        if args.interval_sec <= 0:
            raise StateError("trigger interval must be positive")
        config = read_json(args.config)
        if not isinstance(config, dict) or not isinstance(config.get("sources"), list):
            raise StateError("trigger config requires a sources array")
        for index, source in enumerate(config["sources"]):
            if not isinstance(source, dict):
                raise StateError(f"trigger sources[{index}] must be an object")
        names = [source.get("name") for source in config["sources"]]
        if len(set(names)) != len(names) or not all(isinstance(n, str) and n for n in names):
            raise StateError("trigger source names must be unique nonempty strings")
        due = {}
        while True:
            now = time.time()
            sources = [s for s in config["sources"] if now >= due.get(s["name"], 0)]
            for source in sources:
                interval = float(source.get("interval_sec", 120))
                if interval <= 0:
                    raise StateError("source interval must be positive")
                due[source["name"]] = now + interval
            result = runtime.poll_sources(dict(config, sources=sources))
            result["starts"] = runtime.reconcile_starts()
            result["outbox"] = runtime.dispatch(config.get("router", "router"))
            if args.once:
                return result
            output(result)
            time.sleep(args.interval_sec)
    if args.interval_sec <= 0 or args.grace_sec < 0 or args.watchdog_interval_sec < 0:
        raise StateError("watchdog intervals invalid (tick 0 disables periodic model calls)")
    driver = Driver(runtime, leader=args.leader, watchdog=args.watchdog, router=args.router,
                    state_file=args.state_file, grace_sec=args.grace_sec,
                    watchdog_interval_sec=args.watchdog_interval_sec)
    while True:
        result = driver.step()
        if args.once or result["should_exit"]:
            return result
        output(result)
        time.sleep(args.interval_sec)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        output(run(args))
        return 0
    except (StateError, TransportError, OSError, ValueError, TypeError) as exc:
        output({"ok": False, "error": {"kind": getattr(exc, "kind", "invalid_state"), "message": str(exc),
                                     "uncertain": getattr(exc, "uncertain", False)}}, error=True)
        return 1
    except KeyboardInterrupt:
        output({"ok": False, "error": {"kind": "interrupted",
                                     "message": "stopped; durable work retained for reconciliation"}}, error=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
