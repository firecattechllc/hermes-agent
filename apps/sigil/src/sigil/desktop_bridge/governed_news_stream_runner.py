"""Executable entry point for the governed Alpaca news stream."""

from __future__ import annotations

import json

from .governed_news_stream import run_stream_worker
from .runtime import _state_directory


def main() -> int:
    result = run_stream_worker(_state_directory())
    print(json.dumps(result, sort_keys=True), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
