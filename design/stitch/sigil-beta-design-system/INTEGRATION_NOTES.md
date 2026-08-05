# Sigil Stitch Integration Notes

## Status

The visual direction and Mission Control state designs are approved as the
reference for Sigil Beta implementation.

The exported HTML is design-reference material and must not be copied directly
into the packaged Electron runtime without adaptation.

## Required terminology corrections

- Replace "Execute entry near current VWAP levels" with
  "Proposed paper entry near current VWAP levels."
- Replace the generic reset confirmation label "EXECUTE" with
  "RESET LOCAL PAPER PORTFOLIO."
- Rename "Execution Receipts" to "Paper Execution Receipts."

## Production integration requirements

- Preserve PAPER ONLY and BROKER SUBMISSION DISABLED as global indicators.
- Use the new Mission Control layouts as authoritative over the older Overview.
- Remove external Tailwind CDN usage.
- Remove externally loaded Google Fonts and Material Symbols dependencies.
- Use locally built styles and locally available assets.
- Treat all displayed values as visual fixtures only.
- Do not introduce fixture values as runtime defaults or fallback state.
- Bind UI state only through the existing Electron preload and governed bridge.
- Do not create direct broker, provider, or financial execution calls.
- Preserve existing confirmations, disabled reasons, audit evidence, and
  fail-closed behavior.
- Implement and certify Mission Control before migrating additional screens.
