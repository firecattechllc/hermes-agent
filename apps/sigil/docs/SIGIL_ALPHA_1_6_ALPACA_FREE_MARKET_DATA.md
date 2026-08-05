# Sigil Alpha 1.6: Governed Alpaca Free Market Data

Alpha 1.6 connects the Alpha 1.5 governed universe to Alpaca's Basic
market-data architecture without creating an execution path. It is data-only:
`execution_authorized`, `live_trading_enabled`, and
`broker_submission_available` remain false.

## Official provider contract

The Trading API `GET /v2/assets` catalog is normalized into immutable
`SourceInstrument` evidence. Every response row is either accepted or assigned
an explicit exclusion reason. Alpaca identifiers, class, status, exchange,
tradability, fractionability, marginability, shortability, legacy
`easy_to_borrow`, current `borrow_status`, maintenance margin when supplied,
observation time, evidence digest, and schema identifier are preserved. Provider
presence is not liquidity evidence and does not grant proposal eligibility.

Alpaca Basic provides real-time IEX, a single-exchange partial-market feed, with
a maximum of 30 streamed stock symbols. It is not consolidated SIP and is not
represented as NBBO. Broad-market screening uses historical SIP minute bars
whose request end and provider timestamps are at least 15 minutes old. The UI
always calls this **15-minute delayed SIP**, never live SIP.

## Governance and operations

- Catalog refresh has explicit start, completion, failure, exclusion, and
  degradation audit outcomes. HTTP requests use bounded timeouts, at most three
  exponentially backed-off attempts, and explicit 401/403/429 handling.
- A last-known-good catalog and per-feed observations can be stored in atomic,
  checksummed, schema-versioned local caches. Corruption fails closed. Retention
  is bounded and cached IEX evidence must be reclassified stale.
- Delayed SIP scans sort the governed universe deterministically, use a
  configurable bounded batch size (default 200), record checkpoint progress,
  preserve missing/rejected results, and never synthesize prices.
- The IEX manager accepts ranked candidates from existing Sigil layers. It
  deterministically tie-breaks, unsubscribes before subscribing, prevents
  duplicates, honors dwell/cooldown policy, and rejects a requested set above
  the configured limit. The configurable limit cannot exceed 30.
- Negative, non-finite, boolean, or malformed numeric values are rejected.
  Quality vocabulary includes delayed, partial-market, stale, crossed/locked
  market, missing bid/ask, zero volume, sequence, timestamp, price/size, and
  provider-degraded states.

Environment variables follow Alpaca and repository conventions:
`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `APCA_API_BASE_URL`, and
`APCA_DATA_BASE_URL`. Secrets are kept in request headers only. They are neither
logged nor persisted, returned to the renderer, placed in exceptions, fixtures,
or audit details.

## Runtime and desktop

Mission Control shows configuration/authentication, catalog counts and
freshness, exclusions/conflicts, delayed-SIP progress, IEX connection and
capacity, subscribed symbols, staleness/degradation, last sanitized error, and
the data-only/live-trading-disabled safety state. Controls are limited to asset
refresh, starting/stopping delayed scanning, connecting/disconnecting IEX, and
status refresh. None can submit an order.

Provider outages degrade only the affected feed. Delayed scanning, catalog
refresh, local paper execution, and Alpaca brokerage availability are distinct
states; a brokerage outage is never reported as a local paper failure.

## Testing and limitations

Normal tests use injected deterministic HTTP and stream transports and require
no network. The synthetic 10,000-asset test validates deterministic ingestion,
50 bounded batches at the default size of 200, stable checkpoint counts, and
the independent IEX cap. It is capacity evidence, not a live Alpaca catalog.

Alpha 1.5 snapshots remain compatible because all provider fields added to
`SourceInstrument` are optional defaults. Upgrade by deploying the 1.6 desktop
and configuring credentials locally. Without credentials, the UI remains safely
unconfigured. Live credential-backed catalog counts are reported only after an
operator performs a refresh.

Known limitations: Basic IEX represents only IEX activity, historical SIP is
delayed, and live stream quality depends on provider connectivity. A future paid
SIP policy may add consolidated real-time coverage only after entitlement is
explicitly detected and governed; it must not reuse Basic labels.
