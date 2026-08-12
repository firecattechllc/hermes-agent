#!/usr/bin/env python3
"""Stable product-identity resolver for Agent Watch evidence."""

from __future__ import annotations

import json
import pathlib


CONTRACT = pathlib.Path(__file__).with_name("agent_watch_paths.json")


def evidence_directory(bundle_identifier: str, *, home: pathlib.Path | None = None) -> pathlib.Path:
    mapping = json.loads(CONTRACT.read_text(encoding="utf-8"))
    relative = mapping.get(bundle_identifier)
    if not isinstance(relative, str):
        raise ValueError(f"unsupported Sigil bundle identifier: {bundle_identifier}")
    root = home if home is not None else pathlib.Path.home()
    return root / "Library/Containers" / bundle_identifier / "Data/Library/Application Support" / relative
