"""Signed HTTP adapter for the existing governed Sigil remote-task contracts."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import TypeAdapter
from starlette.concurrency import run_in_threadpool

from sigil.ai.fleet import (
    GovernedRemoteResult,
    GovernedRemoteTask,
    RemoteTaskState,
    canonical_digest,
)

from .security import (
    CredentialRegistry,
    HermesLinkAuthenticationError,
    SignedRequestAuthenticator,
    build_signed_request,
    resolve_secret,
)


_TASK = TypeAdapter(GovernedRemoteTask)
_RESULT = TypeAdapter(GovernedRemoteResult)


def _jsonable(value: object) -> object:
    return json.loads(json.dumps(asdict(value), default=lambda item: item.value))


class DurableRemoteTaskStore:
    """Atomic task projection containing only bounded task/result contracts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or self.path.stat().st_mode & 0o077:
            raise ValueError("remote task state permissions are unsafe")
        value = json.loads(self.path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("remote task state is corrupt")
        return value

    def _write(self, values: Mapping[str, dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_suffix(".json.new")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("remote task state write made no progress")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def create(self, task: GovernedRemoteTask) -> None:
        with self._lock:
            values = self._read()
            if task.remote_task_id in values:
                raise ValueError("duplicate remote task identity")
            values[task.remote_task_id] = {
                "task": _jsonable(task),
                "state": RemoteTaskState.ACKNOWLEDGED.value,
                "result": None,
            }
            self._write(values)

    def complete(self, result: GovernedRemoteResult) -> None:
        with self._lock:
            values = self._read()
            current = values.get(result.remote_task_id)
            if current is None:
                raise ValueError("remote task identity is unknown")
            if current["state"] in {
                RemoteTaskState.SUCCEEDED.value,
                RemoteTaskState.FAILED.value,
                RemoteTaskState.CANCELLED.value,
            }:
                raise ValueError("terminal remote task is immutable")
            current["state"] = result.state.value
            current["result"] = _jsonable(result)
            self._write(values)

    def cancel(self, task_id: str, token_id: str) -> RemoteTaskState:
        with self._lock:
            values = self._read()
            current = values.get(task_id)
            if current is None:
                raise ValueError("remote task identity is unknown")
            task = _TASK.validate_python(current["task"])
            if task.cancellation_token_id != token_id:
                raise ValueError("cancellation identity is invalid")
            state = RemoteTaskState(str(current["state"]))
            if state in {
                RemoteTaskState.SUCCEEDED,
                RemoteTaskState.FAILED,
                RemoteTaskState.CANCELLED,
            }:
                return state
            current["state"] = RemoteTaskState.CANCELLATION_REQUESTED.value
            self._write(values)
            return RemoteTaskState.CANCELLATION_REQUESTED

    def state(self, task_id: str) -> RemoteTaskState:
        with self._lock:
            current = self._read().get(task_id)
            if current is None:
                raise ValueError("remote task identity is unknown")
            return RemoteTaskState(str(current["state"]))

    def result(self, task_id: str) -> GovernedRemoteResult | None:
        with self._lock:
            current = self._read().get(task_id)
            if current is None or current["result"] is None:
                return None
            return _RESULT.validate_python(current["result"])


class GovernedRemoteWorkerService:
    """One allowlisted digest-only worker; no raw input or executable surface."""

    def __init__(
        self,
        node_id: str,
        store: DurableRemoteTaskStore,
        handlers: Mapping[str, Callable[[tuple[str, ...]], Mapping[str, str]]],
        *,
        maximum_timeout_ms: int,
        maximum_output_chars: int,
        maximum_concurrency: int = 1,
    ) -> None:
        self.node_id = node_id
        self.store = store
        self.handlers = dict(handlers)
        self.maximum_timeout_ms = maximum_timeout_ms
        self.maximum_output_chars = maximum_output_chars
        self._concurrency = threading.BoundedSemaphore(maximum_concurrency)

    def dispatch(
        self, task: GovernedRemoteTask, *, completed_at: str
    ) -> GovernedRemoteResult:
        if not self._concurrency.acquire(blocking=False):
            raise ValueError("remote worker concurrency limit reached")
        try:
            return self._execute(task, completed_at=completed_at)
        finally:
            self._concurrency.release()

    def _execute(
        self, task: GovernedRemoteTask, *, completed_at: str
    ) -> GovernedRemoteResult:
        if task.node_id != self.node_id:
            raise ValueError("remote task target identity is invalid")
        if task.task_type.value not in self.handlers:
            raise ValueError("remote task type is not allowlisted")
        if task.timeout_ms > self.maximum_timeout_ms:
            raise ValueError("remote task timeout exceeds the service bound")
        self.store.create(task)
        if task.task_type.value == "deterministic_calculation":
            deadline = time.monotonic() + min(task.timeout_ms / 1000, 2.0)
            while time.monotonic() < deadline:
                if (
                    self.store.state(task.remote_task_id)
                    == RemoteTaskState.CANCELLATION_REQUESTED
                ):
                    result = GovernedRemoteResult(
                        remote_result_id=f"remote-result-{canonical_digest({'task': task.remote_task_id, 'state': 'cancelled'})[:64]}",
                        remote_task_id=task.remote_task_id,
                        node_id=task.node_id,
                        provider_id="openworker",
                        model_id="deterministic-cancellation-probe-v1",
                        started_at=task.requested_at,
                        ended_at=completed_at,
                        state=RemoteTaskState.CANCELLED,
                        structured_payload=(),
                        input_digest=f"sha256:{canonical_digest(task.input_digests)}",
                        output_digest=None,
                        resource_usage=(
                            ("network", "disabled"),
                            ("worker_concurrency", "1"),
                        ),
                        cancellation_state="acknowledged",
                        limitations=(
                            "Cancellation probe performs no external work or mutation.",
                        ),
                        evidence_identity=f"sha256:{canonical_digest({'task': task.remote_task_id, 'state': 'cancelled'})}",
                        failure_classification="cancelled",
                    )
                    self.store.complete(result)
                    return result
                time.sleep(0.02)
        payload = tuple(
            sorted(self.handlers[task.task_type.value](task.input_digests).items())
        )
        if len(str(payload)) > min(
            task.maximum_output_chars, self.maximum_output_chars
        ):
            raise ValueError("remote task output exceeds the service bound")
        output_digest = f"sha256:{canonical_digest(payload)}"
        result = GovernedRemoteResult(
            remote_result_id=f"remote-result-{canonical_digest({'task': task.remote_task_id, 'output': output_digest})[:64]}",
            remote_task_id=task.remote_task_id,
            node_id=task.node_id,
            provider_id="openworker",
            model_id="deterministic-digest-v1",
            started_at=task.requested_at,
            ended_at=completed_at,
            state=RemoteTaskState.SUCCEEDED,
            structured_payload=payload,
            input_digest=f"sha256:{canonical_digest(task.input_digests)}",
            output_digest=output_digest,
            resource_usage=(("network", "disabled"), ("worker_concurrency", "1")),
            cancellation_state="not_requested",
            limitations=(
                "Digest-only deterministic worker; no raw inputs or external authority.",
            ),
            evidence_identity=f"sha256:{canonical_digest({'task': task.remote_task_id, 'result': output_digest})}",
        )
        self.store.complete(result)
        return result


def digest_evidence_handler(input_digests: tuple[str, ...]) -> Mapping[str, str]:
    return {"evidence_digest": f"sha256:{canonical_digest(input_digests)}"}


def attach_remote_worker_routes(
    app: FastAPI,
    service: GovernedRemoteWorkerService,
    authenticator: SignedRequestAuthenticator,
) -> None:
    async def authenticate(request: Request) -> None:
        try:
            authenticator.verify(
                request.method, request.url.path, request.headers, await request.body()
            )
        except HermesLinkAuthenticationError as exc:
            raise HTTPException(
                status_code=401, detail={"code": exc.code, "message": str(exc)}
            ) from exc

    @app.post("/fleet/tasks", dependencies=[Depends(authenticate)])
    async def dispatch(request: Request):
        try:
            task = _TASK.validate_json(await request.body())
            result = await run_in_threadpool(
                service.dispatch, task, completed_at=task.requested_at
            )
            return _jsonable(result)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "task_rejected", "message": str(exc)},
            ) from exc

    @app.get("/fleet/tasks/{task_id}", dependencies=[Depends(authenticate)])
    def query(task_id: str):
        result = service.store.result(task_id)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "result_unavailable",
                    "message": "task result is unavailable",
                },
            )
        return _jsonable(result)

    @app.post("/fleet/tasks/{task_id}/cancel", dependencies=[Depends(authenticate)])
    async def cancel(task_id: str, request: Request):
        try:
            value = json.loads(await request.body())
            if set(value) != {"cancellation_token_id"}:
                raise ValueError("exact cancellation identity is required")
            state = service.store.cancel(task_id, str(value["cancellation_token_id"]))
            return {"remote_task_id": task_id, "state": state.value}
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "cancellation_rejected", "message": str(exc)},
            ) from exc


class HTTPResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


class HTTPTransport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> HTTPResponse: ...


class SignedFleetHTTPAdapter:
    """Mac-side Phase 9 adapter over signed Hermes Link HTTP."""

    def __init__(
        self,
        base_url: str,
        credential_registry: CredentialRegistry,
        *,
        coordinator_node_id: str,
        target_node_id: str,
        transport: HTTPTransport | None = None,
        connect_timeout: float = 2.0,
        read_timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.registry = credential_registry
        self.coordinator_node_id = coordinator_node_id
        self.target_node_id = target_node_id
        self.transport = transport or httpx.Client(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout)
        )

    def _request(
        self, method: str, path: str, value: object | None = None
    ) -> HTTPResponse:
        body = (
            b""
            if value is None
            else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        )
        now = int(time.time())
        credential = self.registry.active_for(
            self.coordinator_node_id, self.target_node_id, now=now
        )
        signed = build_signed_request(method, path, body, credential, now=now)
        headers = signed.headers(resolve_secret(credential.secret_reference))
        if body:
            headers["Content-Type"] = "application/json"
        try:
            response = self.transport.request(
                method, self.base_url + path, headers=headers, content=body
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError("signed fleet request timed out") from exc
        except (httpx.TransportError, OSError) as exc:
            raise ConnectionError("signed fleet node is unreachable") from exc
        if response.status_code >= 400:
            value = response.json()
            detail = value.get("detail", {}) if isinstance(value, dict) else {}
            code = (
                detail.get("code", "request_rejected")
                if isinstance(detail, dict)
                else "request_rejected"
            )
            raise ValueError(f"signed fleet node rejected the bounded request: {code}")
        return response

    def dispatch(self, task: GovernedRemoteTask) -> GovernedRemoteResult:
        response = self._request("POST", "/fleet/tasks", _jsonable(task))
        return _RESULT.validate_python(response.json())

    def cancel(self, task_id: str, cancellation_token_id: str) -> RemoteTaskState:
        response = self._request(
            "POST",
            f"/fleet/tasks/{task_id}/cancel",
            {"cancellation_token_id": cancellation_token_id},
        )
        value = response.json()
        if value.get("remote_task_id") != task_id:
            raise ValueError("signed cancellation response identity is invalid")
        return RemoteTaskState(value["state"])

    def query(self, task_id: str) -> GovernedRemoteResult | None:
        try:
            response = self._request("GET", f"/fleet/tasks/{task_id}")
        except ValueError:
            return None
        return _RESULT.validate_python(response.json())
