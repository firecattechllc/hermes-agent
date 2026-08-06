"""Idle-model unload/cool-down manager for Titan's local Ollama.

Titan loads models only when needed and unloads/cools them down after
inactivity -- there is no permanently-resident inference process. This
module is purely reactive and has no timer or loop of its own: a caller (the
one-shot reflection cycle, :mod:`hermes_cli.prime.titan_reflection_entrypoint`)
calls :meth:`IdleModelManager.touch` each time a model is used and
:meth:`IdleModelManager.sweep` once per cycle to unload anything idle past
the configured threshold. State persists to disk (atomic write, same
pattern as :class:`hermes_cli.prime.titan_scheduler.SchedulerStateStore`)
since this is a one-shot-process world with no in-memory daemon to hold it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol, Tuple

DEFAULT_IDLE_THRESHOLD_SECONDS = 600  # 10 minutes


class IdleManagerConfigError(ValueError):
    """Idle model manager configuration is invalid."""


class OllamaUnloadTransport(Protocol):
    def post(self, url: str, payload: dict, *, timeout_seconds: float) -> object: ...


@dataclass(frozen=True, slots=True)
class UnloadOutcome:
    model: str
    attempted: bool
    succeeded: bool
    error: Optional[str] = None


class IdleModelManager:
    def __init__(
        self,
        *,
        state_path: Path,
        ollama_endpoint: str,
        idle_threshold_seconds: int = DEFAULT_IDLE_THRESHOLD_SECONDS,
        transport: Optional[OllamaUnloadTransport] = None,
    ) -> None:
        state_path = Path(state_path)
        if not state_path.is_absolute():
            raise IdleManagerConfigError("idle manager state path must be absolute")
        if idle_threshold_seconds < 1:
            raise IdleManagerConfigError("idle_threshold_seconds must be positive")
        self._state_path = state_path
        self._ollama_endpoint = ollama_endpoint.rstrip("/")
        self._idle_threshold_seconds = idle_threshold_seconds
        self._transport = transport

    def _load(self) -> Dict[str, int]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): int(v) for k, v in data.items()}

    def _save(self, last_used: Dict[str, int]) -> None:
        self._state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp_path = self._state_path.with_name(self._state_path.name + ".tmp")
        tmp_path.write_text(json.dumps(last_used, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, self._state_path)

    def touch(self, model: str, *, now: int) -> None:
        """Record that ``model`` was used at ``now``."""
        last_used = self._load()
        last_used[model] = now
        self._save(last_used)

    def last_used_models(self) -> Dict[str, int]:
        return self._load()

    def sweep(self, *, now: int) -> Tuple[UnloadOutcome, ...]:
        """Unload every model idle for at least the configured threshold.

        A model is only dropped from tracked state once an unload was
        actually attempted (whether it succeeded or not) -- a transport
        error never silently loses track of a model that might still be
        loaded.
        """
        last_used = self._load()
        outcomes = []
        remaining = dict(last_used)
        for model, used_at in last_used.items():
            if now - used_at < self._idle_threshold_seconds:
                continue
            outcome = self._unload(model)
            outcomes.append(outcome)
            if outcome.attempted:
                remaining.pop(model, None)
        if len(remaining) != len(last_used):
            self._save(remaining)
        return tuple(outcomes)

    def _unload(self, model: str) -> UnloadOutcome:
        if self._transport is None:
            return UnloadOutcome(
                model=model,
                attempted=False,
                succeeded=False,
                error="no transport configured",
            )
        try:
            self._transport.post(
                f"{self._ollama_endpoint}/api/generate",
                {"model": model, "keep_alive": 0},
                timeout_seconds=5.0,
            )
        except Exception as error:  # noqa: BLE001 - an unload failure must never crash the cycle
            return UnloadOutcome(
                model=model, attempted=True, succeeded=False, error=str(error)
            )
        return UnloadOutcome(model=model, attempted=True, succeeded=True)
