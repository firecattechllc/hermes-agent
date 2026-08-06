from __future__ import annotations

import os
import stat
import subprocess
import threading
import time

import pytest

from sigil.ai.goose import (
    GOOSE_GOVERNANCE_BOUNDARIES,
    GooseConcurrencyLimiter,
    GooseConfigurationError,
    GooseInspector,
    GooseProcessResult,
    GooseVersion,
    GooseWorkerConfig,
    GooseWorkerProvider,
    GooseWorkspaceDenied,
    SubprocessGooseRunner,
    _minimal_environment,
    _validate_workspace,
    parse_goose_version,
    redact_secrets,
    resolve_goose_executable,
)
from sigil.ai.ledger import DurableAIEvidenceLedger
from sigil.ai.models import Capability
from sigil.ai.provider import ProviderFailureClass, ProviderInvocation

NOW = "2026-08-05T12:00:00Z"

FAKE_GOOSE_SCRIPT = """#!/usr/bin/env python3
import json
import sys
import time

args = sys.argv[1:]

if args and args[0] == "--version":
    print("goose 1.45.0")
    sys.exit(0)

if args and args[0] == "run":
    text = ""
    if "-t" in args:
        text = args[args.index("-t") + 1]
    if text == "TRIGGER_FAILURE":
        sys.stderr.write("simulated failure api_key=sk-shouldnotleak1234567890\\n")
        sys.exit(2)
    if text == "TRIGGER_HANG":
        time.sleep(30)
        sys.exit(0)
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "ECHO:" + text}],
            }
        ],
        "metadata": {
            "status": "completed",
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
        },
    }
    print(json.dumps(payload))
    sys.exit(0)

sys.exit(1)
"""


@pytest.fixture
def fake_goose(tmp_path):
    script = tmp_path / "goose"
    script.write_text(FAKE_GOOSE_SCRIPT)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


