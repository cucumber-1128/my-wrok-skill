"""Public preview and Windows/POSIX wrapper contract with a private hc substitute."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


class ProfileCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="herdr-engine-cli-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.calls = self.root / "calls.jsonl"
        stub = self.root / "hc_stub.py"
        stub.write_text(
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['HERDR_TEST_HC_CALLS'],'a',encoding='utf-8') as log:\n"
            "    print(json.dumps(args), file=log)\n"
            "if args[:2] != ['run','--json']:\n"
            "    raise SystemExit(1)\n"
            "print(json.dumps({'address':'stub-'+args[args.index('--name')+1],'state':'live'}))\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            exe = self.root / "hc.cmd"
            exe.write_text(f'@"{sys.executable}" -B "{stub}" %*\n', encoding="utf-8")
        else:
            import shlex
            exe = self.root / "hc"
            exe.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B "
                           + shlex.quote(str(stub)) + ' "$@"\n', encoding="utf-8")
            exe.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                        HERDR_STATE_DIR=str(self.state), HERDR_PYTHON=sys.executable,
                        HERDR_TEST_HC_CALLS=str(self.calls), HC_TERMINAL_ID="",
                        PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        self.assertEqual(exe.resolve(), Path(shutil.which("hc", path=self.env["PATH"])).resolve())

    def call(self, script, *args, good=True):
        p = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / script),
                            *map(str, args)], env=self.env, cwd=self.root, capture_output=True,
                           text=True, encoding="utf-8", timeout=30)
        self.assertEqual(0 if good else 1, p.returncode, p.stderr + p.stdout)
        return json.loads(p.stdout if good else p.stderr)

    def test_builtin_previews_only_swap_development_and_review_routes(self):
        fixed = {
            "leader": ("codex", "codex/gpt-6.2-astra"),
            **{r: ("claude", "auto/deepseek-v4.1-flash") for r in (
                "scout", "router", "watchdog", "tester", "test-worker")},
        }
        for profile, development, review in (("delivery", "group/group", "devin/swe-2"),
                                             ("delivery-swapped", "devin/swe-2", "group/group")):
            with self.subTest(profile=profile):
                data = self.call("sa-state.ps1", "profile", "show", "--workflow-profile", profile)
                expected = {
                    **fixed,
                    **{r: ("claude", development) for r in ("dev-worker", "implementer", "simplifier")},
                    **{r: ("claude", review) for r in ("review-worker", "reviewer")},
                }
                self.assertEqual(profile, data["name"])
                self.assertEqual(expected, {r: (v["harness"], v["model"]) for r, v in data["roles"].items()})
                self.assertEqual({"browser": {"harness": "codex", "model": "codex/gpt-6.2-sol"}},
                                 data["purposes"])
        self.assertFalse(self.state.exists(), "Route preview must not initialize SQLite")
        self.assertFalse(self.calls.exists(), "Route preview must not contact hc")

    def test_swapped_spawn_exchanges_models_without_exchanging_role_files(self):
        for role, model in (("dev-worker", "devin/swe-2"), ("review-worker", "group/group")):
            with self.subTest(role=role):
                result = self.call("sa-spawn.ps1", "-Name", role, "-Role", role, "-Cwd", self.root,
                                   "-NoWait", "-WorkflowProfile", "delivery-swapped")
                cmd = json.loads(self.calls.read_text(encoding="utf-8").splitlines()[-1])
                argv = cmd[cmd.index("--") + 1:]
                self.assertEqual("claude", Path(argv[0]).stem)
                self.assertEqual(model, argv[argv.index("--model") + 1])
                self.assertEqual("delivery-swapped", result["route"]["profile"])
                agent = self.call("sa-state.ps1", "agent", "get", "--name", role)
                self.assertEqual(role, agent["role"])
                self.assertEqual(role, Path(agent["request"]["role_file"]).stem)

    def test_preview_of_missing_profile_fails_without_creating_state(self):
        err = self.call("sa-state.ps1", "profile", "show", "--workflow-profile",
                        self.root / "missing.json", good=False)
        self.assertFalse(err["ok"])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.calls.exists())

    def test_show_accepts_a_local_profile_path_with_spaces(self):
        custom = self.root / "team routes.json"
        data = json.loads((SKILL / "profiles" / "delivery.json").read_text(encoding="utf-8"))
        data["name"] = "team"
        data["roles"]["reviewer"]["model"] = "test/reviewer"
        custom.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(data, self.call("sa-state.ps1", "profile", "show", "--workflow-profile", custom))
        self.assertFalse(self.state.exists())
        self.assertFalse(self.calls.exists())

    def test_real_spawn_wrapper_selects_sol_only_for_explicit_browser_purpose(self):
        for name, extra, harness, model in (
                ("plain", [], "claude", "auto/deepseek-v4.1-flash"),
                ("web", ["-Purpose", "browser"], "codex", "codex/gpt-6.2-sol")):
            result = self.call("sa-spawn.ps1", "-Name", name, "-Cwd", self.root, "-Role", "tester",
                               "-NoWait", "-WorkflowProfile", "delivery", *extra)
            self.assertEqual("PENDING", result["delivery"])
            invocation = json.loads(self.calls.read_text(encoding="utf-8").splitlines()[-1])
            argv = invocation[invocation.index("--") + 1:]
            self.assertEqual(harness, Path(argv[0]).stem)
            self.assertEqual(model, argv[argv.index("--model") + 1])
            if harness == "codex":
                self.assertIn("--no-daemon", argv)
                self.assertNotIn("--add-dir", argv)
                self.assertNotIn("--permission-mode", argv)
        self.assertEqual(2, len(self.calls.read_text(encoding="utf-8").splitlines()))

    def test_resume_rejects_route_overrides_before_calling_hc(self):
        for extra in (["-WorkflowProfile", "delivery"], ["-Purpose", "browser"], ["-Model", "other"]):
            with self.subTest(extra=extra):
                err = self.call("sa-spawn.ps1", "-Name", "some-start", "-Resume", *extra, good=False)
                self.assertIn("resume", err["error"]["message"].lower())
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
