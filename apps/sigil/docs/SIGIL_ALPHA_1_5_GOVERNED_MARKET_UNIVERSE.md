# Sigil Alpha 1.5: Governed Market Universe

Alpha 1.5 replaces the twelve-symbol screening list as the authoritative
universe model with a governed, provider-neutral market-universe backend. The
existing list remains only as an honestly labelled local seed catalog until a
licensed or credentialed provider catalog is available.

## Safety boundary

The universe is analytical and read-only. It cannot place an order, authorize
execution, approve a proposal, transfer funds, change a capital limit, or
enable broker submission. Mission Control projections always return
`execution_authorized: false` and `broker_submission_available: false`.
Alpha 1.4 paper-runtime persistence, pause controls, monthly authorization,
recovery, and approval gates are unchanged.

## Canonical model

`sigil.market_universe` accepts immutable `SourceInstrument` evidence. Inputs
are normalized to uppercase canonical symbols and identifiers, bounded names,
sorted aliases, allowed geographies, explicit asset classes, and explicit
lifecycle states. Canonical identity prefers FIGI, then ISIN, then CUSIP, and
finally the country/exchange/symbol listing identity. Stable Sigil instrument
IDs are SHA-256 derived from that canonical identity.

Each canonical instrument retains:

- aliases and provider record identities;
- exchange, currency, country, asset class, sector, and industry;
- active, halted, delisted, or unknown lifecycle state;
- per-source observation time and SHA-256 evidence digest;
- validated, conflicted, or excluded reconciliation state;
- explicit conflict fields and exclusion reasons;
- deterministic monitoring tier and universe memberships.

Source records are sorted before reconciliation, so provider arrival order
cannot change snapshot identity or results. Duplicate source identities,
unsupported geography, malformed identifiers, and invalid classifications
fail closed.

## Separate universes

The following memberships are never collapsed into one “watched” claim:

1. **Master** — validated canonical instruments.
2. **Broker tradable** — master instruments confirmed tradable by every
   contributing record.
3. **Actively researched** — validated instruments explicitly selected for
   active research.
4. **Proposal eligible** — validated, active, supported instruments that are
   both broker tradable and actively researched and explicitly requested for
   proposal eligibility.
5. **Excluded** — conflicted, inactive, unsupported, or otherwise
   policy-ineligible instruments.

Monitoring tiers use the same deterministic precedence: excluded, proposal
eligible, actively researched, broker tradable, then master only. Proposal
eligibility never implies proposal approval or execution authorization.

## Reconciliation and persistence

Conflicting canonical attributes are retained as conflict evidence and exclude
the instrument from tradable, researched, and proposal-eligible projections.
Delisted, halted, unknown, or unsupported instruments are likewise excluded
with explicit reasons.

`UniverseStore` writes a canonical JSON payload in a SHA-256 checksummed
envelope. Writes use a same-directory temporary file, file `fsync`, atomic
replace, and directory `fsync`. Reads verify the checksum and exact schema
before returning immutable records. Corrupt, incomplete, or non-canonical
state fails closed.

## Runtime and Mission Control

The desktop bridge allowlists two new read-only commands:

- `market_universe_status`
- `market_universe_search`

Status projects separate membership counts, conflicts and exclusions, source
record count, snapshot and policy identities, the 8,000–12,000 target, and an
honest capacity/coverage distinction. Search supports symbol, issuer,
exchange, and alias text with universe, asset-class, lifecycle, and monitoring
tier filters. Results are sorted deterministically, paginated, and bounded to
100 rows per request.

Mission Control displays the four separate universe counts, catalog identity,
capacity evidence, coverage limitation, bounded search, and filters. It does
not receive provider credentials or a submission capability.

## Scale and provider limitation

The deterministic backend suite reconciles at least 10,000 synthetic source
records in forward and reverse order and proves identical snapshot identity.
This certifies the target capacity without claiming synthetic companies are
real instruments.

The bundled runtime currently projects twelve validated demonstration
equities because no licensed or credentialed full asset catalog is included.
Real 8,000–12,000 coverage requires provider credentials or datasets and
production source-recency policy. Until supplied and reconciled, the desktop
states that limitation and does not claim whole-market coverage.

## Validation

Run:

```text
PYTHONPATH=apps/sigil/src .venv/bin/python -m pytest apps/sigil/tests -q
npm run typecheck --workspace @firecattechnology/sigil-desktop
npm run test --workspace @firecattechnology/sigil-desktop
npm run lint --workspace @firecattechnology/sigil-desktop
npm run build --workspace @firecattechnology/sigil-desktop
```