@pytest.fixture
def dummy_executable(tmp_path):
    """A real, executable file with no goose-like behavior.

    Used by tests that inject a FakeGooseRunner (so the file's actual
    contents never run) but still need executable *detection* to succeed
    without depending on a real ``goose`` binary being on PATH — this must
    hold in CI, which has no Goose install.
    """

    path = tmp_path / "dummy-goose"
    path.write_text("#!/usr/bin/env python3\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def config(**changes):
    values = {"enabled": True, "executable": "goose"}
    values.update(changes)
    return GooseWorkerConfig(**values)


def invocation(model="gemma4:12b", payload=None, timeout_ms=5_000):
    return ProviderInvocation(
        request_id="request-1",
        task_correlation_id="task-1",
        model_id=model,
        registry_revision="sha256:" + "a" * 64,
        capability=Capability.CODING,
        input_payload=payload if payload is not None else {"instructions": "do the thing"},
        timeout_ms=timeout_ms,
        started_at=NOW,
        ended_at=NOW,
    )


class FakeGooseRunner:
    def __init__(self, *, version_result=None, run_result=None):
        self.version_result = version_result or GooseProcessResult(
            returncode=0, stdout=b"goose 1.45.0\n", stderr=b""
        )
        self.run_result = run_result
        self.calls: list[dict[str, object]] = []

    def run(self, args, *, cwd, env, timeout_seconds, cancel_event=None):
        self.calls.append(
            {
                "args": list(args),
                "cwd": cwd,
                "env": dict(env),
                "timeout_seconds": timeout_seconds,
                "cancel_event": cancel_event,
            }
        )
        if len(args) >= 2 and args[1] == "--version":
            return self.version_result
        return self.run_result


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_config_is_disabled_by_default_with_governed_defaults():
    value = GooseWorkerConfig()
    assert value.enabled is False
    assert value.provider == "ollama"
    assert value.model == "gemma4:12b"
    assert value.allowed_workspaces == ()
    assert value.max_concurrent_jobs == 1


def test_config_rejects_out_of_bound_values():
    with pytest.raises(GooseConfigurationError):
        GooseWorkerConfig(executable="   ")
    with pytest.raises(GooseConfigurationError):
        GooseWorkerConfig(timeout_ms=100)
    with pytest.raises(GooseConfigurationError):
        GooseWorkerConfig(max_turns=0)
    with pytest.raises(GooseConfigurationError):
        GooseWorkerConfig(max_concurrent_jobs=99)
    with pytest.raises(GooseConfigurationError):
        GooseWorkerConfig(allowed_workspaces=("relative/path",))
    # provider/model reuse ai.models.validate_identifier, which fails closed
    # with a plain ValueError (same convention as MacOllamaProfileConfig).
    with pytest.raises(ValueError):
        GooseWorkerConfig(provider="Not Lowercase")


def test_config_from_environment_reads_governed_env_vars(tmp_path):
    workspace = str(tmp_path)
    env = {
        "SIGIL_AI_GOOSE_ENABLED": "true",
        "SIGIL_AI_GOOSE_EXECUTABLE": "/usr/local/bin/goose",
        "SIGIL_AI_GOOSE_PROVIDER": "ollama",
        "SIGIL_AI_GOOSE_MODEL": "gemma4:12b",
        "SIGIL_AI_GOOSE_ALLOWED_WORKSPACES": workspace,
        "SIGIL_AI_GOOSE_TIMEOUT_MS": "5000",
        "SIGIL_AI_GOOSE_MAX_CONCURRENT_JOBS": "2",
    }
    result = GooseWorkerConfig.from_environment(env)
    assert result.enabled is True
    assert result.executable == "/usr/local/bin/goose"
    assert result.allowed_workspaces == (workspace,)
    assert result.timeout_ms == 5_000
    assert result.max_concurrent_jobs == 2


def test_config_from_environment_defaults_disabled_when_unset():
    result = GooseWorkerConfig.from_environment({})
    assert result.enabled is False


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("goose 1.45.0", GooseVersion("1.45.0", 1, 45, 0)),
        (" 1.45.0\n", GooseVersion("1.45.0", 1, 45, 0)),
        ("goose-cli version 2.0.10 (build abc)", GooseVersion("2.0.10", 2, 0, 10)),
    ],
)
def test_parse_goose_version_accepts_common_formats(text, expected):
    assert parse_goose_version(text) == expected


def test_parse_goose_version_returns_none_for_garbage():
    assert parse_goose_version("not a version") is None
    assert parse_goose_version("") is None


# ---------------------------------------------------------------------------
# Executable detection
# ---------------------------------------------------------------------------


def test_resolve_executable_via_explicit_path(fake_goose):
    assert resolve_goose_executable(fake_goose) == fake_goose


def test_resolve_executable_missing_explicit_path_returns_none(tmp_path):
    missing = str(tmp_path / "does-not-exist")
    assert resolve_goose_executable(missing) is None


def test_resolve_executable_rejects_non_executable_file(tmp_path):
    path = tmp_path / "goose"
    path.write_text("not executable")
    os.chmod(path, 0o644)
    assert resolve_goose_executable(str(path)) is None


def test_resolve_executable_via_path_lookup(monkeypatch, fake_goose):
    monkeypatch.setenv("PATH", os.path.dirname(fake_goose))
    monkeypatch.setattr(os.path, "basename", os.path.basename)
    import shutil

    resolved = shutil.which(os.path.basename(fake_goose))
    assert resolved is not None
    assert resolve_goose_executable(os.path.basename(fake_goose)) == resolved


def test_inspector_status_reports_installed_and_version(fake_goose):
    status = GooseInspector(config(executable=fake_goose)).status()
    assert status["installed"] is True
    assert status["health"] == "healthy"
    assert status["version"] == "1.45.0"
    assert status["readiness"] == "ready"
    for key, value in GOOSE_GOVERNANCE_BOUNDARIES.items():
        assert status[key] == value


def test_inspector_status_missing_executable_is_unavailable(tmp_path):
    missing = str(tmp_path / "no-goose")
    status = GooseInspector(config(executable=missing)).status()
    assert status["installed"] is False
    assert status["health"] == "unavailable"
    assert status["reason"] == "executable_not_found"


