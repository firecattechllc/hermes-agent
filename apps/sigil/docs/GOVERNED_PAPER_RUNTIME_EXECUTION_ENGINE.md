# Governed Paper Runtime Execution Engine

## Scope

The Step 39 runtime is a local, deterministic paper-execution engine. It is
not a brokerage integration: it has no credentials, network transport, API
keys, provider submission path, or live-execution switch.

## Architecture and lifecycle

`desktop_bridge.runtime` owns the checksummed, locked runtime-state file.
`paper_execution` transforms that state only from an explicit paper-order
request and an injected market snapshot. The runtime validates and reserves an
order, records an immutable audit event, applies zero or more deterministic
fills, recalculates portfolio values, and atomically persists the replacement
state. On restart, checksum validation occurs before the state is loaded; an
invalid or symlinked state fails closed.

## Execution flow

Market orders fill at the supplied snapshot price. Limit orders fill only when
the supplied price crosses their limit. Stop orders are represented as open
state only; they do not trigger automatically. Partial fills retain the
unfilled quantity and reservation. Cancelling releases the remaining cash
reservation. Rejections, submissions, fills, and cancellations each append an
audit event with an explicit paper-only marker.

## Portfolio accounting

The engine maintains available and reserved cash, buying power, positions,
average cost, market value, realized and unrealized P&L, equity, and total
account value. Values use Decimal arithmetic and are recomputed after each
fill using only the injected snapshot.

## Mission Control and safety guarantees

Mission Control receives a bounded status containing cash, positions, open
orders, last execution, health, and explicit paper-mode / broker-disabled
indicators. The desktop bridge remains read-only: internal paper operations
are not exposed as arbitrary desktop or provider commands. The runtime rejects
non-paper requests and records `broker_submission_attempted: false` in all
execution audit records.
