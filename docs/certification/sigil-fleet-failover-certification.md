# Sigil Fleet Failover / High-Availability Certification

## Status

- Status: `not_tested`
- Certifying: `false`
- Fleet failover requirement id: `sigil-fleet-failover-v1`

## What this document is

An explicit, machine-checkable placeholder stating that **no fleet
high-availability or failover certification evidence exists for Sigil in
this repository**, rather than leaving that gap silent and open to being
mistaken for "passing" by omission.

## Why this document exists

`docs/certification/sigil-golden-master-v3.5.0-post-gamma.md` previously
described its certification command as a "Gateway and HA certification
gate" and cited `tests/integration/test_ha_integration.py` as part of that
gate. That test file exercises the **Home Assistant** smart-home platform
adapter (`plugins/platforms/homeassistant`) — an unrelated Hermes gateway
integration, not Sigil fleet high-availability or failover behavior. The
shared "HA" abbreviation made it easy to misread that test as fleet
high-availability evidence; it is not, and never was.

That document has been corrected to stop implying failover coverage it
never had. This document is the truthful replacement claim: fleet
failover/high-availability certification for Sigil is **not yet
evidenced**. No such test suite exists in `apps/sigil` as of Fleet
Unification Stage 1.

## What would satisfy this requirement

A future, separately certified stage that adds a real Sigil fleet
failover/high-availability test suite should replace the `Status` above
with `review_approved` (or the relevant passing status from
`sigil.certification.evidence.CertificationEvidenceStatus`) only once that
suite has actually executed and passed, and should record the suite
identity and run evidence the same way other Sigil certification evidence
is recorded. Until then, this file — or its successor — must keep an
explicit non-certifying status rather than being deleted or silently
dropped, so the absence of evidence stays visible.

Fleet networking, Mission Control unification, and remote fleet maintenance
are explicitly out of scope for Fleet Unification Stage 1; this placeholder
does not attempt to build failover infrastructure, only to stop
misrepresenting its absence.
