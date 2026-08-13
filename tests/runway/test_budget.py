from __future__ import annotations

import pytest

from runway.budget import BudgetLedger, BudgetPolicy, BudgetRejected


def test_task_cap(tmp_path):
    ledger = BudgetLedger.open(
        BudgetPolicy(per_task_max_micros=100, per_hour_max_micros=10_000,
                     per_day_max_micros=10_000, per_month_max_micros=10_000),
        path=tmp_path / "b.db",
    )
    with pytest.raises(BudgetRejected) as excinfo:
        ledger.spend(amount_micros=101, provider_id="p", model_key="m@p", task_type="t", timestamp=0)
    assert excinfo.value.reason == "task_cap_exceeded"
    ledger.spend(amount_micros=100, provider_id="p", model_key="m@p", task_type="t", timestamp=0)
    assert ledger.spent_today_micros(timestamp=0) == 100


def test_daily_cap(tmp_path):
    ledger = BudgetLedger.open(
        BudgetPolicy(per_task_max_micros=1_000, per_hour_max_micros=10_000,
                     per_day_max_micros=150, per_month_max_micros=10_000),
        path=tmp_path / "b.db",
    )
    ledger.spend(amount_micros=100, provider_id="p", model_key="m@p", task_type="t", timestamp=0)
    with pytest.raises(BudgetRejected) as excinfo:
        ledger.spend(amount_micros=60, provider_id="p", model_key="m@p", task_type="t", timestamp=1)
    assert excinfo.value.reason == "daily_cap_exceeded"


def test_monthly_cap(tmp_path):
    ledger = BudgetLedger.open(
        BudgetPolicy(per_task_max_micros=1_000, per_hour_max_micros=10_000,
                     per_day_max_micros=10_000, per_month_max_micros=150),
        path=tmp_path / "b.db",
    )
    ledger.spend(amount_micros=100, provider_id="p", model_key="m@p", task_type="t", timestamp=0)
    with pytest.raises(BudgetRejected) as excinfo:
        ledger.spend(amount_micros=60, provider_id="p", model_key="m@p", task_type="t", timestamp=1)
    assert excinfo.value.reason == "monthly_cap_exceeded"


def test_provider_cap(tmp_path):
    policy = BudgetPolicy(
        per_task_max_micros=1_000, per_hour_max_micros=10_000, per_day_max_micros=10_000,
        per_month_max_micros=10_000, provider_daily_caps_micros={"p1": 100},
    )
    ledger = BudgetLedger.open(policy, path=tmp_path / "b.db")
    ledger.spend(amount_micros=80, provider_id="p1", model_key="m@p1", task_type="t", timestamp=0)
    with pytest.raises(BudgetRejected) as excinfo:
        ledger.spend(amount_micros=30, provider_id="p1", model_key="m@p1", task_type="t", timestamp=1)
    assert excinfo.value.reason == "provider_cap_exceeded"
    # a different provider is unaffected by p1's cap
    ledger.spend(amount_micros=30, provider_id="p2", model_key="m@p2", task_type="t", timestamp=1)


def test_premium_reserve(tmp_path):
    policy = BudgetPolicy(
        per_task_max_micros=1_000, per_hour_max_micros=10_000, per_day_max_micros=10_000,
        per_month_max_micros=1_000, emergency_reserve_micros=200, premium_escalation_ceiling_micros=200,
    )
    ledger = BudgetLedger.open(policy, path=tmp_path / "b.db")
    ledger.spend(amount_micros=800, provider_id="p", model_key="m@p", task_type="t", timestamp=0)
    # normal spend cannot dip into the 200-micros reserve
    with pytest.raises(BudgetRejected) as excinfo:
        ledger.spend(amount_micros=150, provider_id="p", model_key="m@p", task_type="t", timestamp=1)
    assert excinfo.value.reason == "monthly_cap_exceeded"
    # a premium escalation may draw on the reserve up to the escalation ceiling
    ledger.spend(
        amount_micros=150, provider_id="p", model_key="m@p", task_type="t", timestamp=2,
        is_premium_escalation=True,
    )
    assert ledger.premium_escalation_count(timestamp=2) == 1


def test_rejected_spend_does_not_mutate_ledger(tmp_path):
    ledger = BudgetLedger.open(
        BudgetPolicy(per_task_max_micros=10, per_hour_max_micros=10, per_day_max_micros=10, per_month_max_micros=10),
        path=tmp_path / "b.db",
    )
    with pytest.raises(BudgetRejected):
        ledger.spend(amount_micros=11, provider_id="p", model_key="m@p", task_type="t", timestamp=0)
    assert ledger.spent_today_micros(timestamp=0) == 0
    assert ledger.spent_month_to_date_micros(timestamp=0) == 0