def test_inspector_status_disabled_by_default(fake_goose):
    status = GooseInspector(config(executable=fake_goose, enabled=False)).status()
    assert status["health"] == "disabled"
    assert status["enabled"] is False


# ---------------------------------------------------------------------------
# Environment sanitization / secret handling
# ---------------------------------------------------------------------------


def test_minimal_environment_never_leaks_full_process_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-appear-1234567890")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/tester")
    env = _minimal_environment()
    assert "OPENAI_API_KEY" not in env
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/tester"


def test_redact_secrets_masks_known_patterns():
    text = "leaked api_key=sk-abcdefghijklmnop and Bearer abcdefghij1234567890"
    redacted = redact_secrets(text)
    assert "sk-abcdefghijklmnop" not in redacted
    assert "abcdefghij1234567890" not in redacted
    assert "[redacted]" in redacted


# ---------------------------------------------------------------------------
# Workspace boundaries
# ---------------------------------------------------------------------------


def test_validate_workspace_allows_configured_directory_and_subdirectory(tmp_path):
    allowed = (str(tmp_path),)
    sub = tmp_path / "nested"
    sub.mkdir()
    assert _validate_workspace(str(tmp_path), allowed) == os.path.realpath(str(tmp_path))
    assert _validate_workspace(str(sub), allowed) == os.path.realpath(str(sub))


def test_validate_workspace_denies_unlisted_directory(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(GooseWorkspaceDenied):
        _validate_workspace(str(other), (str(tmp_path / "allowed"),))


def test_validate_workspace_denies_relative_path(tmp_path):
    with pytest.raises(GooseWorkspaceDenied):
        _validate_workspace("relative/path", (str(tmp_path),))


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrency_limiter_fails_closed_when_exhausted():
    limiter = GooseConcurrencyLimiter(1)
    assert limiter.try_acquire() is True
    assert limiter.active_jobs == 1
    assert limiter.try_acquire() is False
    limiter.release()
    assert limiter.active_jobs == 0
    assert limiter.try_acquire() is True


# ---------------------------------------------------------------------------
# invoke() with the fake (double) runner — fast, deterministic unit tests
# ---------------------------------------------------------------------------


def test_invoke_disabled_worker_fails_closed_without_spawning():
    runner = FakeGooseRunner()
    provider = GooseWorkerProvider(config(enabled=False), runner=runner)
    result = provider.invoke(invocation())
    assert not result.succeeded
    assert result.failure.classification is ProviderFailureClass.UNAVAILABLE
    assert runner.calls == []


def test_invoke_rejects_model_mismatch():
    provider = GooseWorkerProvider(config(), runner=FakeGooseRunner())
    result = provider.invoke(invocation(model="other-model"))
    assert result.failure.classification is ProviderFailureClass.MODEL_IDENTITY_MISMATCH


def test_invoke_rejects_capability_mismatch():
    provider = GooseWorkerProvider(config(), runner=FakeGooseRunner())
    invocation_obj = ProviderInvocation(
        request_id="r",
        task_correlation_id="t",
        model_id="gemma4:12b",
        registry_revision="sha256:" + "b" * 64,
        capability=Capability.EMBEDDINGS,
        input_payload={"instructions": "x"},
        timeout_ms=1000,
        started_at=NOW,
        ended_at=NOW,
    )
    result = provider.invoke(invocation_obj)
    assert result.failure.classification is ProviderFailureClass.CAPABILITY_MISMATCH


def test_invoke_requires_instructions():
    provider = GooseWorkerProvider(config(), runner=FakeGooseRunner())
    result = provider.invoke(invocation(payload={}))
    assert result.failure.classification is ProviderFailureClass.MALFORMED_OUTPUT


def test_invoke_denies_workspace_outside_allowlist(tmp_path):
    provider = GooseWorkerProvider(
        config(allowed_workspaces=(str(tmp_path / "allowed"),)),
        runner=FakeGooseRunner(),
    )
    result = provider.invoke(
        invocation(payload={"instructions": "x", "workspace": str(tmp_path / "other")})
    )
    assert result.failure.classification is ProviderFailureClass.MALFORMED_OUTPUT


def test_invoke_allows_workspace_inside_allowlist(tmp_path, dummy_executable):
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(
            returncode=0,
            stdout=(
                b'{"messages":[{"role":"assistant","content":'
                b'[{"type":"text","text":"ok"}]}],'
                b'"metadata":{"status":"completed","input_tokens":1,'
                b'"output_tokens":1,"total_tokens":2}}'
            ),
            stderr=b"",
        )
    )
    provider = GooseWorkerProvider(
        config(executable=dummy_executable, allowed_workspaces=(str(tmp_path),)), runner=runner
    )
    result = provider.invoke(invocation(payload={"instructions": "x", "workspace": str(tmp_path)}))
    assert result.succeeded
    run_call = runner.calls[-1]
    assert run_call["cwd"] == os.path.realpath(str(tmp_path))


