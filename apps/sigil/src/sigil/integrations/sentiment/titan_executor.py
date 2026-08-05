from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class LocalFinBERTInference(Protocol):
    """Injected local-only FinBERT runtime boundary."""

    def predict(self, *, text: str) -> Mapping[str, Any]:
        """Return FinBERT scores without network or download side effects."""


class TitanFinBERTTaskError(RuntimeError):
    """Raised when a Hermes task cannot be executed safely."""


@dataclass(frozen=True, slots=True)
class GovernedTitanFinBERTTaskExecutor:
    """Execute one tightly scoped Sigil FinBERT task on Titan."""

    inference: LocalFinBERTInference
    model_name: str = "ProsusAI/finbert"
    model_version: str = "titan-certified-local"
    max_characters: int = 20_000

    def execute(self, *, task: Mapping[str, Any]) -> Mapping[str, Any]:
        task_id = self._required_nonempty_string(task, "task_id")
        self._validate_task_envelope(task)

        payload = task.get("payload")
        if not isinstance(payload, Mapping):
            raise TitanFinBERTTaskError("task payload must be an object")

        request = payload.get("request")
        if not isinstance(request, Mapping):
            raise TitanFinBERTTaskError("task payload is missing FinBERT request")

        self._validate_request(request)
        document = request["document"]
        assert isinstance(document, Mapping)
        text = document["text"]
        assert isinstance(text, str)

        prediction = self.inference.predict(text=text)
        result = self._normalize_prediction(prediction)

        return {
            "schema_version": 1,
            "task_id": task_id,
            "status": "completed",
            "result": {
                "schema_version": 1,
                "model": self.model_name,
                "model_version": self.model_version,
                **result,
            },
        }

    def _validate_task_envelope(self, task: Mapping[str, Any]) -> None:
        if task.get("schema_version") != 1:
            raise TitanFinBERTTaskError("unsupported Hermes task schema")
        if task.get("task_type") != "sigil.finbert.inference":
            raise TitanFinBERTTaskError("unsupported Hermes task type")

        capabilities = task.get("capabilities")
        if not isinstance(capabilities, Mapping):
            raise TitanFinBERTTaskError("task capabilities must be an object")

        denied = {
            "shell",
            "sudo",
            "external_network",
            "trade_execution",
            "spending",
            "publishing",
        }
        granted = {name for name, enabled in capabilities.items() if enabled is True}
        forbidden = sorted(denied & granted)
        if forbidden:
            raise TitanFinBERTTaskError(
                f"task grants forbidden capabilities: {', '.join(forbidden)}"
            )

    def _validate_request(self, request: Mapping[str, Any]) -> None:
        if request.get("schema_version") != 1:
            raise TitanFinBERTTaskError("unsupported FinBERT request schema")
        if request.get("operation") != "financial_sentiment":
            raise TitanFinBERTTaskError("unsupported FinBERT operation")
        if request.get("model") != self.model_name:
            raise TitanFinBERTTaskError("unexpected FinBERT model identity")

        constraints = request.get("constraints")
        if not isinstance(constraints, Mapping):
            raise TitanFinBERTTaskError("FinBERT constraints must be an object")

        required_constraints = {
            "local_only": True,
            "allow_model_download": False,
            "allow_external_api": False,
            "allow_trade_execution": False,
        }
        for name, expected in required_constraints.items():
            if constraints.get(name) is not expected:
                raise TitanFinBERTTaskError(f"unsafe FinBERT constraint: {name}")

        document = request.get("document")
        if not isinstance(document, Mapping):
            raise TitanFinBERTTaskError("FinBERT document must be an object")

        self._required_nonempty_string(document, "id")
        text = self._required_nonempty_string(document, "text")
        self._required_nonempty_string(document, "content_sha256")
        self._required_nonempty_string(document, "source_uri")

        if len(text) > self.max_characters:
            raise TitanFinBERTTaskError(
                f"document exceeds governed limit of {self.max_characters} characters"
            )

    def _normalize_prediction(self, prediction: Mapping[str, Any]) -> dict[str, Any]:
        scores_value = prediction.get("scores")
        if not isinstance(scores_value, Mapping):
            scores_value = prediction

        scores: dict[str, float] = {}
        for label in ("positive", "neutral", "negative"):
            value = scores_value.get(label)
            if not isinstance(value, (int, float)):
                raise TitanFinBERTTaskError(
                    f"local inference is missing numeric {label} score"
                )
            score = float(value)
            if score < 0:
                raise TitanFinBERTTaskError(
                    f"local inference returned negative {label} score"
                )
            scores[label] = score

        total = sum(scores.values())
        if total <= 0:
            raise TitanFinBERTTaskError("local inference returned empty probability mass")

        normalized = {label: value / total for label, value in scores.items()}
        label = max(normalized, key=normalized.__getitem__)

        return {
            "scores": normalized,
            "confidence": normalized[label],
            "rationale": (
                "Confidence is the highest normalized probability produced by the "
                "governed local Titan FinBERT runtime."
            ),
        }

    @staticmethod
    def _required_nonempty_string(value: Mapping[str, Any], field: str) -> str:
        candidate = value.get(field)
        if not isinstance(candidate, str) or not candidate.strip():
            raise TitanFinBERTTaskError(f"{field} must be a non-empty string")
        return candidate
