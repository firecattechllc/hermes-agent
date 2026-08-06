"""Prose generation: local Gemma via Ollama, with a deterministic fallback.

Governance requirements enforced here:

- Prefer a local, official Gemma model reachable through Ollama; if it is
  unavailable, fall back to a deterministic non-LLM generator that still
  records evidence but skips prose -- never block a run on model
  availability, and never substitute a different model silently.
- Every prompt is redacted before it leaves this process
  (:func:`redaction.redact_text`), and model output is only ever inserted
  as inert prose text into a bounded "narrative" field of a template
  (:mod:`hermes_docs_worker.markdown_gen`) -- never parsed as Markdown
  structure, never executed, and never allowed to add or remove a section,
  table, or wiki-link on its own.
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

from hermes_docs_worker.redaction import redact_text

logger = logging.getLogger("hermes.docs_worker.ollama")

_MAX_PROSE_CHARS = 2_000


class ProseClient(Protocol):
    def generate(self, prompt: str) -> Optional[str]:
        """Return generated prose, or ``None`` to skip prose entirely (the
        caller must render a structured-only section in that case)."""


class DeterministicFallbackClient:
    """Always returns ``None`` -- the documented "skip prose generation"
    behavior. Evidence collection and Markdown generation are unaffected;
    only narrative paragraphs are omitted."""

    def generate(self, prompt: str) -> Optional[str]:
        del prompt
        return None


class OllamaProseClient:
    """Generates short narrative prose from a local Ollama model.

    Fails soft: any connection error, timeout, non-200 response, or
    malformed response returns ``None`` (never raises) so the orchestrator
    always has a safe fallback path without special-casing Ollama
    failures.
    """

    def __init__(self, *, endpoint: str, model: str, timeout_seconds: float) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds

    def is_reachable(self) -> bool:
        try:
            req = urllib_request.Request(f"{self._endpoint}/api/version", method="GET")
            with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
                return response.status == 200
        except (urllib_error.URLError, OSError, ValueError):
            return False

    def generate(self, prompt: str) -> Optional[str]:
        safe_prompt = redact_text(prompt)
        payload = json.dumps(
            {"model": self._model, "prompt": safe_prompt, "stream": False}
        ).encode("utf-8")
        req = urllib_request.Request(
            f"{self._endpoint}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
                if response.status != 200:
                    return None
                body = json.loads(response.read().decode("utf-8"))
        except (urllib_error.URLError, OSError, ValueError, json.JSONDecodeError):
            logger.warning("Ollama prose generation unavailable; falling back to deterministic output")
            return None

        text = str(body.get("response", "")).strip()
        if not text:
            return None
        # Model output is treated as inert text, never as Markdown control
        # syntax or instructions: strip anything that could be mistaken for
        # a directive (front matter fences, HTML comments used for the
        # provenance auto-block) before it is ever inserted into a template.
        text = text.replace("<!--", "").replace("-->", "").replace("---", "-")
        text = redact_text(text)
        return text[:_MAX_PROSE_CHARS]


def resolve_prose_client(
    *, endpoint: str, model: str, timeout_seconds: float
) -> ProseClient:
    """Prefer Ollama if reachable; otherwise the deterministic fallback."""
    candidate = OllamaProseClient(
        endpoint=endpoint, model=model, timeout_seconds=timeout_seconds
    )
    if candidate.is_reachable():
        return candidate
    return DeterministicFallbackClient()
