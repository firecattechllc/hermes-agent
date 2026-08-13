from __future__ import annotations

from runway.pricing import CostModel, UsageEstimate, cache_savings_micros, estimate_cost_micros


def _base_cost(**overrides) -> CostModel:
    fields = dict(snapshot_at=0, input_micros_per_million=2_000_000, output_micros_per_million=6_000_000)
    fields.update(overrides)
    return CostModel(**fields)


def test_deterministic_token_calculations():
    cost = _base_cost()
    usage = UsageEstimate(input_tokens=1_000, output_tokens=500)
    # input: 1000 * 2_000_000 // 1_000_000 = 2000 ; output: 500 * 6_000_000 // 1_000_000 = 3000
    assert estimate_cost_micros(cost, usage) == 5000


def test_cache_calculations():
    cost = _base_cost(cached_input_micros_per_million=200_000, cache_write_micros_per_million=100_000)
    usage = UsageEstimate(
        input_tokens=1_000, cached_input_tokens=800, cache_write_tokens=800, output_tokens=0,
    )
    # uncached input: 200 tokens * 2_000_000 // 1e6 = 400
    # cached input: 800 * 200_000 // 1e6 = 160
    # cache write: 800 * 100_000 // 1e6 = 80
    assert estimate_cost_micros(cost, usage) == 400 + 160 + 80


def test_cache_savings_is_positive_when_cache_is_cheaper():
    cost = _base_cost(cached_input_micros_per_million=200_000)
    usage = UsageEstimate(input_tokens=1_000, cached_input_tokens=1_000, output_tokens=0)
    savings = cache_savings_micros(cost, usage)
    # without cache: 1000 * 2_000_000 // 1e6 = 2000 ; with cache: 1000 * 200_000 // 1e6 = 200
    assert savings == 1800


def test_fixed_request_fee_is_additive():
    cost = _base_cost(fixed_request_fee_micros=750)
    usage = UsageEstimate(input_tokens=0, output_tokens=0)
    assert estimate_cost_micros(cost, usage) == 750


def test_free_quota_reduces_cost_floored_at_zero():
    cost = _base_cost(free_quota_micros=10_000)
    usage = UsageEstimate(input_tokens=1_000, output_tokens=500)  # gross = 5000
    assert estimate_cost_micros(cost, usage) == 0  # 5000 - 10000 floors at 0, never negative


def test_platform_fee_is_additive():
    cost = _base_cost(platform_fee_micros=333)
    usage = UsageEstimate(input_tokens=0, output_tokens=0)
    assert estimate_cost_micros(cost, usage) == 333


def test_currency_field_is_preserved_and_distinguishes_pricing_snapshots():
    usd = _base_cost(currency="USD")
    eur = _base_cost(currency="EUR")
    assert usd.currency == "USD"
    assert eur.currency == "EUR"
    # Runway never silently sums costs across currencies — CostModel carries
    # the currency alongside every price so callers can't accidentally merge
    # a USD snapshot with a EUR one without noticing.
    assert usd.model_dump()["currency"] != eur.model_dump()["currency"]


def test_no_float_drift_across_many_repeated_calculations():
    cost = _base_cost(cached_input_micros_per_million=333_333)
    usage = UsageEstimate(input_tokens=7, cached_input_tokens=3, output_tokens=11)
    results = {estimate_cost_micros(cost, usage) for _ in range(10_000)}
    assert len(results) == 1
    value = results.pop()
    assert isinstance(value, int)
