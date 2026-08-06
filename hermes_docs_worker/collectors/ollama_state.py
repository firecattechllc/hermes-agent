"""Ollama version, reachability, and installed model names.

A bounded HTTP GET against the configured Titan-local Ollama endpoint --
never the Mac's. Reachability is a live signal (``Verified``/``Degraded``);
this collector is intentionally separate from
:mod:`hermes_docs_worker.ollama_client` (which exists to *generate prose*,
not to produce evidence) so a prose-generation failure and an
evidence-collection failure are never conflated.
"""

from __future__ import annotations

import json
import time
from typing import Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request

from hermes_docs_worker.evidence import EvidenceFact, make_fact
from hermes_docs_worker.status import StatusValue

SOURCE = "ollama_state"


def collect(
    *, endpoint: str, timeout_seconds: float, now: int | None = None
) -> Tuple[EvidenceFact, ...]:
    observed_at = now if now is not None else int(time.time())
    base = endpoint.rstrip("/")

    version = _get_json(f"{base}/api/version", timeout_seconds)
    if version is None:
        return (
            make_fact(
                category="ollama", label="reachability", status=StatusValue.DEGRADED,
                detail="Ollama endpoint unreachable", source=SOURCE, collected_at=observed_at,
            ),
        )

    facts = [
        EvidenceFact(
            category="ollama", label="reachability", status=StatusValue.VERIFIED,
            detail=f"version={version.get('version', 'unknown')}", source=SOURCE,
            collected_at=observed_at,
        )
    ]

    tags = _get_json(f"{base}/api/tags", timeout_seconds)
    if tags is None:
        facts.append(
            make_fact(
                category="ollama", label="models", status=StatusValue.UNKNOWN,
                detail="could not list installed models", source=SOURCE,
                collected_at=observed_at,
            )
        )
    else:
        names = sorted(
            str(model.get("name", "")) for model in tags.get("models", []) if model.get("name")
        )
        detail = ", ".join(names) if names else "no models installed"
        facts.append(
            make_fact(
                category="ollama", label="models", status=StatusValue.VERIFIED,
                detail=detail[:1024], source=SOURCE, collected_at=observed_at,
            )
        )
    return tuple(facts)


def _get_json(url: str, timeout_seconds: float) -> dict | None:
    try:
        req = urllib_request.Request(url, method="GET")
        with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (urllib_error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
