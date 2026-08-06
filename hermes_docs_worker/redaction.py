"""Secret redaction.

Applied at two mandatory points, per governance requirement: before any
evidence is persisted to disk (:mod:`hermes_docs_worker.evidence`) and
before any text is sent to a local model as a prompt
(:mod:`hermes_docs_worker.ollama_client`). Generated Markdown also passes
through :func:`redact_text` as a final defensive pass in
:mod:`hermes_docs_worker.markdown_gen` even though every input to it should
already be redacted -- belt and suspenders, not a substitute for redacting
at the source.

This module only ever *removes or masks* content; it never raises on input
it cannot classify, because a generator that crashes on unexpected input
would (perversely) make the fail-open case "wrote the secret anyway"
impossible to distinguish from "worker crashed, no evidence produced at
all." Use :func:`contains_secret` at construction boundaries (e.g.
``EvidenceFact`` validators) to fail closed on data that still looks
sensitive *after* redaction.
"""

from __future__ import annotations

import re
from typing import Mapping

_MASK = "[REDACTED]"

# Sensitive material redaction must strip out of any text before it is
# persisted or sent to a model, regardless of surrounding context.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer / Authorization headers.
    re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    # KEY=VALUE / KEY: VALUE style secrets (env files, YAML, JSON-ish).
    re.compile(
        r"(?i)\b((?:[A-Z0-9_]*_)?(?:token|api[_-]?key|secret|password|passwd|"
        r"private[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token))"
        r"\s*[:=]\s*[\"']?([^\s\"'\n]{3,})[\"']?"
    ),
    # PEM private key blocks.
    re.compile(
        r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----"
    ),
    # GitHub tokens.
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    # OpenAI / Anthropic style API keys.
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    # AWS access key IDs.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Generic long base64/hex-looking bearer-shaped tokens in a URL.
    re.compile(r"(?i)([?&](?:token|key|password|secret)=)[^&\s]+"),
)

# Bare secret-shaped identifier names -- used by contains_secret() to fail
# closed even when a value wasn't caught by the value patterns above (e.g.
# a field that is a secret marker with no attached value in this string).
_SENSITIVE_MARKERS = (
    "-----begin private key-----",
    "-----begin rsa private key-----",
    "-----begin openssh private key-----",
    "authorization: bearer",
    "bearer ",
    "api_key",
    "api-key",
    "apikey",
    "private_key",
    "private key",
    "client_secret",
    "password=",
    "passwd=",
    "secret=",
    "token=",
)

# A .env-style KEY name is treated as sensitive by name alone, independent
# of its value shape, so a redacted-looking value doesn't leak the fact
# that (say) HERMES_OMNIROUTE_AUTH_TOKEN was populated with something.
_SENSITIVE_KEY_NAME = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret|auth[_-]?token|credential)"
)

# A full RFC1918 private IPv4 address. The requirement is "never copy a
# full private IP inventory into generated documentation" -- this module
# redacts the host octets of any private IPv4 literal, keeping only the
# network prefix, which is enough for a human to recognize "a Titan-local
# address" without reconstructing the fleet's address map from the docs.
_PRIVATE_IPV4 = re.compile(
    r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3})\b"
)


def redact_text(text: str) -> str:
    """Return ``text`` with every recognized secret pattern masked.

    Loopback (``127.0.0.1``) and link-local addresses are intentionally
    left alone -- they carry no fleet-topology information -- but any
    other private IPv4 literal has its host octets masked.
    """
    if not text:
        return text
    redacted = text
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(_MASK, redacted)
    redacted = _PRIVATE_IPV4.sub(lambda m: _mask_ip(m.group(0)), redacted)
    return redacted


def _mask_ip(ip: str) -> str:
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.x.x"


def contains_secret(text: str) -> bool:
    """True if ``text`` still looks like it carries secret material.

    Used as a fail-closed gate at construction boundaries: callers should
    redact first, then refuse to persist/emit content that still trips
    this check rather than silently shipping it.
    """
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return True
    return any(pattern.search(text) for pattern in _SECRET_VALUE_PATTERNS)


def redact_mapping(values: Mapping[str, str]) -> dict[str, str]:
    """Redact a name/value mapping (e.g. parsed env-file contents).

    A key whose *name* looks sensitive has its value fully masked
    regardless of shape; every other value is passed through
    :func:`redact_text`.
    """
    result: dict[str, str] = {}
    for key, value in values.items():
        if _SENSITIVE_KEY_NAME.search(key):
            result[key] = _MASK
        else:
            result[key] = redact_text(str(value))
    return result


def assert_redacted(text: str, *, field_name: str) -> str:
    """Return ``text`` unchanged, raising ``ValueError`` if it still
    contains secret material. Intended for use inside model validators so a
    record simply cannot be constructed with a live secret in it."""
    if contains_secret(text):
        raise ValueError(f"{field_name} contains forbidden sensitive content")
    return text
