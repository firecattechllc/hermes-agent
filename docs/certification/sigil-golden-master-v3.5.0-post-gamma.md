# Sigil Golden Master v3.5.0 Post-Gamma Certification

## Certification identity

- Product: Sigil
- Release: v3.5.0 post-Gamma Golden Master
- Branch: `sigil-golden-master`
- Gamma base tag: `sigil-gamma-v3.5.0`
- Gamma sign-off commit: `2a7d66fd3`
- Certified source commit: `470cbf47a`
- Golden Master tag: `sigil-golden-master-v3.5.0-post-gamma`

## Certified change stack

- `5cdf3ba67` — Stabilize gateway certification compatibility
- `d404458f5` — Normalize Sigil worker contract imports
- `b9d211b00` — Stabilize gateway certification across platforms
- `470cbf47a` — Allow active macOS temporary workspace paths

## Certification command

```bash
ulimit -n 4096

uv run pytest \
  tests/gateway \
  tests/integration/test_ha_integration.py \
  -x -q
```

## Previously verified result

- Passed: 9,826
- Failed: 0
- Skipped: 15
- Deselected: 14
- Warnings: 285
- Duration: 391.86 seconds
- Python: 3.13.14
- pytest: 9.0.2
- Platform: macOS Darwin

## Certification decision

The Gateway and HA certification gate completed with zero failures. This
post-Gamma source tree is approved for final Golden Master tagging after the
verification run recorded against this manifest commit.
