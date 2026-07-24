# Governed Public Equity Trading Execution

## Purpose and risk boundary

Sigil Step 9B adds architecture capable of submitting real-money equity and ETF orders
through Public's personal, non-commercial API. Public is the governed execution broker;
Alpaca remains the preferred read-only market-data provider. This boundary is disabled
unless a caller supplies an explicit, finite `PublicExecutionPolicy`.

Production use can lose money. A successful preflight is an estimate, not a fill-price
or execution guarantee. A successful submission only means Public accepted an
asynchronous request; it never means the order filled.

The adapter supports exactly:

- listing accounts;
- retrieving one account portfolio;
- retrieving bounded `EQUITY` quotes;
- preflighting one exact cash-only equity order;
- submitting one exactly approved equity order;
- retrieving the resulting order state; and
- submitting one separately approved cancellation.

It supports only `EQUITY`, `BUY` or `SELL`, `MARKET` or `LIMIT`, `DAY`, `CORE`, and
`useMargin=false`. ETFs are represented by Public's `EQUITY` instrument type.

It does not implement order replacement, options, crypto, bonds, treasuries, multi-leg
orders, short selling, margin borrowing, extended or 24-hour execution, recurring or
scheduled autonomous orders, tax-lot instructions, transfers, deposits, withdrawals,
account-setting mutations, Public's hosted MCP server, Public's CLI, or a generic broker
request method. Future expansion requires a separate reviewed milestone.

## Authentication and transport

Configure only the runtime secret placeholder:

```bash
export SIGIL_PUBLIC_API_SECRET="<runtime-secret>"
```

There is no dotenv support. The secret is resolved through `CredentialResolver` only
when a temporary token is needed. Authentication uses exactly:

`POST /userapiauthservice/personal/access-tokens`

The default requested validity is 15 minutes, bounded to 5–30 minutes. The private token
manager retains only the token string and monotonic expiry in memory, expires it early,
and has a separate local authentication limiter. Neither secret nor token is serialized,
cached, logged, placed in identities/evidence, returned by health, or persisted.

The low-level Public transport is private and is not part of the stable provider API.
It enforces HTTPS, the exact `api.public.com` host, internal path construction, no
redirects, bounded timeout and bytes, canonical JSON bodies, JSON responses where
required, and safe 401/403/429/error mapping. It offers no arbitrary URL, path, headers,
body, or HTTP method interface. Raw broker transport invocation is unsupported and is a
governance violation: order submission is reachable only through the governed proposal,
preflight, approval, and record-before-transport journal path. This is an enforced
application architecture boundary, not a claim of Python-level tamper-proofing. Tests
inject every response and prohibit network access.

## Proposal, preflight, and approval

`GovernedEquityTradeProposal` is frozen and content-addressed from canonical non-secret
fields. Decimal strings are canonicalized without binary floats. Exactly one positive
quantity or notional amount is required. Market orders reject limit prices; limit orders
require them. Notional sells are excluded because local holding sufficiency cannot be
proved safely.

The injected immutable policy supplies finite order/quantity/fractional caps, an explicit
symbol allowlist, freshness/lifetime limits, and closed safety flags. There is no
unlimited or allow-all mode and no implicit production dollar cap.

Preflight is mandatory. Immediately beforehand, the adapter retrieves a fresh portfolio.
Buys require cash-only buying power at least equal to Public's returned requirement.
Sells require a known long `EQUITY` position and quantity no greater than locally
observed holdings. Missing, stale, malformed, insufficient, or mismatched portfolio and
preflight data fails closed; Sigil never resizes or substitutes a trade.

The immutable preflight record binds the proposal/hash, protected account binding, exact
submitted-body hash, response hash, provider numeric strings, acquisition/expiry, and
adapter version. It performs no recommendations or reinterpretation.

Execution requires a distinct `GovernedTradeApproval` with scope exactly
`submit_one_public_equity_order`. It binds every economic and execution term, proposal
and preflight hashes, finite maximum authorized notional, approver, expiry, correlation
ID, and a privately represented single-use nonce. Modified, broad, wildcard, free-form,
expired, mismatched, or replayed approvals fail closed.

## Idempotency, state, and reconciliation

Before outbound submission, Sigil creates an RFC 4122 client order ID and records an
immutable submission intent binding that ID to one exact body, proposal, preflight, and
approval. A transport ambiguity moves the order to
`UNKNOWN_RECONCILIATION_REQUIRED`. An explicit retry reuses the same order ID and
identical body; it never creates a replacement ID.
Before transport, the retry verifies that the supplied account resolves to the same
protected account binding recorded in the immutable intent. The retry reuses the exact
original UUID and canonical payload.

The explicit state machine includes proposed, preflighted, approved, intent-recorded,
submitted, acknowledged, partially filled, filled, cancellation requested, cancelled,
rejected, expired, and reconciliation-required states. Transitions are closed and have
no backward path. Status polling is explicit and bounded by the caller; there is no
background polling. An immediate status 404 requires reconciliation and never triggers a
new order.

Cancellation requires a separate immutable, expiring,
`cancel_one_public_equity_order` approval bound to the exact protected account and order
ID. A successful DELETE means only cancellation requested. A later GET must observe
`CANCELLED`; fills or other terminal outcomes remain authoritative.

## Privacy, evidence, cache, and health

Raw account IDs are validated bounded ASCII values used only for internal path
construction. Audit relationships use a SHA-256 protected account binding. Generic
endpoint identities use `{accountId}` and `{orderId}` placeholders. Secrets, tokens,
Authorization, raw account IDs, approval nonces, balances, holdings, and unrestricted
provider bodies are excluded from audit evidence.

Safe audit events contain stable IDs/hashes, timestamps, adapter version, correlation
ID, and explicit state transitions. The supplied in-memory journal enforces approval
single use and order-ID/body collisions during the current process lifetime. It is not
crash-durable and must be replaced with durable governed persistence implementing the
same record-before-transport semantics before unattended or production trading is
enabled.

Authentication, approvals, submission/cancellation responses, preflight, and execution
state are never stored in the ordinary financial-data cache. Execution-safety portfolio,
buying-power, preflight, and order-reconciliation requests bypass that cache.

Health is local only. It reports provider ID, host and operation allowlists, runtime
secret availability, token presence as a boolean, local limiter capacity, policy
configuration, and whether execution is enabled. It never contacts Public or exposes
credentials, account/order IDs, portfolio data, approvals, or buying power.