def test_invoke_missing_executable_fails_closed(tmp_path):
    missing = str(tmp_path / "no-goose")
    provider = GooseWorkerProvider(config(executable=missing), runner=FakeGooseRunner())
    result = provider.invoke(invocation())
    assert result.failure.classification is ProviderFailureClass.UNAVAILABLE
    assert "not found" in result.failure.message.lower()


def test_invoke_concurrency_limit_exceeded(fake_goose):
    runner = FakeGooseRunner()
    limiter = GooseConcurrencyLimiter(1)
    assert limiter.try_acquire() is True  # simulate an in-flight job
    provider = GooseWorkerProvider(
        config(executable=fake_goose), runner=runner, concurrency=limiter
    )
    result = provider.invoke(invocation())
    assert result.failure.classification is ProviderFailureClass.UNAVAILABLE
    assert "concurrency" in result.failure.message.lower()


def test_invoke_successful_execution_normalizes_result_and_updates_state(fake_goose):
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(
            returncode=0,
            stdout=(
                b'{"messages":[{"role":"user","content":[{"type":"text","text":"hi"}]},'
                b'{"role":"assistant","content":[{"type":"text","text":"OK"}]}],'
                b'"metadata":{"status":"completed","input_tokens":10,'
                b'"output_tokens":5,"total_tokens":15}}'
            ),
            stderr=b"",
        )
    )
    provider = GooseWorkerProvider(config(executable=fake_goose), runner=runner)
    result = provider.invoke(invocation())
    assert result.succeeded
    assert result.output["text"] == "OK"
    assert result.output["truncated"] is False
    assert result.output["total_tokens"] == 15
    for key, value in GOOSE_GOVERNANCE_BOUNDARIES.items():
        assert result.output[key] == value
    assert provider.last_execution["succeeded"] is True
    assert provider.active_jobs == 0

    run_call = runner.calls[-1]
    assert run_call["args"][:5] == [fake_goose, "run", "--no-profile", "--no-session", "-q"]
    assert "--with-extension" not in run_call["args"]
    assert "--with-builtin" not in run_call["args"]
    assert "-s" not in run_call["args"]
    assert "--interactive" not in run_call["args"]


def test_invoke_nonzero_exit_fails_closed_and_redacts_stderr(dummy_executable):
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(
            returncode=2,
            stdout=b"",
            stderr=b"boom api_key=sk-leakedsecret1234567890",
        )
    )
    provider = GooseWorkerProvider(config(executable=dummy_executable), runner=runner)
    result = provider.invoke(invocation())
    assert not result.succeeded
    assert result.failure.classification is ProviderFailureClass.UNAVAILABLE
    assert "sk-leakedsecret1234567890" not in result.failure.message


def test_invoke_timeout_is_reported_distinctly(dummy_executable):
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(returncode=None, stdout=b"", stderr=b"", timed_out=True)
    )
    provider = GooseWorkerProvider(config(executable=dummy_executable), runner=runner)
    result = provider.invoke(invocation())
    assert result.failure.classification is ProviderFailureClass.TIMEOUT


