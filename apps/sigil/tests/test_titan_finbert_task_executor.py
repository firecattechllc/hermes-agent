from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from sigil.integrations.sentiment import (
    GovernedTitanFinBERTTaskExecutor,
    TitanFinBERTTaskError,
)


class FakeInference:
    def __init__(self, prediction: Mapping[str, Any] | None = None) -> None:
        self.prediction = prediction or {
            "positive": 2.0,
            "neutral": 1.0,
            "negative": 1.0,
        }
        self.received_text: str | None = None

    def predict(self, *, text: str) -> Mapping[str, Any]:
        self.received_text = text
        return self.prediction


def valid_task() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": "sigil-finbert-001",
        "task_type": "sigil.finbert.inference",
        "capabilities": {
            "shell": False,
            "sudo": False,
            "external_network": False,
            "trade_execution": False,
            "spending": False,
            "publishing": False,
        },
        "payload": {
            "request": {
                "schema_version": 1,
                "operation": "financial_sentiment",
                "model": "ProsusAI/finbert",
                "document": {
                    "id": "doc-1",
                    "text": "Revenue grew while operating margins improved.",
                    "content_sha256": "abc123",
                    "source_uri": "file:///evidence/doc-1.txt",
                    "instrument_symbol": "FIRE",
                },
                "constraints": {
                    "local_only": True,
                    "allow_model_download": False,
                    "allow_external_api": False,
                    "allow_trade_execution": False,
                },
            }
        },
    }


def test_executes_valid_task_and_correlates_result() -> None:
    inference = FakeInference()
    executor = GovernedTitanFinBERTTaskExecutor(inference=inference)

    response = executor.execute(task=valid_task())

    assert response["task_id"] == "sigil-finbert-001"
    assert response["status"] == "completed"
    assert response["result"]["model"] == "ProsusAI/finbert"
    assert response["result"]["scores"] == {
        "positive": 0.5,
        "neutral": 0.25,
        "negative": 0.25,
    }
    assert response["result"]["confidence"] == 0.5
    assert inference.received_text == "Revenue grew while operating margins improved."


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("task_type", "shell.command"),
        ("task_id", ""),
    ],
)
def test_rejects_invalid_task_identity(field: str, value: Any) -> None:
    task = valid_task()
    task[field] = value

    with pytest.raises(TitanFinBERTTaskError):
        GovernedTitanFinBERTTaskExecutor(inference=FakeInference()).execute(task=task)


@pytest.mark.parametrize(
    "capability",
    [
        "shell",
        "sudo",
        "external_network",
        "trade_execution",
        "spending",
        "publishing",
    ],
)
def test_rejects_forbidden_capability(capability: str) -> None:
    task = valid_task()
    task["capabilities"][capability] = True

    with pytest.raises(TitanFinBERTTaskError):
        GovernedTitanFinBERTTaskExecutor(inference=FakeInference()).execute(task=task)


@pytest.mark.parametrize(
    ("constraint", "value"),
    [
        ("local_only", False),
        ("allow_model_download", True),
        ("allow_external_api", True),
        ("allow_trade_execution", True),
    ],
)
def test_rejects_unsafe_request_constraints(constraint: str, value: bool) -> None:
    task = valid_task()
    task["payload"]["request"]["constraints"][constraint] = value

    with pytest.raises(TitanFinBERTTaskError):
        GovernedTitanFinBERTTaskExecutor(inference=FakeInference()).execute(task=task)


def test_rejects_wrong_model() -> None:
    task = valid_task()
    task["payload"]["request"]["model"] = "unknown/model"

    with pytest.raises(TitanFinBERTTaskError):
        GovernedTitanFinBERTTaskExecutor(inference=FakeInference()).execute(task=task)


def test_rejects_oversized_document() -> None:
    task = valid_task()
    task["payload"]["request"]["document"]["text"] = "x" * 21

    executor = GovernedTitanFinBERTTaskExecutor(
        inference=FakeInference(),
        max_characters=20,
    )

    with pytest.raises(TitanFinBERTTaskError):
        executor.execute(task=task)


@pytest.mark.parametrize(
    "prediction",
    [
        {"positive": 0.5, "neutral": 0.5},
        {"positive": -0.1, "neutral": 0.5, "negative": 0.6},
        {"positive": 0.0, "neutral": 0.0, "negative": 0.0},
    ],
)
def test_rejects_invalid_local_predictions(prediction: Mapping[str, Any]) -> None:
    executor = GovernedTitanFinBERTTaskExecutor(inference=FakeInference(prediction))

    with pytest.raises(TitanFinBERTTaskError):
        executor.execute(task=valid_task())
