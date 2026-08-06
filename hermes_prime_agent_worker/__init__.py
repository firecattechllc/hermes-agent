"""Governed Hermes worker adapter for the third-party Prime Agent CLI on Titan.

Prime Agent (https://github.com/PrimeIntellect-ai/prime-agent) is a worker
only. Hermes remains the authority for admission, budgets, approvals,
evidence, and shutdown -- this package never lets Prime Agent decide those
things for itself. See docs/hermes-prime-agent-worker/architecture.md.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
