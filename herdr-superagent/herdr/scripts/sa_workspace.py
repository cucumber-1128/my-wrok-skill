"""Git worktree preparation; never changes or cleans the main checkout."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sa_process import TransportError, run_process
from sa_state import StateError, normalized_path, safe_key


def git(cwd, *args, optional=False):
    try:
        code, stdout, stderr = run_process(["git", "-C", str(cwd), *args], timeout=30)
    except TransportError as exc:
        raise StateError(f"git unavailable: {exc}") from exc
    if code and not optional:
        raise StateError(f"git failed: {stderr.strip()[:1200]}")
    return None if code else stdout.strip()


def repository_info(cwd):
    root = git(cwd, "rev-parse", "--show-toplevel", optional=True)
    if root is None:
        return None
    git_dir = git(cwd, "rev-parse", "--absolute-git-dir")
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    parent = git(cwd, "rev-parse", "--show-superproject-working-tree")
    return {"root": normalized_path(root), "git_dir": normalized_path(git_dir),
            "common_dir": normalized_path(common), "submodule": bool(parent)}


def validate_isolation(cwd):
    info = repository_info(cwd)
    if not info or info["git_dir"] == info["common_dir"] or info["submodule"]:
        raise StateError("an isolated Git worktree is required; preserve the main checkout")
    return dict(info, cwd=normalized_path(cwd), isolated=True)


def snapshot_revision(cwd):
    if git(cwd, "status", "--porcelain=v1", "--untracked-files=all"):
        raise StateError("dirty working directory: commit the scoped change before recording a version")
    return git(cwd, "rev-parse", "--verify", "HEAD^{commit}")


def _redact_remote(remote):
    if not remote:
        return ""
    parsed = urlsplit(remote)
    if parsed.scheme and parsed.netloc and "@" in parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[1],
                           parsed.path, parsed.query, parsed.fragment))
    return remote


def prepare_workspace(repo, branch, thread_id, state_root, *, destination=None):
    repo = Path(repo).resolve()
    info = repository_info(repo)
    if not info:
        raise StateError("workspace source is not a Git repository")
    if not branch or git(repo, "check-ref-format", "--branch", branch, optional=True) is None:
        raise StateError("invalid approved branch")
    ref = f"refs/heads/{branch}"
    if git(repo, "show-ref", "--verify", ref, optional=True) is None:
        raise StateError("approved existing local branch required; follow the qe workflow first")
    if git(repo, "symbolic-ref", "-q", ref, optional=True) is not None:
        raise StateError("an approved branch must be a direct local ref; symbolic branches are not allowed")
    target = Path(destination).resolve() if destination else (
        Path(state_root).resolve() / "worktrees" / safe_key(thread_id)
    )
    if target.exists():
        isolated = validate_isolation(target)
        if isolated["common_dir"] != info["common_dir"]:
            raise StateError("existing destination belongs to a different repository")
        if git(target, "branch", "--show-current") != branch:
            raise StateError("existing destination uses a different branch")
        head = snapshot_revision(target)
    else:
        if target == Path(info["root"]) or Path(info["root"]) in target.parents:
            relative = os.path.relpath(target, info["root"])
            if git(repo, "check-ignore", relative, optional=True) is None:
                raise StateError("a project-local worktree destination must already be ignored")
        target.parent.mkdir(parents=True, exist_ok=True)
        # worktree add <path> <name> can create a branch from a matching remote
        # if the local branch disappears. Attach by name only via switch --no-guess.
        start = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
        git(repo, "worktree", "add", "--detach", "--", str(target), start)
        try:
            git(target, "switch", "--no-guess", branch)
        except StateError as exc:
            # Remove only our fresh detached checkout. Preserve changes, including
            # ignored files, left by a hook or another process; never use --force.
            if (git(target, "branch", "--show-current", optional=True) == ""
                    and git(target, "rev-parse", "--verify", "HEAD", optional=True) == start
                    and git(target, "status", "--porcelain=v1", "--ignored", "--untracked-files=all",
                            optional=True) == ""
                    and git(repo, "worktree", "remove", "--", str(target), optional=True) is not None):
                raise
            raise StateError(f"{exc}; failed worktree preserved at {target}") from exc
        validate_isolation(target)
        if git(target, "branch", "--show-current") != branch:
            raise StateError(f"unexpected branch after attaching approved worktree; inspect {target}")
        head = snapshot_revision(target)
    remote = _redact_remote(git(target, "remote", "get-url", "origin", optional=True))
    return {"cwd": str(target), "branch": branch, "head": head, "base": head,
            "remote": remote, "isolated": True, "thread_id": thread_id}
