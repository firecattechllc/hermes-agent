from __future__ import annotations

import stat
import textwrap
from pathlib import Path

import pytest

from hermes_prime_agent_worker.config import PrimeAgentWorkerConfig


@pytest.fixture
def fake_prime_agent_script(tmp_path: Path) -> Path:
    """A fake ``prime-agent`` executable for tests that need a real
    subprocess without a real Prime Agent install or network access. Mode
    is selected via ``set_fake_mode()`` (a file under $HOME), not an env
    var -- see the script body for why."""
    script = tmp_path / "prime-agent"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            # Mode is read from a file under $HOME rather than an env var:
            # proc.py deliberately builds an explicit environment allowlist
            # for the real subprocess (no host env leakage), so a test
            # fixture must respect that same boundary rather than smuggling
            # a signal in through a variable production code would strip.
            mode="echo"
            if [ -n "$HOME" ] && [ -f "$HOME/.fake_prime_agent_mode" ]; then
                mode="$(cat "$HOME/.fake_prime_agent_mode")"
            fi
            if [ -n "$HOME" ]; then
                printf '%s\\n' "$*" > "$HOME/.fake_prime_agent_last_argv" 2>/dev/null || true
            fi
            case "$mode" in
              sleep)
                sleep 30
                ;;
              fail)
                echo "simulated failure" >&2
                exit 1
                ;;
              json)
                echo '{"type":"session","id":"fake"}'
                echo '{"type":"agent_end","messages":[]}'
                exit 0
                ;;
              daemon_status)
                echo '[{"socketPath":"/tmp/fake/daemon.sock","pid":424242,"uptimeSeconds":99,"version":"0.7.0","buildId":"fake-build","sessionCount":0}]'
                exit 0
                ;;
              json_exit_zero_but_model_failed)
                # Reproduces a real, observed Prime Agent behavior: the
                # CLI process exits 0 even though the model call itself
                # failed after exhausting retries -- the failure is only
                # visible inside the JSON-lines transcript.
                echo '{"type":"session","id":"fake"}'
                echo '{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"422 invalid_request"}}'
                echo '{"type":"auto_retry_end","success":false,"attempt":3,"finalError":"422 invalid_request"}'
                exit 0
                ;;
              large)
                yes "x" | head -c 5000000
                exit 0
                ;;
              *)
                echo "ok: $*"
                exit 0
                ;;
            esac
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture
def worker_config_factory(tmp_path: Path, fake_prime_agent_script: Path):
    def _make(**overrides: object) -> PrimeAgentWorkerConfig:
        home_dir = tmp_path / "home"
        home_dir.mkdir(exist_ok=True)
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)
        node_bin_dir = tmp_path / "node" / "bin"
        node_bin_dir.mkdir(parents=True, exist_ok=True)

        defaults: dict[str, object] = dict(
            executable=fake_prime_agent_script,
            node_bin_dir=node_bin_dir,
            home_dir=home_dir,
            workspace_allowlist=(workspace,),
            state_dir=home_dir / "state",
            cache_dir=home_dir / "cache",
            log_dir=home_dir / "logs",
            xdg_data_home=home_dir / ".local" / "share",
            provider="titan-omniroute",
            model="lightweight",
            provider_active=True,
            max_turns=6,
            max_tokens=20_000,
            timeout_seconds=10,
            gate_retries=1,
            gate_timeout_seconds=10,
            max_output_bytes=200_000,
            max_concurrent_sessions=1,
            allowed_tools=(),
            mutation_tools=("write_file",),
            allowed_network_endpoints=("http://127.0.0.1:8791",),
            evidence_retention_days=30,
            evidence_max_files=500,
            cooldown_seconds_after_failure=30,
            max_consecutive_failures_before_cooldown=3,
        )
        defaults.update(overrides)
        return PrimeAgentWorkerConfig(**defaults)

    return _make


@pytest.fixture
def workspace_dir(worker_config_factory) -> Path:
    config = worker_config_factory()
    return config.workspace_allowlist[0]


@pytest.fixture
def set_fake_mode():
    def _set(config: PrimeAgentWorkerConfig, mode: str) -> None:
        config.home_dir.mkdir(parents=True, exist_ok=True)
        (config.home_dir / ".fake_prime_agent_mode").write_text(mode)

    return _set