def test_invoke_cancellation_is_reported_distinctly(dummy_executable):
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(returncode=None, stdout=b"", stderr=b"", cancelled=True)
    )
    provider = GooseWorkerProvider(config(executable=dummy_executable), runner=runner)
    result = provider.invoke(invocation(), cancel_event=threading.Event())
    assert result.failure.classification is ProviderFailureClass.CANCELLED


def test_invoke_malformed_json_output_fails_closed(dummy_executable):
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(returncode=0, stdout=b"not json", stderr=b"")
    )
    provider = GooseWorkerProvider(config(executable=dummy_executable), runner=runner)
    result = provider.invoke(invocation())
    assert result.failure.classification is ProviderFailureClass.MALFORMED_OUTPUT


def test_invoke_incomplete_status_fails_closed(dummy_executable):
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(
            returncode=0,
            stdout=(
                b'{"messages":[{"role":"assistant","content":[{"type":"text","text":"partial"}]}],'
                b'"metadata":{"status":"max_turns_reached"}}'
            ),
            stderr=b"",
        )
    )
    provider = GooseWorkerProvider(config(executable=dummy_executable), runner=runner)
    result = provider.invoke(invocation())
    assert not result.succeeded
    assert "max_turns_reached" in result.failure.message


def test_invoke_output_truncation_is_enforced_and_flagged(dummy_executable):
    long_text = "x" * 5_000
    payload = (
        '{"messages":[{"role":"assistant","content":[{"type":"text","text":"'
        + long_text
        + '"}]}],"metadata":{"status":"completed"}}'
    ).encode("utf-8")
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(returncode=0, stdout=payload, stderr=b"")
    )
    provider = GooseWorkerProvider(
        config(executable=dummy_executable, max_output_bytes=1_000), runner=runner
    )
    result = provider.invoke(invocation())
    assert result.succeeded
    assert result.output["truncated"] is True
    assert len(result.output["text"].encode("utf-8")) <= 1_000


def test_invoke_redacts_secrets_from_model_output(dummy_executable):
    payload = (
        '{"messages":[{"role":"assistant","content":[{"type":"text",'
        '"text":"here is api_key=sk-abcdefghijklmno for you"}]}],'
        '"metadata":{"status":"completed"}}'
    ).encode("utf-8")
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(returncode=0, stdout=payload, stderr=b"")
    )
    provider = GooseWorkerProvider(config(executable=dummy_executable), runner=runner)
    result = provider.invoke(invocation())
    assert "sk-abcdefghijklmno" not in result.output["text"]


def test_invoke_records_evidence_with_governed_boundaries_and_no_secrets(
    tmp_path, dummy_executable
):
    payload = (
        '{"messages":[{"role":"assistant","content":[{"type":"text","text":"OK"}]}],'
        '"metadata":{"status":"completed","input_tokens":1,"output_tokens":1,"total_tokens":2}}'
    ).encode("utf-8")
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(returncode=0, stdout=payload, stderr=b"")
    )
    ledger = DurableAIEvidenceLedger(tmp_path)
    provider = GooseWorkerProvider(
        config(executable=dummy_executable), runner=runner, ledger=ledger
    )
    result = provider.invoke(invocation())
    assert result.succeeded
    records = ledger.read_records()
    assert len(records) == 1
    record = records[0]
    assert record.record_type.value == "provider_result_succeeded"
    assert record.provider_id == "goose-cli"
    assert record.paper_only is True
    assert record.broker_submission is False
    assert record.succeeded is True
    # Evidence stores only digests — raw model output must never be persisted.
    assert "OK" not in str(record.provider_metadata)


