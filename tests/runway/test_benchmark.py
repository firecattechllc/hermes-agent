from __future__ import annotations

from runway.benchmark import run_benchmark


def test_benchmark_is_deterministic_and_shows_savings():
    first = run_benchmark(num_tasks=150, seed=7)
    second = run_benchmark(num_tasks=150, seed=7)
    assert first == second  # same seed -> byte-identical synthetic run

    assert first.all_premium.successes > 0
    assert first.runway.successes > 0
    # Runway should be materially cheaper per successful task than always
    # paying for the premium model, in this fixture world.
    assert first.runway.cost_per_success_micros < first.all_premium.cost_per_success_micros
    assert first.savings_percent is not None and first.savings_percent > 0
