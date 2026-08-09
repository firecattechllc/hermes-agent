import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("installer", SCRIPT_DIR / "install_agent_watch_hooks.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)

CODEX_EMITTER_PATH = (
    SCRIPT_DIR.parent / "Sigil/Sigil/Support/agent_watch_codex_emitter.py"
)
SUPPORT_DIRECTORY = CODEX_EMITTER_PATH.parent


def load_emitter(path: pathlib.Path, name: str):
    resolver_spec = importlib.util.spec_from_file_location(
        "agent_watch_paths", SUPPORT_DIRECTORY / "agent_watch_paths.py"
    )
    resolver = importlib.util.module_from_spec(resolver_spec)
    resolver_spec.loader.exec_module(resolver)
    sys.modules["agent_watch_paths"] = resolver
    spec = importlib.util.spec_from_file_location(name, path)
    emitter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(emitter)
    return emitter


def load_codex_emitter():
    return load_emitter(CODEX_EMITTER_PATH, "codex_emitter")


class AgentWatchHookTests(unittest.TestCase):
    def test_installer_prefers_installed_release_emitters(self):
        project = pathlib.Path("/tmp/project")
        info = {"CFBundleIdentifier": "com.firecattechnology.sigil.macos"}
        with unittest.mock.patch.object(installer.pathlib.Path, "is_file", return_value=True), \
             unittest.mock.patch.object(installer.pathlib.Path, "read_bytes", return_value=installer.plistlib.dumps(info)):
            self.assertEqual(
                installer.emitter_product(project),
                (pathlib.Path("/Applications/Sigil.app/Contents/Resources"), "com.firecattechnology.sigil.macos"),
            )

    def test_installer_uses_explicit_debug_identity_without_release(self):
        project = pathlib.Path("/tmp/project")
        with unittest.mock.patch.object(installer.pathlib.Path, "is_file", return_value=False):
            self.assertEqual(
                installer.emitter_product(project),
                (project / "Sigil/Sigil/Support", "com.firecattechnology.Sigil.dev"),
            )

    def test_merge_replaces_stale_agent_watch_command(self):
        config = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /old/agent_watch_codex_emitter.py"}]}]}}
        changed = installer.merge_hooks(config, ("Stop",), "/usr/bin/python3 /new/agent_watch_codex_emitter.py")
        self.assertTrue(changed)
        self.assertEqual(config["hooks"]["Stop"][0]["hooks"][0]["command"], "/usr/bin/python3 /new/agent_watch_codex_emitter.py")

    def test_merge_replaces_legacy_hyphenated_agent_watch_command(self):
        legacy = "/usr/bin/python3 /Users/test/.local/bin/sigil-agent-watch-codex-emitter.py"
        command = "/usr/bin/python3 /Applications/Sigil.app/Contents/Resources/agent_watch_codex_emitter.py --bundle-identifier com.firecattechnology.sigil.macos"
        config = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": legacy}]}]}}
        self.assertTrue(installer.merge_hooks(config, ("Stop",), command))
        flattened = [hook for group in config["hooks"]["Stop"] for hook in group.get("hooks", [])]
        self.assertEqual([hook["command"] for hook in flattened], [command])

    def test_merge_removes_duplicates_without_removing_unrelated_group_hooks(self):
        command = "/usr/bin/python3 /Applications/Sigil.app/Contents/Resources/agent_watch_codex_emitter.py --bundle-identifier com.firecattechnology.sigil.macos"
        unrelated = {"type": "command", "command": "/usr/bin/python3 /safe/unrelated.py"}
        config = {"hooks": {"Stop": [
            {"matcher": "all", "hooks": [unrelated, {"type": "command", "command": "/old/agent_watch_codex_emitter.py"}]},
            {"hooks": [{"type": "command", "command": command}]},
            {"hooks": [{"type": "command", "command": command}]},
        ]}}
        self.assertTrue(installer.merge_hooks(config, ("Stop",), command))
        flattened = [hook for group in config["hooks"]["Stop"] for hook in group.get("hooks", [])]
        self.assertEqual(sum(hook.get("command") == command for hook in flattened), 1)
        self.assertIn(unrelated, flattened)

    def test_shared_resolver_selects_release_and_debug_paths_independently(self):
        emitter = load_codex_emitter()
        home = pathlib.Path("/Users/test")
        self.assertEqual(
            emitter.evidence_directory("com.firecattechnology.sigil.macos", home=home),
            home / "Library/Containers/com.firecattechnology.sigil.macos/Data/Library/Application Support/Sigil/AgentWatch/Events",
        )
        self.assertEqual(
            emitter.evidence_directory("com.firecattechnology.Sigil.dev", home=home),
            home / "Library/Containers/com.firecattechnology.Sigil.dev/Data/Library/Application Support/SigilDev/AgentWatch/Events",
        )
        with self.assertRaises(ValueError):
            emitter.evidence_directory("unknown", home=home)
    def test_codex_parent_pid_uses_documented_libproc_field(self):
        emitter = load_codex_emitter()
        class LibProc:
            def proc_pidinfo(self, pid, flavor, argument, pointer, size):
                self.call = (pid, flavor, argument, size)
                pointer._obj.pbi_ppid = 10640
                return size

        library = LibProc()
        self.assertEqual(emitter.parent_pid(988, library=library), 10640)
        self.assertEqual(library.call, (988, 3, 0, 136))

    def test_codex_ancestor_traverses_multiple_levels(self):
        emitter = load_codex_emitter()
        parents = {300: 200, 200: 100, 100: 1}
        paths = {300: "/bin/zsh", 200: "/usr/bin/python3", 100: "/Applications/Codex.app/Contents/MacOS/codex"}
        self.assertEqual(
            emitter.codex_ancestor(
                start_pid=300,
                parent_resolver=lambda pid: parents[pid],
                path_resolver=lambda pid: paths[pid],
            ),
            100,
        )

    def test_codex_ancestor_returns_zero_without_match_or_parent(self):
        emitter = load_codex_emitter()
        self.assertEqual(
            emitter.codex_ancestor(
                start_pid=300,
                parent_resolver=lambda _pid: 0,
                path_resolver=lambda _pid: "/bin/zsh",
            ),
            0,
        )
        class FailedLibProc:
            def proc_pidinfo(self, *_args):
                return 0

        self.assertEqual(emitter.parent_pid(999999, library=FailedLibProc()), 0)

    def test_merge_preserves_unrelated_hooks_and_is_idempotent(self):
        config = {"other": 7, "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "existing"}]}]}}
        self.assertTrue(installer.merge_hooks(config, ("PreToolUse", "Stop"), "sigil"))
        self.assertEqual(config["other"], 7)
        self.assertEqual(config["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "existing")
        self.assertFalse(installer.merge_hooks(config, ("PreToolUse", "Stop"), "sigil"))

    def test_codex_emitter_accepts_valid_supplied_pid_and_rejects_invalid_values(self):
        emitter = load_codex_emitter()
        with unittest.mock.patch.object(emitter, "transcript_owner_pid", return_value=0), \
             unittest.mock.patch.object(emitter, "codex_ancestor", return_value=77):
            self.assertEqual(emitter.process_id({"process_id": 424242}), 424242)
            self.assertEqual(emitter.process_id({"process_id": True}), 77)
            self.assertEqual(emitter.process_id({"process_id": "424242"}), 77)
            self.assertEqual(emitter.process_id({"process_id": 1}), 77)

    def test_codex_emitter_resolves_sole_exact_transcript_owner(self):
        emitter = load_codex_emitter()
        with tempfile.NamedTemporaryFile() as transcript, \
             unittest.mock.patch.object(emitter, "process_path", return_value="/Applications/Codex.app/Contents/Resources/codex"):
            runner = unittest.mock.Mock(return_value=subprocess.CompletedProcess([], 0, "33089\n", ""))
            self.assertEqual(
                emitter.transcript_owner_pid({"transcript_path": transcript.name}, runner=runner),
                33089,
            )
            runner.assert_called_once_with(
                ["/usr/sbin/lsof", "-t", "--", transcript.name],
                check=False, capture_output=True, text=True, timeout=2,
            )

    def test_codex_emitter_transcript_owner_fails_closed(self):
        emitter = load_codex_emitter()
        self.assertEqual(emitter.transcript_owner_pid({}), 0)
        self.assertEqual(emitter.transcript_owner_pid({"transcript_path": "/missing"}), 0)
        with tempfile.NamedTemporaryFile() as transcript, \
             unittest.mock.patch.object(emitter, "process_path", return_value="/usr/bin/python3"):
            runner = unittest.mock.Mock(return_value=subprocess.CompletedProcess([], 0, "10\n11\n", ""))
            self.assertEqual(
                emitter.transcript_owner_pid({"transcript_path": transcript.name}, runner=runner),
                0,
            )

    def test_refuses_destructive_shape_replacement(self):
        with self.assertRaises(ValueError):
            installer.merge_hooks({"hooks": []}, ("Stop",), "sigil")

    def test_emitters_drop_all_raw_fields(self):
        for script_name, agent in (("agent_watch_claude_emitter.py", "claudeCode"), ("agent_watch_codex_emitter.py", "codex")):
            path = SCRIPT_DIR.parent / "Sigil/Sigil/Support" / script_name
            emitter = load_emitter(path, agent)
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

    def test_emitters_use_shared_stable_identity_resolver(self):
        for script_name in ("agent_watch_claude_emitter.py", "agent_watch_codex_emitter.py"):
            source = (SCRIPT_DIR.parent / "Sigil/Sigil/Support" / script_name).read_text(encoding="utf-8")
            self.assertIn("from agent_watch_paths import evidence_directory", source)
            self.assertIn('parser.add_argument("--bundle-identifier", required=True)', source)
            self.assertNotIn("pgrep", source)


if __name__ == "__main__":
    unittest.main()
