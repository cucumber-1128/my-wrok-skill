"""A project may explicitly use the installed global closure only when appropriate."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sa_project import load_assignment, validate_project_start
from sa_state import StateError


class WorkflowSourceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = Path(t.name).resolve()
        cm = patch.object(Path, "home", return_value=self.home)
        cm.start()
        self.addCleanup(cm.stop)
        self.project = self.home / "work" / "project"
        (self.project / ".git").mkdir(parents=True)
        self.entries = {}
        for kind, skill in (("bug", "bug-closeloop"), ("story", "req-closeloop")):
            entry = self.home / ".claude" / "skills" / skill / "SKILL.md"
            entry.parent.mkdir(parents=True)
            entry.write_text("Installed workflow", encoding="utf-8")
            self.entries[kind] = entry
        self.manifest_path = self.home / "input.json"

    def load(self, kind="story", **changes):
        raw = dict(workflow_source="global-" + kind, project_root=str(self.project),
                   skill_entry=str(self.entries[kind]), project_ref=f"tapd:00000001:{kind}:1100000000000000002",
                   stop_at="return without submitting", mode="read_only")
        raw.update(changes)
        self.manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        return load_assignment(self.manifest_path)

    def test_global_req_and_bug_keep_business_root_and_original_authorities(self):
        for kind in self.entries:
            with self.subTest(kind=kind):
                result = self.load(kind)
                self.assertEqual(str(self.project), result["project_root"])
                self.assertEqual(str(self.entries[kind]), result["skill_entry"])
                self.assertEqual("global-" + kind, result["workflow_source"])
                validate_project_start(result, "scout", self.project)
                with self.assertRaises(StateError):
                    validate_project_start(result, "implementer", self.project)

    def test_spoof_wrong_kind_and_inaccessible_source_are_refused(self):
        fake = self.project / ".claude" / "skills" / "req-closeloop" / "SKILL.md"
        fake.parent.mkdir(parents=True)
        fake.write_text("not the user's canonical global skill", encoding="utf-8")
        for changes in ({"skill_entry": str(fake)}, {"skill_entry": str(self.entries["bug"])},
                        {"workflow_source": "guess"}, {"project_ref": "tapd:00000001:bug:1100000000000000002"},
                        {"project_ref": "https://www.tapd.cn/tapd_fe/00000001/bug/detail/1100000000000000002"}):
            with self.subTest(changes=changes), self.assertRaises(StateError):
                self.load(**changes)
        self.entries["story"].unlink()
        with self.assertRaises(StateError):
            self.load()

    def test_applicable_project_entry_blocks_global_including_on_later_spawn(self):
        before = self.load("bug")
        bug = self.project / ".agents" / "skills" / "project-bug-fix" / "SKILL.md"
        bug.parent.mkdir(parents=True)
        bug.write_text("project bug gate", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "project workflow"):
            self.load("bug")
        with self.assertRaisesRegex(StateError, "project workflow"):
            validate_project_start(before, "leader", self.project)
        self.load("story")

    def test_story_only_pipeline_is_not_automatically_a_bug_pipeline(self):
        (self.project / "openspec").mkdir()
        with self.assertRaisesRegex(StateError, "project workflow"):
            self.load("story")
        self.load("bug")
        (self.project / "AGENTS.md").write_text("缺陷一律按项目 OpenSpec 修复流程执行，见 docs/repair.md。", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "project workflow"):
            self.load("bug")

    def test_no_local_workflow_and_explicit_delegation_allow_the_global_original(self):
        guide = self.project / "AGENTS.md"
        for instruction in ("本项目没有单独的缺陷工作流，缺陷走全局 bug-closeloop。",
                            "本项目缺陷处理流程遵循全局 bug-closeloop。",
                            "本项目没有独立的开发工作流。",
                            "请检查本项目是否有开发工作流。",
                            "缺陷修复必须使用 QE 办理迭代。"):
            with self.subTest(instruction=instruction):
                guide.write_text(instruction, encoding="utf-8")
                self.assertEqual(str(self.entries["bug"]), self.load("bug")["skill_entry"])

    def test_story_and_bug_declarations_do_not_leak_across_clauses(self):
        guide = self.project / "AGENTS.md"
        for line, allowed, blocked in (
            ("需求遵循全局 req-closeloop；缺陷按本项目修复流程执行。", "story", "bug"),
            ("需求走全局 req-closeloop；缺陷按本项目 `docs/repair.md` 执行。", "story", "bug"),
            ("所有需求按项目 OpenSpec 流程执行，缺陷通过全局 bug-closeloop。", "bug", "story"),
            ("缺陷修复工作流暂不可用。需求走全局 req-closeloop。", "story", "bug"),
        ):
            with self.subTest(line=line):
                guide.write_text(line, encoding="utf-8")
                self.load(allowed)
                with self.assertRaisesRegex(StateError, "project workflow"):
                    self.load(blocked)

    def test_concrete_local_entry_beats_conflicting_prose(self):
        (self.project / "AGENTS.md").write_text("缺陷走全局 bug-closeloop。", encoding="utf-8")
        (self.project / ".claude/skills/project-bugfix").mkdir(parents=True)
        with self.assertRaisesRegex(StateError, "project workflow"):
            self.load("bug")

    def test_parent_workflow_and_incomplete_local_skill_cannot_be_skipped(self):
        missing = self.project / ".claude" / "skills" / "openspec-apply"
        missing.mkdir(parents=True)  # known project workflow, broken install should not enable fallback
        nested_repo = self.project / "backend"
        (nested_repo / ".git").mkdir(parents=True)
        for root in (self.project, nested_repo):
            with self.subTest(root=root), self.assertRaisesRegex(StateError, "project workflow"):
                self.load(project_root=str(root))

    def test_workspace_mapping_parent_is_not_proof_of_no_project_workflow(self):
        marker = self.project / "agent" / ".agents" / "skills"
        marker.mkdir(parents=True)
        (marker / "project-bugfix.md").write_text("active workflow", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "project workflow"):
            self.load("bug")

    def test_repo_specific_workflow_at_mapped_worktree_blocks_global(self):
        repo = self.home / "mapped-worktree"
        repo.mkdir()
        before = self.load("bug", workspaces={"api": str(repo)})
        (repo / "CLAUDE.md").write_text("缺陷修复流程在 docs/workflows/fix.md。", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "project workflow"):
            self.load("bug", workspaces={"api": str(repo)})
        with self.assertRaisesRegex(StateError, "project workflow"):
            validate_project_start(before, "reviewer", self.project)

    def test_scratch_and_linked_worktree_cannot_stand_in_for_rule_root(self):
        for path in (self.home / "scratch", self.project / "wt" / "story"):
            path.mkdir(parents=True)
            if "wt" in path.parts:
                (path / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
            with self.subTest(path=path), self.assertRaisesRegex(StateError, "root"):
                self.load(project_root=str(path))

    def test_private_library_can_recognize_an_unversioned_multi_repo_root(self):
        lib = self.home / "private-library"
        lib.mkdir()
        root = self.home / "multi-repo"
        root.mkdir()
        (lib / "映射.json").write_text(json.dumps({"projects": {"Example": ["../multi-repo"]}}), encoding="utf-8")
        with patch.dict(os.environ, {"WORK_SKILLS_LIBRARY": str(lib)}):
            self.assertEqual(str(root), self.load(project_root=str(root))["project_root"])
            with self.assertRaisesRegex(StateError, "root"):
                self.load(project_root=str(lib))

    def test_project_without_global_source_still_refuses_any_external_entry(self):
        with self.assertRaises(StateError):
            self.load(workflow_source=None)
        self.load()
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        raw.pop("workflow_source")
        self.manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(StateError, "must belong"):
            load_assignment(self.manifest_path)


if __name__ == "__main__":
    unittest.main()
