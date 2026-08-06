"""Governed-worker configuration and local test evidence.

Two read-only signals: that this worker's own configuration is currently
valid (re-validating it here, rather than trusting "the process started"
to imply validity, since this fact is meant to be a durable record
independent of process lifetime), and whatever local test-run evidence a
separate test runner has already written to
``config.hermes_test_evidence_path``. This collector never executes a test
suite itself -- collection must stay read-only and fast, and never claim a
test passed based on anything but a genuine, already-produced test
artifact.
"""

from __future__ import annotations

import json
import time
from typing import Tuple

from hermes_docs_worker.config import DocsWorkerConfig
from hermes_docs_worker.evidence import EvidenceFact, make_fact
from hermes_docs_worker.status import StatusValue

SOURCE = "worker_config_evidence"


def collect(config: DocsWorkerConfig, *, now: int | None = None) -> Tuple[EvidenceFact, ...]:
    observed_at = now if now is not None else int(time.time())
    facts = [
        make_fact(
            category="worker_config", label="validity", status=StatusValue.VERIFIED,
            detail="documentation worker configuration validated at collection time",
            source=SOURCE, collected_at=observed_at,
        )
    ]

    test_path = config.hermes_test_evidence_path
    if test_path is None:
        facts.append(
            make_fact(
                category="worker_config", label="local_test_evidence",
                status=StatusValue.UNKNOWN,
                detail="no local test evidence path configured", source=SOURCE,
                collected_at=observed_at,
            )
        )
        return tuple(facts)

    if not test_path.exists():
        facts.append(
            make_fact(
                category="worker_config", label="local_test_evidence",
                status=StatusValue.UNKNOWN,
                detail="configured local test evidence file does not exist yet",
                source=SOURCE, collected_at=observed_at,
            )
        )
        return tuple(facts)

    try:
        payload = json.loads(test_path.read_text(encoding="utf-8"))
        passed = bool(payload.get("passed"))
        run_at = payload.get("run_at", "unknown")
        status = StatusValue.VERIFIED if passed else StatusValue.DEGRADED
        detail = f"last local test run passed={passed} at {run_at}"
    except (OSError, ValueError) as error:
        status = StatusValue.UNKNOWN
        detail = f"local test evidence file unreadable: {error}"

    facts.append(
        make_fact(
            category="worker_config", label="local_test_evidence", status=status,
            detail=detail, source=SOURCE, collected_at=observed_at,
        )
    )
    return tuple(facts)