def test_provider_never_grants_sigil_or_fleet_authority(fake_goose):
    runner = FakeGooseRunner(
        run_result=GooseProcessResult(
            returncode=0,
            stdout=(
                b'{"messages":[{"role":"assistant","content":[{"type":"text","text":"ok"}]}],'
                b'"metadata":{"status":"completed"}}'
            ),
            stderr=b"",
        )
    )
    provider = GooseWorkerProvider(config(executable=fake_goose), runner=runner)
    result = provider.invoke(invocation())
    assert result.output["broker_submission"] is False
    assert result.output["execution_authorized"] is False
    assert result.output["capital_authority"] is False
    assert result.output["policy_mutation"] is False
    assert result.output["credential_access"] is False
    assert result.output["arbitrary_shell"] is False
    assert result.output["arbitrary_filesystem"] is False
    assert result.output["governance_bypass"] is False
    assert result.output["fleet_administrative_authority"] is False
    assert not hasattr(provider, "sigil_execution")
    assert not hasattr(provider, "fleet_admin")


# ---------------------------------------------------------------------------
# Real subprocess integration tests (fake goose executable, no network/Ollama)
# ---------------------------------------------------------------------------


def test_integration_successful_governed_execution(fake_goose):
    provider = GooseWorkerProvider(config(executable=fake_goose))
    result = provider.invoke(invocation(payload={"instructions": "hello world"}))
    assert result.succeeded
    assert result.output["text"] == "ECHO:hello world"


def test_integration_nonzero_exit_via_real_subprocess(fake_goose):
    provider = GooseWorkerProvider(config(executable=fake_goose))
    result = provider.invoke(invocation(payload={"instructions": "TRIGGER_FAILURE"}))
    assert not result.succeeded
    assert result.failure.classification is ProviderFailureClass.UNAVAILABLE
    assert "sk-shouldnotleak1234567890" not in result.failure.message


def test_integration_timeout_via_real_subprocess(fake_goose):
    provider = GooseWorkerProvider(config(executable=fake_goose, timeout_ms=1_000))
    started = time.monotonic()
    result = provider.invoke(invocation(payload={"instructions": "TRIGGER_HANG"}, timeout_ms=1_000))
    elapsed = time.monotonic() - started
    assert result.failure.classification is ProviderFailureClass.TIMEOUT
    assert elapsed < 10


def test_integration_cancellation_via_real_subprocess(fake_goose):
    provider = GooseWorkerProvider(config(executable=fake_goose, timeout_ms=30_000))
    cancel_event = threading.Event()

    def cancel_soon():
        time.sleep(0.3)
        cancel_event.set()

    threading.Thread(target=cancel_soon, daemon=True).start()
    started = time.monotonic()
    result = provider.invoke(
        invocation(payload={"instructions": "TRIGGER_HANG"}, timeout_ms=30_000),
        cancel_event=cancel_event,
    )
    elapsed = time.monotonic() - started
    assert result.failure.classification is ProviderFailureClass.CANCELLED
    assert elapsed < 10


def test_integration_no_shell_injection_instructions_pass_through_literally(fake_goose):
    dangerous = "hello; rm -rf / && echo pwned $(whoami) `id`"
    provider = GooseWorkerProvider(config(executable=fake_goose))
    result = provider.invoke(invocation(payload={"instructions": dangerous}))
    assert result.succeeded
    assert result.output["text"] == "ECHO:" + dangerous


def test_integration_no_shell_injection_workspace_marker_untouched(tmp_path, fake_goose):
    marker = tmp_path / "should-not-exist"
    dangerous = f"x; touch {marker}"
    provider = GooseWorkerProvider(config(executable=fake_goose))
    provider.invoke(invocation(payload={"instructions": dangerous}))
    assert not marker.exists()


def test_subprocess_runner_never_uses_shell(monkeypatch, fake_goose):
    captured = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        captured["shell"] = kwargs.get("shell", False)
        captured["args"] = args[0]
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    runner = SubprocessGooseRunner()
    result = runner.run(
        [fake_goose, "--version"], cwd=None, env=_minimal_environment(), timeout_seconds=10
    )
    assert result.returncode == 0
    assert captured["shell"] is False
    assert isinstance(captured["args"], list)
