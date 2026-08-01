# Sigil Beta UI Acceptance Checklist

## Functional parity

- [ ] Every certified Alpha button exists or has an approved replacement interaction.
- [ ] Every control invokes the same governed backend action as Alpha.
- [ ] No placeholder control appears functional.
- [ ] Disabled controls display a specific reason.
- [ ] Loading, empty, error, blocked, stale, and reconnecting states are implemented.
- [ ] Navigation preserves active symbol, proposal, and runtime context.

## Governance and safety

- [ ] Paper-only status is globally visible.
- [ ] Broker submission is not available or implied.
- [ ] Proposal generation does not imply execution authority.
- [ ] Approval and rejection require deliberate operator interaction.
- [ ] Paper authorization requirements remain enforced.
- [ ] Missing or failed dependencies fail closed.
- [ ] Confirmation dialogs state the exact action and consequence.
- [ ] Sensitive state is read from the governed backend rather than renderer assumptions.

## Market data

- [ ] Exact ticker results rank correctly.
- [ ] Search remains debounced.
- [ ] Quote price and change render correctly.
- [ ] Quote source is visible.
- [ ] Quote age is visible.
- [ ] Stale state is visible.
- [ ] After-hours or closed-session state is visible.
- [ ] Refresh does not create duplicate handlers or disruptive UI movement.

## Research and proposals

- [ ] Sources are distinguishable from model interpretation.
- [ ] Confidence and uncertainty are visible.
- [ ] Bull case, bear case, and risks remain accessible.
- [ ] Missing evidence is not presented as certainty.
- [ ] Proposal status and timestamps are clear.
- [ ] Approval and rejection outcomes persist and recover after restart.

## Runtime and portfolio

- [ ] Runtime state, cycle count, and proposal-only state are visible.
- [ ] Positions, quantities, cash, values, and P&L preserve exact meaning.
- [ ] Runtime errors offer safe recovery actions.
- [ ] Restart restores the same governed state.
- [ ] Audit evidence is created and visible for material actions.

## Desktop quality

- [ ] Keyboard navigation covers primary workflows.
- [ ] Focus indicators are visible.
- [ ] Contrast is sufficient.
- [ ] Reduced-motion preferences are respected.
- [ ] Numeric columns align consistently.
- [ ] No critical status relies on color alone.
- [ ] The packaged macOS desktop behaves like development builds.
- [ ] Production build, desktop tests, backend certification, Playwright E2E, and Release Guardian pass.

## Final sign-off

- [ ] Every-button manual audit complete.
- [ ] Full operator workflow complete.
- [ ] Alpaca authentication failure and recovery certified.
- [ ] Stream disconnect and reconnect certified.
- [ ] Duplicate-handler protection certified.
- [ ] Stale-price behavior certified.
- [ ] Paper-only boundary certified.
- [ ] Broker submission remains disabled.
