"""Narrow governed transport from Sigil into the Hermes Anthropic runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class ClaudeTransportFailure(str, Enum):
    UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    MALFORMED = "malformed_output"


class ClaudeTransportError(RuntimeError):
    """Typed fail-closed Claude transport error."""

    def __init__(
        self,
        classification: ClaudeTransportFailure,
        message: str,
    ) -> None:
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True, slots=True)
class ClaudeTransportResult:
    """Credential-free, tool-free result returned across the Sigil boundary."""

    content: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


CredentialResolver = Callable[[], str | None]


class HermesClaudeTransport:
    """Invoke Claude through Hermes without exposing broad agent authority."""

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver,
    ) -> None:
        self._credential_resolver = credential_resolver

    def invoke(
        self,
        *,
        model: str,
        prompt: str,
        timeout_ms: int,
        max_output_tokens: int,
    ) -> ClaudeTransportResult:
        if not model.strip():
            raise ClaudeTransportError(
                ClaudeTransportFailure.MALFORMED,
                "Claude runtime model must not be empty.",
            )
        if not prompt.strip():
            raise ClaudeTransportError(
                ClaudeTransportFailure.MALFORMED,
                "Claude prompt must not be empty.",
            )
        if timeout_ms < 1:
            raise ClaudeTransportError(
                ClaudeTransportFailure.TIMEOUT,
                "Claude timeout must be positive.",
            )
        if max_output_tokens < 1:
            raise ClaudeTransportError(
                ClaudeTransportFailure.MALFORMED,
                "Claude max output tokens must be positive.",
            )

        client = None
        try:
            credential = self._credential_resolver()
            if not isinstance(credential, str) or not credential.strip():
                raise ClaudeTransportError(
                    ClaudeTransportFailure.UNAVAILABLE,
                    "Claude credentials are unavailable.",
                )

            from agent.anthropic_adapter import (
                _is_oauth_token,
                build_anthropic_client,
                build_anthropic_kwargs,
                create_anthropic_message,
            )
            from agent.transports import get_transport

            client = build_anthropic_client(
                credential,
                timeout=timeout_ms / 1_000,
            )

            api_kwargs = build_anthropic_kwargs(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                tools=None,
                max_tokens=max_output_tokens,
                reasoning_config=None,
                tool_choice="none",
                is_oauth=_is_oauth_token(credential),
            )

            response = create_anthropic_message(
                client,
                api_kwargs,
                log_prefix="Sigil governed Claude: ",
            )

            normalized = get_transport("anthropic_messages").normalize_response(
                response,
                strip_tool_prefix=_is_oauth_token(credential),
            )

            if normalized.tool_calls:
                raise ClaudeTransportError(
                    ClaudeTransportFailure.MALFORMED,
                    "Claude returned prohibited tool calls.",
                )

            content = normalized.content
            if not isinstance(content, str) or not content.strip():
                raise ClaudeTransportError(
                    ClaudeTransportFailure.MALFORMED,
                    "Claude returned empty or malformed content.",
                )

            finish_reason = normalized.finish_reason
            if not isinstance(finish_reason, str) or not finish_reason:
                raise ClaudeTransportError(
                    ClaudeTransportFailure.MALFORMED,
                    "Claude returned an invalid finish reason.",
                )

            usage = getattr(response, "usage", None)
            input_tokens = _non_negative_int(
                getattr(usage, "input_tokens", 0) if usage is not None else 0
            )
            output_tokens = _non_negative_int(
                getattr(usage, "output_tokens", 0) if usage is not None else 0
            )
            reported_total = _non_negative_int(
                getattr(usage, "total_tokens", 0) if usage is not None else 0
            )
            total_tokens = reported_total or input_tokens + output_tokens

            return ClaudeTransportResult(
                content=content.strip(),
                finish_reason=finish_reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )
        except ClaudeTransportError:
            raise
        except TimeoutError as exc:
            raise ClaudeTransportError(
                ClaudeTransportFailure.TIMEOUT,
                "Claude transport timed out.",
            ) from exc
        except (ImportError, OSError, ConnectionError) as exc:
            raise ClaudeTransportError(
                ClaudeTransportFailure.UNAVAILABLE,
                "Claude transport is unavailable.",
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ClaudeTransportError(
                ClaudeTransportFailure.MALFORMED,
                "Claude transport returned malformed data.",
            ) from exc
        except Exception as exc:
            # Unknown SDK and HTTP failures remain fail-closed and do not
            # expose provider exception text, credentials, or response bodies.
            raise ClaudeTransportError(
                ClaudeTransportFailure.UNAVAILABLE,
                "Claude transport failed safely.",
            ) from exc
        finally:
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    close()


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("token usage must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("token usage must be an integer") from exc
    if normalized < 0:
        raise ValueError("token usage must not be negative")
    return normalized
