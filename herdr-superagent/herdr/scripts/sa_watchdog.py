"""Deterministic control loop. Model inspection is an explicit escalation."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sa_process import TransportError, terminal_state
from sa_state import LIFECYCLES, TERMINAL, StateError, canonical


class Driver:
    def __init__(self, runtime, *, leader="", watchdog="", router="router", state_file=None,
                 grace_sec=60, watchdog_interval_sec=0, failure_threshold=3, stall_sec=900):
        self.runtime, self.store = runtime, runtime.store
        self.leader, self.watchdog, self.router = leader, watchdog, router
        self.state_file = Path(state_file) if state_file else None
        self.grace_sec = grace_sec
        self.watchdog_interval_sec = watchdog_interval_sec
        self.failure_threshold, self.stall_sec = failure_threshold, stall_sec
        self.targets = {name: {"failures": 0} for name in (leader, watchdog) if name}
        self.next_tick = 0
        self.sent_alerts = set()
        self.interrupted = set()

    def _legacy_status(self):
        if not self.state_file:
            return None
        if not self.state_file.is_file():
            raise StateError("state file missing; checkpoint suppressed")
        text = self.state_file.read_text(encoding="utf-8-sig")
        if self.state_file.suffix.lower() == ".json":
            data = json.loads(text)
            status = data.get("status")
        else:
            matches = re.findall(r"(?m)^\s*status:\s*([A-Z_]+)\s*$", text)
            if len(matches) != 1:
                raise StateError("one exact status field required; checkpoint suppressed")
            status = matches[0]
        if status not in LIFECYCLES:
            raise StateError("unknown lifecycle; checkpoint suppressed")
        return status

    def _alert(self, key, message, thread=None):
        if key in self.sent_alerts:
            return
        self.sent_alerts.add(key)
        recipient = (thread or {}).get("parent") or self.router
        self.store.notify(
            "ALERT", {"problem": key, "summary": message,
                      "thread_id": (thread or {}).get("id", "")},
            recipient=recipient, thread_id=(thread or {}).get("id", "system:watchdog"),
            event_key=f"{key}:{int(self.runtime.clock() // 3600)}",
        )

    def step(self):
        now = self.runtime.clock()
        starts = self.runtime.reconcile_starts()
        alerts, observations = [], {}
        threads = {thread["id"]: thread for thread in self.store.list_threads()}
        agents = self.store.list_agents(active=True)
        by_address = {}
        for agent in agents:
            by_address[agent["name"]] = agent
            if agent["address"]:
                by_address[agent["address"]] = agent
                if agent["name"] != agent["address"] and agent["name"] in self.targets:
                    self.targets.setdefault(agent["address"], self.targets.pop(agent["name"]))
            self.targets.setdefault(agent["address"] or agent["name"], {"failures": 0})
            if agent["status"] == "UNCERTAIN" or agent["request"].get("delivery") == "UNCERTAIN":
                self._alert(f"startup:{agent['name']}",
                            f"startup {agent['name']} is uncertain; inspect before adopt/retry/abandon",
                            threads.get(agent["thread_id"]))
            elif (agent["request"].get("delivery") == "DELIVERED" and agent["status"] == "READY" and
                  now - agent["updated"] >= self.stall_sec):
                self._alert(f"startup-ack:{agent['name']}",
                            f"startup {agent['name']} is delivered but has no ACK; inspect before retry",
                            threads.get(agent["thread_id"]))
        leader_target = (by_address.get(self.leader) or {}).get("address") or self.leader
        watchdog_target = (by_address.get(self.watchdog) or {}).get("address") or self.watchdog
        for thread in threads.values():
            if thread["status"] == "RUNNING" and now - thread["created"] >= thread["max_runtime_sec"]:
                updated = self.store.set_thread_status(thread["id"], "PAUSED", summary="runtime budget reached")
                threads[thread["id"]] = updated
                alerts.append({"thread_id": thread["id"], "reason": "runtime budget reached"})
        legacy_status = None
        if self.state_file:
            try:
                legacy_status = self._legacy_status()
            except (OSError, ValueError, StateError) as exc:
                alerts.append({"target": self.leader, "reason": str(exc)})
                legacy_status = "UNKNOWN"
        leader_terminal = False
        suspicious = False
        for target, history in list(self.targets.items()):
            if history.get("finished"):
                continue
            agent = by_address.get(target)
            thread = threads.get(agent["thread_id"]) if agent and agent["thread_id"] else None
            lifecycle = thread["status"] if thread else (
                legacy_status if target == leader_target else None
            )
            if target == leader_target and lifecycle in TERMINAL:
                leader_terminal = True
            if agent and not agent["address"]:
                observations[target] = {"unverified": True, "startup": agent["request"].get("delivery")}
                continue
            try:
                info = self.runtime.hc.call(["get", target])
                state = terminal_state(info)
                history["failures"] = 0
            except TransportError as exc:
                history["failures"] += 1
                observations[target] = {"reachable": False, "failures": history["failures"],
                                        "error": str(exc)}
                if history["failures"] >= self.failure_threshold:
                    suspicious = True
                    self._alert(f"observe:{target}", f"cannot observe {target}: {exc}", thread)
                continue
            if state == "finished":
                observations[target] = {"state": "finished"}
                if agent:
                    self.store.release_agent(agent["name"])
                history["finished"] = True
                if thread and thread["status"] == "RUNNING" and agent["role"] == "leader":
                    self.store.set_thread_status(thread["id"], "BLOCKED", summary="leader exited")
                    suspicious = True
                continue
            if agent and lifecycle in TERMINAL:
                try:
                    self.runtime.stop_agent(agent["name"])
                    history["finished"] = True
                    observations[target] = {"state": "reclaimed", "lifecycle": lifecycle}
                except (TransportError, StateError) as exc:
                    alerts.append({"target": target, "reason": f"cleanup pending: {exc}"})
                continue
            status = info.get("status") or {}
            status = status.get("state") if isinstance(status, dict) else status
            observations[target] = {"state": "live", "status": status, "lifecycle": lifecycle}
            obsolete = self.store.agent_context_error(agent["name"]) if agent else ""
            if obsolete:
                observations[target]["obsolete"] = obsolete
                if status == "working" and (target, "OBSOLETE") not in self.interrupted:
                    try:
                        self.runtime.hc.call(["interrupt", target])
                        self.interrupted.add((target, "OBSOLETE"))
                    except TransportError as exc:
                        alerts.append({"target": target, "reason": str(exc)})
                self._alert(f"obsolete:{target}", f"{target}: {obsolete}; reconcile this session", thread)
                continue
            if (agent and lifecycle in {"PAUSED", "CANCELED"} and status == "working" and
                    (target, lifecycle, thread["state_version"]) not in self.interrupted):
                try:
                    self.runtime.hc.call(["interrupt", target])
                    self.interrupted.add((target, lifecycle, thread["state_version"]))
                except TransportError as exc:
                    alerts.append({"target": target, "reason": str(exc)})
            if status == "blocked":
                history.pop("idle_since", None)
                suspicious = True
                if lifecycle not in {"WAITING_HUMAN", "PAUSED", "BLOCKED"}:
                    self._alert(f"blocked:{target}", f"{target} requires human permission/decision", thread)
                continue
            if agent and (agent["request"].get("delivery") != "DELIVERED" or agent["status"] != "RUNNING"):
                history.pop("idle_since", None)
                continue
            is_leader = target == leader_target or (agent and agent["role"] == "leader")
            if is_leader and lifecycle == "RUNNING" and status in {"idle", "done"}:
                history.setdefault("idle_since", now)
                last_checkpoint = history.get("last_checkpoint", float("-inf"))
                if (now - history["idle_since"] >= self.grace_sec and
                        now - last_checkpoint >= max(30, self.grace_sec)):
                    try:
                        self.runtime.hc.call(["send", target, (
                            "SA_CHECKPOINT " + canonical({
                                "thread_id": thread["id"] if thread else "",
                                "state_dir": str(self.store.root),
                                "state_file": str(self.state_file) if self.state_file else "",
                            }) + ". Read canonical state and pending results, then continue only RUNNING work."
                        )])
                        history["last_checkpoint"] = now
                    except TransportError as exc:
                        alerts.append({"target": target, "reason": str(exc)})
            else:
                history.pop("idle_since", None)
            progress = (info.get("turn"), info.get("status_ms"), thread["updated"] if thread else None)
            if history.get("progress") != progress:
                history["progress"], history["progress_at"] = progress, now
            elif status == "working" and now - history.get("progress_at", now) >= self.stall_sec:
                suspicious = True
                self._alert(f"stalled:{target}", f"{target} has no observed progress; inspect before interrupting",
                            thread)
        active_work = any(t["status"] == "RUNNING" for t in threads.values())
        tick_due = self.watchdog_interval_sec > 0 and now >= self.next_tick
        if self.watchdog and active_work and (suspicious or tick_due):
            observation = observations.get(watchdog_target, {})
            if observation.get("status") in {"idle", "done"}:
                try:
                    self.runtime.hc.call(["send", watchdog_target,
                                         f"WATCHDOG-TICK state_dir={self.store.root}; inspect current anomalies."])
                    self.next_tick = now + max(300, self.watchdog_interval_sec)
                except TransportError as exc:
                    alerts.append({"target": self.watchdog, "reason": str(exc)})
        for event in self.store.list_events(statuses=["UNCERTAIN", "DEAD", "AWAITING_ACK", "CLAIMED"]):
            if event["kind"] == "ALERT":
                continue
            if event["status"] in {"UNCERTAIN", "DEAD"} or now - event["updated"] >= self.stall_sec:
                self._alert(f"event:{event['id']}:{event['status']}",
                            f"event {event['id']} is {event['status']}; reconcile before replay",
                            threads.get(event["thread_id"]))
        delivery = self.runtime.dispatch(self.router)
        return {
            "starts": starts, "targets": observations, "alerts": alerts, "outbox": delivery,
            "should_exit": bool(self.leader and not self.watchdog and leader_terminal and not alerts),
        }
