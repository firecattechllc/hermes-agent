"""Tests for the Step 40 production-certification tool itself."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "certify_step40.py"
)


def load_certification_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "sigil_step40_certification",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def certification() -> ModuleType:
    return load_certification_module()


def test_extract_pytest_count(
    certification: ModuleType,
) -> None:
    output = """
..................... [100%]
21 passed in 0.07s
"""

    assert certification.extract_pytest_count(output) == 21


def test_extract_vitest_count_without_ansi(
    certification: ModuleType,
) -> None:
    output = """
 Test Files  5 passed (5)
      Tests  29 passed (29)
"""

    assert certification.extract_vitest_count(output) == 29


def test_extract_vitest_count_with_ansi(
    certification: ModuleType,
) -> None:
    output = (
        " Tests  "
        "\x1b[1m\x1b[32m29 passed\x1b[39m\x1b[22m"
        " (29)\n"
    )

    assert certification.extract_vitest_count(output) == 29


def test_checksum_attestation_without_signing_key(
    certification: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "SIGIL_CERTIFICATION_SIGNING_KEY",
        raising=False,
    )

    report = {
        "schema": "sigil.step40.certification.test",
        "result": "PASS",
    }
    report_bytes = certification.canonical_json(report)
    expected = hashlib.sha256(report_bytes).hexdigest()

    attestation = certification.create_signature(report_bytes)

    assert attestation == {
        "report_sha256": expected,
        "signature": expected,
        "signature_type": "SHA256-CHECKSUM",
    }


def test_hmac_attestation_with_signing_key(
    certification: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing_key = "step40-test-signing-key"
    monkeypatch.setenv(
        "SIGIL_CERTIFICATION_SIGNING_KEY",
        signing_key,
    )

    report = {
        "schema": "sigil.step40.certification.test",
        "result": "PASS",
    }
    report_bytes = certification.canonical_json(report)

    expected_digest = hashlib.sha256(report_bytes).hexdigest()
    expected_signature = hmac.new(
        signing_key.encode("utf-8"),
        report_bytes,
        hashlib.sha256,
    ).hexdigest()

    attestation = certification.create_signature(report_bytes)

    assert attestation == {
        "report_sha256": expected_digest,
        "signature": expected_signature,
        "signature_type": "HMAC-SHA256",
    }


def test_canonical_json_is_stable(
    certification: ModuleType,
) -> None:
    first = {
        "result": "PASS",
        "counts": {
            "desktop": 29,
            "backend": 899,
        },
    }
    second = {
        "counts": {
            "backend": 899,
            "desktop": 29,
        },
        "result": "PASS",
    }

    first_bytes = certification.canonical_json(first)
    second_bytes = certification.canonical_json(second)

    assert first_bytes == second_bytes
    assert json.loads(first_bytes) == first
