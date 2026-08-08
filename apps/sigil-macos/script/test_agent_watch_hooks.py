import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("installer", SCRIPT_DIR / "install_agent_watch_hooks.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class AgentWatchHookTests(unittest.TestCase):
    def test_merge_preserves_unrelated_hooks_and_is_idempotent(self):
        config = {"other": 7, "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "existing"}]}]}}
        self.assertTrue(installer.merge_hooks(config, ("PreToolUse", "Stop"), "sigil"))
        self.assertEqual(config["other"], 7)
        self.assertEqual(config["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "existing")
        self.assertFalse(installer.merge_hooks(config, ("PreToolUse", "Stop"), "sigil"))

    def test_refuses_destructive_shape_replacement(self):
        with self.assertRaises(ValueError):
            installer.merge_hooks({"hooks": []}, ("Stop",), "sigil")

    def test_emitters_drop_all_raw_fields(self):
        for script_name, agent in (("agent_watch_claude_emitter.py", "claudeCode"), ("agent_watch_codex_emitter.py", "codex")):
            path = SCRIPT_DIR.parent / "Sigil/Sigil/Support" / script_name
            spec = importlib.util.spec_from_file_location(agent, path)
            emitter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(emitter)
            record = emitter.sanitized_record({
                "hook_event_name": "PreToolUse", "prompt": "secret",
                "tool_input": {"command": "secret"}, "tool_output": "secret",
                "environment": {"TOKEN": "secret"}, "unknown": "secret",
            }, 42, "2026-08-08T00:00:00Z")
            self.assertEqual(set(record), {"agent", "processID", "event", "observedAt"})
            self.assertEqual(record["agent"], agent)
            self.assertNotIn("secret", json.dumps(record))

    def test_dry_run_does_not_write_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(SCRIPT_DIR / "install_agent_watch_hooks.py"), "--home", directory],
                check=True, capture_output=True, text=True,
            )
            self.assertIn("would update", result.stdout)
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])

    def test_emitters_gate_writes_on_sigil_liveness(self):
        for script_name in ("agent_watch_claude_emitter.py", "agent_watch_codex_emitter.py"):
            source = (SCRIPT_DIR.parent / "Sigil/Sigil/Support" / script_name).read_text(encoding="utf-8")
            self.assertIn("if not sigil_is_running():", source)
            self.assertIn('["/usr/bin/pgrep", "-x", "SigilDev"]', source)


if __name__ == "__main__":
    unittest.main()
