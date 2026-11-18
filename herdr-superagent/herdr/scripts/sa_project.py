"""Read a local project-stage commission and validate its worker's actual launch root."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from sa_state import StateError
from sa_workspace import validate_isolation


COORDINATORS = {"router", "leader"}
OBSERVERS = {"scout", "reviewer", "review-worker", "watchdog"}
EXECUTORS = {"dev-worker", "implementer", "simplifier", "tester", "test-worker"}
_FIELDS = {"project_root", "skill_entry", "project_ref", "mode", "stop_at", "workspaces", "evidence",
           "workflow_source"}
_GLOBAL = {"global-story": "req-closeloop", "global-bug": "bug-closeloop"}
_SKILL_DIRS = (".agents/skills", ".claude/skills", ".codex/skills",
               ".claude/commands", ".codex/commands")
_NAMED_FLOW = {
    "story": re.compile(r"^(?:openspec[-_:](?!.*(?:bug|fix))|(?:story|requirement)-workflow|req-closeloop$)"),
    "bug": re.compile(r"(?:bugfix|bug-fix|bug-closeloop|defect-fix)"),
}
_KIND_WORD = {"story": re.compile(r"需求|story|requirement", re.I),
              "bug": re.compile(r"缺陷|bug|defect", re.I)}
_FLOW_WORD = re.compile(r"工作流|闭环|流程|workflow|openspec", re.I)
_OVERVIEW_FLOW = re.compile(r"(?:本项目|本仓库|本工程|project|repository)\s*(?:的|采用|启用|设有|现行|使用|提供)?\s*"
                            r"(?:开发工作流|研发流程|development workflow)", re.I)
_CLAUSES = re.compile(r"[\n；;，,。！？!?]|\.\s+")
_NONLOCAL = re.compile(r"全局|\bglobal\b|~[/\\]|(?:没有|不设|未设|无|\bno\b).{0,20}"
                       r"(?:工作流|闭环|流程|workflow)", re.I)
_LOCAL_PATH = r"(?:docs|openspec|\.claude|\.codex|\.agents)[/\\]"
_LOCAL_TARGET = re.compile(r"(?:按|走|使用|遵循|采用)\s*(?:本项目|本仓库|本工程|项目)"
                           r"|(?:本项目|本仓库|本工程)\s*(?:提供|设有|采用|使用)"
                           rf"|{_LOCAL_PATH}"
                           r"|(?:流程|工作流|闭环|workflow).{0,12}(?:未获授权|不可用|失效|损坏|unavailable)", re.I)
_USE_ENTRY = re.compile(r"(?:按|走|使用|遵循|采用)\s*(?:本项目|本仓库|本工程|项目内)?\s*"
                        rf"[` ]*(?:\.[/\\])?{_LOCAL_PATH}", re.I)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"assignment {name} must be a nonempty string")
    return value.strip()


def _path(value, base, name):
    path = Path(_text(value, name)).expanduser()
    return (base / path).resolve()


def _file(value, name):
    path = Path(value)
    if not path.is_file():
        raise StateError(f"assignment {name} is unavailable: {path}")
    # is_file() alone does not catch an unreadable ACL. Only check open, not content.
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise StateError(f"assignment {name} cannot be read: {path}") from exc
    return str(path)


def _project_flow(directory, kind):
    """Recognize a local, relevant entry or a declaration; do not run project workflows."""
    if kind == "story" and (directory / "openspec").is_dir():
        return directory / "openspec"
    for sub in _SKILL_DIRS:
        entries = directory / sub
        if entries.is_dir():
            for candidate in entries.iterdir():
                if _NAMED_FLOW[kind].search(candidate.stem.lower()):
                    return candidate
    for name in ("AGENTS.md", "CLAUDE.md"):
        guide = directory / name
        if guide.is_file():
            # Prose is not a workflow registry. Only automate unmistakable local declarations;
            # the Router reads the complete guide to resolve ambiguous/custom project rules.
            for clause in _CLAUSES.split(guide.read_text(encoding="utf-8-sig")):
                if _NONLOCAL.search(clause):
                    continue
                kinds = {k for k, word in _KIND_WORD.items() if word.search(clause)}
                if (kind in kinds and (_USE_ENTRY.search(clause)
                                       or _FLOW_WORD.search(clause) and _LOCAL_TARGET.search(clause))
                        or not kinds and _OVERVIEW_FLOW.search(clause)):
                    return guide
    return None


def _registered_root(root):
    # The user's existing mapping identifies multi-repo workspaces that have no central Git repository.
    library = os.environ.get("WORK_SKILLS_LIBRARY")
    if not library:
        return False
    mapping = Path(library).expanduser() / "映射.json"
    if not mapping.is_file():
        return False
    projects = json.loads(mapping.read_text(encoding="utf-8-sig")).get("projects", {})
    if not isinstance(projects, dict):
        raise StateError(f"Invalid project mapping at {mapping}; confirm the project's rules root")
    return any((mapping.parent / Path(p).expanduser()).resolve() == root for paths in projects.values() if isinstance(paths, list)
               for p in paths if isinstance(p, str))


def _check_global(root, entry, source, ref, workspaces):
    if source not in _GLOBAL:
        raise StateError("workflow_source must be global-story/global-bug or omitted for a project workflow")
    canonical = (Path.home() / ".claude" / "skills" / _GLOBAL[source] / "SKILL.md").resolve()
    if entry.resolve() != canonical:
        raise StateError(f"{source} must use the installed global closure at {canonical}")
    kind = source.split("-", 1)[1]
    key = re.fullmatch(r"tapd:\d+:(story|bug):\d+", ref or "", re.I)
    ref_kind = key[1].lower() if key else None
    if ref.lower().startswith(("http://", "https://", "www.tapd.cn/", "tapd.cn/")):
        from sa_intake import _parse_link
        parsed, _ = _parse_link(ref)
        ref_kind = parsed[1] if parsed else None
    if ref_kind and ref_kind != kind:
        raise StateError(f"{ref_kind} project_ref cannot select {source}")
    if root == root.parent or root == Path.home().resolve():
        raise StateError("user home and drive roots are not project rules roots")
    if (root / ".git").is_file():
        raise StateError("use the actual project rules root, not a linked worktree or submodule")
    if not ((root / ".git").is_dir() or (root / "AGENTS.md").is_file() or (root / "CLAUDE.md").is_file()
            or _registered_root(root)):
        raise StateError("cannot identify the project rules root; confirm the mapped project, not scratch/cwd")
    # Check the enclosing project governance, not a user's global skill installation.
    # Some multi-repo mappings point one level above the actual agent/rules root.
    home = Path.home().resolve()
    candidates = [Path(p) for p in workspaces.values() if Path(p).is_dir()]
    if (root / "agent").is_dir():
        candidates.append(root / "agent")
    candidates.extend([root, *root.parents])
    for candidate in dict.fromkeys(candidates):
        if candidate == home or candidate == candidate.parent:
            continue
        marker = _project_flow(candidate, kind)
        if marker:
            raise StateError(f"project workflow is declared at {marker}; use/confirm its entry, not {source}")


def _check_entry(root, entry, source=None, ref="", workspaces=None):
    if not root.is_dir():
        raise StateError(f"assignment project_root is unavailable: {root}")
    if source:
        try:
            _check_global(root, entry, source, ref, workspaces or {})
        except (OSError, ValueError) as exc:
            raise StateError(f"cannot verify global closure against project rules: {exc}") from exc
    elif not entry.resolve().is_relative_to(root):
        raise StateError("assignment skill_entry must belong to the project rules root")
    _file(entry, "skill_entry")


def load_assignment(path):
    """project_root is relative to the manifest; its other relative paths are project-relative."""
    manifest = Path(path).expanduser().resolve()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise StateError(f"cannot read local assignment JSON {manifest}: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError("assignment must be a JSON object")
    unknown = data.keys() - _FIELDS
    if unknown:
        raise StateError(f"unknown assignment fields: {', '.join(sorted(unknown))}")
    root = _path(data.get("project_root"), manifest.parent, "project_root")
    entry = _path(data.get("skill_entry"), root, "skill_entry")
    ref = _text(data.get("project_ref"), "project_ref")
    source = _text(data["workflow_source"], "workflow_source") if "workflow_source" in data else None
    mode = _text(data.get("mode"), "mode")
    if mode not in {"read_only", "project_step"}:
        raise StateError("assignment mode must be read_only or project_step")
    paths = data.get("workspaces", {})
    if not isinstance(paths, dict):
        raise StateError("assignment workspaces must map project keys to worktree paths")
    workspaces = {}
    for key, value in paths.items():
        if not key.strip() or key != key.strip():
            raise StateError("workspace key must be nonempty and have no surrounding spaces")
        workspace = _path(value, root, f"workspaces.{key}")
        if mode == "project_step" and not workspace.is_dir():
            raise StateError(f"mapped workspace {key} is unavailable: {workspace}")
        workspaces[key] = str(workspace)
    evidence = data.get("evidence", [])
    if not isinstance(evidence, list):
        raise StateError("assignment evidence must be an array of existing file paths")
    _check_entry(root, entry, source, ref, workspaces)
    return {
        "project_root": str(root), "skill_entry": str(entry), "mode": mode,
        "project_ref": ref, **({"workflow_source": source} if source else {}),
        "stop_at": _text(data.get("stop_at"), "stop_at"), "workspaces": workspaces,
        "evidence": [_file(_path(e, root, "evidence"), "evidence") for e in evidence],
    }


def validate_project_start(assignment, role, cwd, *, task_id="", workspace_key=""):
    """A scheduling gate, not filesystem isolation: no extra directory permissions are granted."""
    root = Path(assignment["project_root"])
    _check_entry(root, Path(assignment["skill_entry"]), assignment.get("workflow_source"),
                 assignment["project_ref"], assignment["workspaces"])
    for evidence in assignment["evidence"]:
        _file(evidence, "evidence")
    if role not in COORDINATORS | OBSERVERS | EXECUTORS:
        raise StateError(f"role {role} has no project-stage scope")
    if assignment["mode"] == "read_only" and role in EXECUTORS:
        raise StateError(f"read_only commission cannot launch {role}; use existing evidence")
    workspaces = assignment["workspaces"]
    actual = Path(cwd).resolve()
    if workspace_key:
        mapped = workspaces.get(workspace_key)
        if not mapped or actual != Path(mapped):
            raise StateError("cwd must be the worktree root bound to this project's original task")
    if role in EXECUTORS:
        if not task_id or not workspace_key:
            raise StateError(f"{role} needs the original task_id and its registered workspace")
        # A non-repository fallback must never turn a stale mapping into a desktop/main-checkout writer.
        info = validate_isolation(actual)
        if actual != Path(info["root"]):
            raise StateError("project task cwd must be the isolated Git worktree root, not a subdirectory")
    elif actual != root and not any(actual == Path(p) for p in workspaces.values()):
        raise StateError("project observer/coordinator cwd must be the project root or a mapped workspace")
    if not actual.is_dir():
        raise StateError(f"project working directory is unavailable: {actual}")
    return {
        "cwd": str(actual), "workspace_key": workspace_key,
        "unavailable_workspaces": [key for key, p in workspaces.items() if not Path(p).is_dir()],
    }
