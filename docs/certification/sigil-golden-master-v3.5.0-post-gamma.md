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

Of these four commits, only `d404458f5` (Normalize Sigil worker contract
imports) touches `apps/sigil`. The other three touch repository-root Hermes
gateway/platform code (`gateway/`, `tests/gateway/`, `plugins/platforms/`)
and `tools/`. See "Evidence scope correction" below for what this means for
what this document actually certifies.

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

## Evidence scope correction (Fleet Unification Stage 1)

This section was added during Fleet Unification Stage 1 certification
integrity review. It does not alter the recorded result above; it corrects
what that result means.

1. **`tests/gateway` and `tests/integration/test_ha_integration.py` are
   repository-root Hermes gateway/platform test suites, not the Sigil
   application test suite.** Sigil's own tests live under `apps/sigil/tests`
   (2,105 tests collected as of this correction) and are not part of this
   certification command. This document's "Certification decision" below
   originally read "The Gateway and HA certification gate completed with
   zero failures... approved for final Golden Master tagging" — that
   sentence is true of the command that was actually run, but it does not
   mean Sigil's trading-domain logic (execution guards, portfolio state,
   policy enforcement, etc.) was exercised by this certification run. Three
   of the four certified commits are shared-infrastructure fixes to the
   gateway/tools layer; only one touches `apps/sigil` at all.

2. **"HA" in this command is Home Assistant, not high availability.**
   `tests/integration/test_ha_integration.py` exercises the Home Assistant
   smart-home platform adapter (`plugins/platforms/homeassistant`). It is
   not, and was never, evidence of Sigil fleet high-availability or
   failover behavior. See
   `docs/certification/sigil-fleet-failover-certification.md` for the
   explicit, truthful status of Sigil fleet failover certification (not
   yet evidenced).

- Fleet failover status: `missing_evidence`
- Fleet failover evidence path: `docs/certification/sigil-fleet-failover-certification.md`

## Test count reconciliation

The recorded result above (Passed: 9,826; Skipped: 15; Deselected: 14) was
captured against commit `470cbf47a` on `sigil-golden-master`. Re-collecting
the identical command (`tests/gateway tests/integration/test_ha_integration.py`)
against this branch during the Stage 1 review reports 9,839 collected /
9,853 total with 14 deselected — a small delta from the certified total
(9,826 + 15 + 14 = 9,855).

This delta is consistent with ordinary test-suite evolution on shared
repository-root code between the certification commit and the current
revision (new/removed test cases in `tests/gateway` or related plugin code
unrelated to this stage's changes). The repository's commit history for
this range does not contain a recorded per-commit test-count diff, so the
exact cause of the delta cannot be proven from repository contents alone —
this note states that explicitly rather than asserting a specific cause.
The originally recorded numbers above are left unchanged; this section adds
context, it does not rewrite history.

## Certification decision

The Gateway and Home Assistant integration test suites (repository-root
`tests/gateway` and `tests/integration/test_ha_integration.py`) completed
with zero failures for the certified change stack. This is valid evidence
that the certified shared-infrastructure commits did not regress those
suites. It is not Sigil application certification and it is not Sigil fleet
high-availability/failover certification; see the corrections above for
what would be required for either of those claims.
