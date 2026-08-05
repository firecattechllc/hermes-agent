# Governed Brokerage Account and Portfolio State

## Purpose

Step 11 provides immutable, validated, read-only observations of one protected Public
brokerage account. These observations support pre-trade safety checks, post-trade
reconciliation, portfolio accounting, future risk analysis, stale-data detection, and
operator-visible discrepancy reporting. The boundary never places, cancels, replaces, or
schedules an order.

## Architecture and provider trust boundary

`sigil.portfolio.models` owns exact normalized domain state and deterministic identities.
`sigil.portfolio.state` acquires account, position, order, and execution data through four
closed Public GET operations. `sigil.portfolio.reconciliation` compares a snapshot with the
validated Step 10 journal through read-only methods.

The Public transport retains the existing exact `https://api.public.com` host restriction,
HTTPS-only construction, redirect rejection, finite timeout, bounded response, JSON content
validation, runtime-only token management, and redacted transport errors. Step 11 adds only:

- `GET /userapigateway/trading/account`
- `GET /userapigateway/trading/{accountId}/portfolio/v2`
- `GET /userapigateway/trading/{accountId}/history`

The account identifier is validated before every request. It is bound in normalized state as
`public-account-sha256:<digest>`. Credentials and authorization headers are never accepted by
portfolio models, provenance, snapshots, or reconciliation reports.

## Source-of-truth rules

1. Broker-reported account and order state is authoritative for external brokerage truth.
2. The durable journal is authoritative for Sigil's recorded intent, approval, and mutation
   history.
3. A conflict between broker state and journal history is an incident requiring reconciliation.
4. Local submitted state alone does not prove broker acceptance.
5. Broker state alone does not prove Sigil approval.
6. Missing data is never interpreted as zero.
7. Partial state is never represented as complete state.

Reconciliation never treats an absent broker order as permission to retry.

## State contracts

`BrokerageAccountState` binds provider identity, protected and provider account identifiers,
account type/status, USD currency, eligibility, exact cash/buying-power/equity values, distinct
provider/acquisition timestamps, and the raw response digest.

`BrokeragePosition` supports equity and ETF symbols, exact quantity/cost/value/price/gain fields,
USD currency, distinct timestamps, and a canonical per-position digest. Optional broker values
remain `None`; they are not synthesized.

`BrokerageOrderState` binds client and provider order IDs, instrument, side, market/limit type,
exact quantity or notional, limit price rules, time in force, allowlisted status, fills,
timestamps, and a status-derived terminal classification.

`BrokerageExecution` binds provider execution/order identities, optional client identity,
instrument and side, exact fill quantity/price/fees, execution time, and bounded deterministic
settlement metadata.

All state is frozen. Decimal strings are parsed with `Decimal` conventions and canonicalized.
Floats, NaN, infinity, impossible negatives, malformed identifiers/timestamps/statuses,
unsupported currencies/instruments/sides/order types, inconsistent order fields, and duplicate
logical identities are rejected.

## Snapshot construction and deterministic hashing

`BrokeragePortfolioSnapshot` binds exactly one protected account and includes account,
positions, open/recent orders, executions, and acquisition provenance. Inputs are sorted by
stable identities. Canonical JSON uses sorted keys, compact separators, ASCII encoding, exact
decimal strings, ISO-8601 timestamps, and no NaN values. The SHA-256 `snapshot_id` covers all
normalized values, provider and acquisition timestamps, provenance, component completeness,
truncation flags, and acquisition duration. Any material change therefore changes identity.

Each component has independent completeness and truncation metadata. Truncation makes that
component incomplete. Missing components must be reported as incomplete rather than replaced
with empty authoritative state.

## Freshness, staleness, and pre-trade eligibility

`PortfolioFreshnessPolicy` supplies maximum account, position, and open-order ages; permitted
future clock skew; maximum acquisition duration; and maximum position, order, and execution
counts. Inspection may retain a stale or partial immutable snapshot.

`pre_trade_eligibility` is the fail-closed decision boundary. It rejects the wrong account,
partial or truncated components, stale observations, excessive future timestamps, slow
acquisition, and count-limit violations. Only a complete, correctly bound, fresh snapshot is
eligible to be considered by later pre-trade policy. Eligibility does not itself authorize a
trade and does not weaken Step 9B approval or Step 10 durability.

## Journal reconciliation and discrepancies

The reconciliation service calls only `audit()` and `read_events()`. It does not submit,
resubmit, cancel, replace, create or consume approvals, mutate broker state, or append to the
journal. The deterministic report identifies matched intents, acknowledged/ambiguous state,
absent journal orders, unjournaled broker orders, account/symbol/side/quantity/notional/type/
price/client-ID/provider-ID mismatches, terminal conflicts, newly observed terminal state,
resolved cancellation ambiguity, unresolved reconciliation, and corrupt journal history.

An operator must investigate every conflict. Any later journal correction or broker mutation
requires a separate governed action.

## Secret handling and operator responsibilities

Public secrets are resolved only by the existing runtime token manager. Transport failures
expose bounded classifications without URLs, headers, request bodies, tokens, or provider
response bodies. Operators must configure exact account identity and freshness/count policy,
review partial and stale classifications, protect journal storage, investigate discrepancies,
and retain final authority over approvals and remediation.

## Limitations and explicit non-capabilities

Step 11 does not provide investment or tax advice. It has no margin, short selling, options,
crypto, bonds, transfers, recurring orders, unattended trading, scheduling, generic broker
client, arbitrary HTTP interface, evidence-store mutation, or knowledge-graph mutation. It
does not infer fills, cash movement, or position changes from local submissions.

Before production trading, the exact Public account-state endpoint contracts and pagination
semantics must be certified against provider documentation in a non-production account;
complete-response guarantees, status mappings, execution/fee/settlement availability, clock
quality, operational alerting, retention, incident response, and operator reconciliation
procedures must be independently approved. Step 9B human approval and Step 10 durable intent
requirements remain mandatory.
