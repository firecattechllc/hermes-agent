# Sigil Stitch Handoff

This directory is the governed design handoff for the Sigil Beta frontend redesign.

## Purpose

Google Stitch should use this package to understand Sigil as a complete product before generating or revising screens. The package is intentionally separate from runtime code so the Alpha implementation remains frozen while the Beta interface is explored.

## Start here

1. Read `STITCH_CONTEXT.md` for the product, architecture, safety boundaries, user flows, and visual direction.
2. Read `sigil-ui-manifest.json` for the machine-readable inventory of screens, states, actions, and invariants.
3. Compare generated designs against `BETA_ACCEPTANCE_CHECKLIST.md` before integration.
4. Add current certified screenshots to `docs/stitch/screenshots/` before the first Stitch generation pass.

## Authority order

When sources conflict, use this order:

1. Certified Alpha runtime behavior and tests
2. Safety and governance invariants in this package
3. Machine-readable UI manifest
4. Visual design guidance
5. Stitch-generated assumptions

A generated design must never redefine backend behavior, execution authority, approval requirements, persistence semantics, or broker boundaries.

## Beta workflow

```text
Certified Sigil Alpha
        ↓
Freeze screen/action inventory
        ↓
Capture certified screenshots
        ↓
Feed this package + screenshots to Stitch
        ↓
Review generated designs
        ↓
Integrate into the existing desktop architecture
        ↓
Run parity, governance, packaging, and recovery certification
```

## Scope

Stitch may redesign presentation, layout, hierarchy, spacing, typography, navigation, responsive behavior, data visualization, and component composition.

Stitch may not invent capabilities, bypass governed actions, enable broker submission, weaken paper-only constraints, hide stale-data warnings, remove confirmations, or convert unavailable actions into apparently functional controls.
