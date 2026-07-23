"""FastAPI adapter for the private Titan Hermes-link service."""

from __future__ import annotations

import hmac
import json
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .models import HermesLinkEnvelope, MessageType
from .service import HermesLinkService, LinkPolicyError

TokenVerifier = Callable[[str], bool]


def static_token_verifier(expected_token: str) -> TokenVerifier:
    """Build a verifier from an already-resolved secret; the token is never logged."""
    if not expected_token:
        raise ValueError("authentication token must not be empty")
    return lambda supplied: hmac.compare_digest(supplied, expected_token)


def create_app(service: HermesLinkService, *, token_verifier: TokenVerifier) -> FastAPI:
    app = FastAPI(
        title="Titan Hermes Link",
        version=service.service_version,
        docs_url=None,
        redoc_url=None,
    )

    def authenticate(authorization: Optional[str] = Header(default=None)) -> None:
        if (
            not authorization
            or not authorization.startswith("Bearer ")
            or not token_verifier(authorization[7:])
        ):
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "authentication_failed",
                    "message": "valid application authentication is required",
                },
            )

    @app.exception_handler(LinkPolicyError)
    async def policy_error(_request: Request, exc: LinkPolicyError) -> JSONResponse:
        return JSONResponse(
            status_code=403
            if exc.code in {"invalid_node_identity", "approval_required"}
            else 422,
            content={
                "error": {"code": exc.code, "message": str(exc), "retryable": False}
            },
        )

    async def parse_envelope(request: Request) -> HermesLinkEnvelope:
        body = await request.body()
        if len(body) > service.maximum_payload_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "payload_too_large",
                    "message": "request exceeds configured size",
                },
            )
        try:
            return HermesLinkEnvelope.model_validate(json.loads(body))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_envelope",
                    "message": "request is not a valid Hermes-link envelope",
                },
            ) from exc

    @app.get("/status", dependencies=[Depends(authenticate)])
    def status():
        return service.status()

    @app.get("/queue", dependencies=[Depends(authenticate)])
    def queue():
        return {"messages": service.list_queue()}

    async def receive(request: Request, allowed: set[MessageType]):
        envelope = await parse_envelope(request)
        return service.receive(envelope, allowed_types=allowed)

    @app.post("/chat", dependencies=[Depends(authenticate)])
    async def chat(request: Request):
        return await receive(request, {MessageType.CHAT})

    @app.post("/task", dependencies=[Depends(authenticate)])
    async def task(request: Request):
        return await receive(request, {MessageType.TASK_REQUEST})

    @app.post("/lesson", dependencies=[Depends(authenticate)])
    async def lesson(request: Request):
        return await receive(request, {MessageType.LESSON_PACKAGE})

    @app.get("/reports/latest", dependencies=[Depends(authenticate)])
    def latest_report():
        report = service.latest_report()
        if report is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "report_not_found",
                    "message": "no report is available",
                },
            )
        return report

    return app
