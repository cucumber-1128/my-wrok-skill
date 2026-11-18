"""Workflow model routing. Profiles describe selections, never commands or credentials."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sa_state import StateError


ROLES = {"router", "leader", "dev-worker", "review-worker", "test-worker",
         "watchdog", "scout", "implementer", "simplifier", "reviewer", "tester"}
BROWSER_ROLES = {"tester", "test-worker"}


def _pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise StateError(f"duplicate workflow profile key: {k}")
        result[k] = v
    return result


def _model(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,191}", value):
        raise StateError("profile model must be a model identifier, not an option or shell command")
    return value


def _routes(data, allowed):
    if not isinstance(data, dict):
        raise StateError("profile routes must be an object")
    unknown = data.keys() - allowed
    if unknown:
        raise StateError(f"unknown workflow route: {', '.join(sorted(unknown))}")
    for key, item in data.items():
        if not isinstance(item, dict) or set(item) != {"harness", "model"}:
            raise StateError(f"{key}: route requires exactly harness and model")
        if item["harness"] not in ("codex", "claude"):
            raise StateError(f"{key}: harness must be codex or claude")
        _model(item["model"])
    return data


def load_profile(name_or_path):
    if name_or_path == "delivery-swapped":
        # Derive from the one maintained map. Swap model IDs, not role permissions or harnesses.
        profile = load_profile("delivery")
        roles = profile["roles"]
        dev, review = roles["dev-worker"]["model"], roles["review-worker"]["model"]
        for role in ("dev-worker", "implementer", "simplifier"):
            roles[role]["model"] = review
        for role in ("review-worker", "reviewer"):
            roles[role]["model"] = dev
        profile["name"] = name_or_path
        return profile
    if name_or_path == "delivery":
        path = Path(__file__).resolve().parents[1] / "profiles" / "delivery.json"
    else:
        path = Path(name_or_path).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_pairs)
    except (ValueError, OSError) as exc:
        raise StateError(f"cannot read workflow profile {path}: {exc}") from exc
    if not isinstance(data, dict) or data.keys() - {"name", "roles", "purposes"}:
        raise StateError("workflow profile accepts only name, roles and purposes")
    if not isinstance(data.get("name"), str) or not re.fullmatch(r"[\w.-]{1,64}", data["name"]):
        raise StateError("workflow profile name must be an identifier")
    if not data.get("roles"):
        raise StateError("workflow profile needs at least one role")
    return {"name": data["name"], "roles": _routes(data["roles"], ROLES),
            "purposes": _routes(data.get("purposes", {}), {"browser"})}


def select_route(profile, role, purpose, role_argv, model, origin):
    """Return public selection metadata and interactive child argv without HC/workspace flags."""
    if purpose and (purpose != "browser" or role not in BROWSER_ROLES):
        raise StateError("purpose browser is only valid for tester or test-worker")
    if profile is None:
        if purpose:
            raise StateError("browser testing requires an explicit or inherited workflow profile")
        rid = role_argv[role_argv.index("--model") + 1] if "--model" in role_argv else None
        route = {"harness": "claude", "model": rid, "purpose": None, "profile": None,
                 "origin": "legacy", "model_source": "explicit" if model else "role" if rid else "harness_default"}
        return route, role_argv
    if role not in profile["roles"]:
        raise StateError(f"workflow profile {profile['name']} has no route for role {role}")
    choice = profile["roles"][role]
    if purpose:
        choice = profile["purposes"].get(purpose)
        if choice is None:
            raise StateError(f"workflow profile {profile['name']} has no route for purpose {purpose}")
    model_id = _model(model or choice["model"])
    harness = choice["harness"]
    if harness == "codex":
        if "--permission-mode" in role_argv or "--dangerously-skip-permissions" in role_argv:
            raise StateError("cannot translate a Claude role permission option to Codex; resolve the policy first")
        # HC owns the interactive process in this cwd. --add-dir in Codex grants write access.
        argv = ["codex", "--no-daemon", "--model", model_id]
    else:
        argv = list(role_argv)
        if "--model" in argv:
            argv[argv.index("--model") + 1] = model_id
        else:
            argv.extend(["--model", model_id])
    return ({"harness": harness, "model": model_id, "purpose": purpose or None,
             "profile": profile["name"], "origin": origin,
             "model_source": "explicit" if model else "purpose" if purpose else "role"}, argv)
