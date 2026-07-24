# Governed Portfolio Ledger and Performance Accounting

## Purpose

Step 12 adds a deterministic local accounting view for one brokerage account. It
explains cash, positions, lots, cost basis, realized and unrealized gain or loss,
valuation, performance, and accounting-period state from immutable normalized
observations. It cannot trade and is not broker truth, a tax engine, or an
investment-advice system.

## Architecture and trust boundaries

The source-of-truth hierarchy is explicit:

1. Broker transactions, fills, cash movements, positions, and balances are
   authoritative external observations.
2. The Step 10 execution journal is authoritative for Sigil intent, approvals,
   and broker mutations initiated by Sigil.
3. Step 11 snapshots are authoritative point-in-time broker observations.
4. Step 12 is a reproducible derived accounting view.

`models.py` contains immutable contracts and closed event types. `ledger.py`
provides append-only persistence and audit. `ingestion.py` converts supplied
provider observations and validates injected adjustment approvals. `replay.py`
derives cash, lots, realized gains, valuations, and unrealized gains.
`performance.py` computes returns and period-close artifacts.
`reconciliation.py` compares derived state with a Step 11 snapshot without
mutating either source.

No accounting result is approval or execution authority. The package imports no
execution service, broker transport, credential manager, scheduler, or external
market-data client.

## Normalized source ingestion

The closed `PortfolioLedgerEventType` set is:

- `account_opening_balance`
- `cash_deposit`
- `cash_withdrawal`
- `buy_fill`
- `sell_fill`
- `dividend`
- `interest`
- `fee`
- `tax_withholding`
- `stock_split`
- `reverse_split`
- `cash_adjustment`
- `position_transfer_in`
- `position_transfer_out`
- `broker_correction`
- `valuation_observation`
- `reconciliation_adjustment_proposed`
- `reconciliation_adjustment_approved`
- `accounting_period_closed`
- `accounting_period_reopened`

Every type has an exact field allowlist and validation contract. Callers cannot
append arbitrary names or payload fields. Money, price, quantity, basis, and
return inputs use canonical decimal strings or `Decimal`; floats, NaN, and
infinity are rejected. Only USD equities and ETFs are supported.

Each source event preserves the protected account binding, provider, provider
record ID, provider and acquisition timestamps, raw-response digest,
normalized-event digest, completeness, truncation, and optional pagination
identity. Secret-bearing keys, including authorization headers, cookies, tokens,
API keys, and private keys, are rejected recursively.

The current Public Step 11 adapter exposes trade executions and point-in-time
portfolio state. It does not yet expose complete normalized endpoints for all
cash activity, dividends, interest, withholding, transfers, or corporate
actions. Step 12 therefore provides provider-neutral contracts and offline
fixtures; it does not fabricate Public endpoints and does not claim complete
history when these observations are unavailable.

## Append-only format, identity, and durability

The caller supplies an existing absolute repository directory; there is no
hidden default. Each account/ledger pair has a bounded directory. Records are
canonical JSON named with a strict sequence and entry hash. A record binds:

- ledger version, account, and ledger identity
- sequence and previous/current SHA-256 entry hashes
- event identity and closed event type
- source identity, provider, record ID, response digest, and timestamps
- effective and acquisition timestamps
- currency and canonical payload
- creation timestamp and accounting-policy version
- completeness, truncation, and pagination metadata

The event identity hashes the complete normalized event excluding only its own
identity. The entry hash covers the persisted record excluding only the current
entry hash. Exact repeated append is idempotent. A provider/record ID with
different normalized content is a conflict.

Append takes repository and account advisory locks, writes a mode-0600 temporary
file, flushes and `fsync`s it, atomically replaces the final path, and `fsync`s
the account directory. New account directories are mode 0700 and their parent is
also synchronized. Repository, account, record, and byte counts are bounded.

Reads validate canonical encoding, exact schema, version, strict contiguous
sequences, filename identity, account/ledger binding, hashes, previous links,
event identities, source uniqueness, and all domain fields. Symlink roots,
symlink records, traversal identifiers, unexpected files, malformed/truncated
records, payload modification, and cross-account injection fail closed. There
is no silent repair, deletion, or historical mutation.

## Cash and lot accounting

Replay is authoritative only as a derivation. Each event has an explicit cash
delta:

- opening balance and deposits increase cash;
- withdrawals decrease cash;
- buys decrease cash by gross consideration plus supplied fees and taxes;
- sells increase cash by net proceeds after supplied fees and taxes;
- dividends and interest increase cash;
- fees and withholding decrease cash;
- explicit broker corrections and governed cash adjustments apply their signed
  amount.

External contributions and withdrawals are tracked separately from investment
return. Explicit fees, taxes, and withholding are never omitted. Settlement
dates are preserved when supplied. The current provider contract does not
provide sufficient universal detail to derive settled versus unsettled cash, so
those fields remain unavailable rather than guessed.

FIFO is the versioned default for equities and ETFs. A buy creates a deterministic
lot whose basis includes supplied acquisition fees and taxes. A sell consumes
oldest lots, allocates disposal fees and proceeds pro rata by exact quantity, and
emits an immutable realized-gain record for each consumed lot. A sale exceeding
known quantity fails as unresolved incomplete history; short selling is never
inferred. Average-cost is represented as a policy capability but is not enabled
unless a future instrument-specific implementation explicitly allows it.

Forward and reverse splits multiply lot quantities by the exact supplied ratio.
Total basis remains unchanged, so per-unit basis changes inversely. Invalid
ratio direction and splits without a known lot fail closed. Cash in lieu must
arrive as separate broker activity.

## Valuation and unrealized gain or loss

Valuation accepts caller-supplied exact prices only. Each result binds the
account, valuation/source/acquisition timestamps, Step 11 snapshot identity,
optional market-data identity, cash, position values, total equity, stale
symbols, unpriced symbols, completeness, and a content identity. Missing prices
are not zero. Missing or stale prices make valuation partial and omit complete
total equity.

Unrealized gain or loss requires open lots and a complete fresh valuation in the
same currency. For each position:

`unrealized gain = market value - remaining open-lot basis`

Valuation changes never become realized gains.

## Performance accounting

Performance is available only from complete caller-supplied valuations. The
period report separates beginning/ending equity, external contributions and
withdrawals, net external flow, investment profit or loss, realized/unrealized
gain or loss, dividends, interest, and fees.

Time-weighted return uses valuation subperiods divided at supplied external cash
flows. For each subperiod:

`r = (ending equity - external flow during subperiod) / beginning equity - 1`

and the linked return is:

`TWR = product(1 + r) - 1`

A non-positive denominator, incomplete/stale valuation, or missing required
valuation returns structured unavailability. Results use `Decimal`,
round-half-even, and the policy return scale.

Money-weighted return builds dated cash flows with beginning equity negative,
external contributions negative, withdrawals positive, and ending equity
positive. It solves NPV = 0 with deterministic Decimal bisection inside the
policy's bounded rate domain, tolerance, and iteration cap. A domain without a
bracketed root or failure to converge returns a reason instead of an IRR.
Conservative bounded bracketing avoids guessing when ambiguity cannot be ruled
out; production tax or actuarial use is out of scope.

Benchmark comparison accepts normalized caller-supplied beginning and ending
benchmark valuations. It reports benchmark identity, timestamps, return, and
portfolio excess return. The accounting layer never fetches a benchmark.

Incomplete history is never labeled lifetime performance.

## Period close and reopen

A close artifact binds the account, period, first/last sequences, chain head,
opening/closing state digests, valuation and performance digests, discrepancy
count, completeness, approval identity, close timestamp, and content identity.
Close is blocked by chain corruption, incomplete/truncated history, unknown
source coverage, missing/partial valuation, unresolved activity, or any material
discrepancy.

Once a close event is committed, activity effective in that interval is rejected.
A reopen event must name the active close and preserve a reason code, injected
approval ID, and approval digest. Reopen is auditable and does not rewrite the
close or intervening history.

## Step 11 reconciliation

The read-only reconciler compares protected account and currency, derived and
broker cash, settled cash when comparable, position presence and quantity,
derived and broker-observed cost basis, source-history completeness, snapshot
freshness/completeness/truncation, and equity when a valuation is supplied.
It returns stable sorted discrepancies and a deterministic report digest.

Broker cost basis remains an observation separate from Sigil-derived basis.
Reconciliation never writes a ledger event, execution-journal event, approval,
period close, or broker mutation; it never balances a discrepancy automatically.

## Adjustment governance

Manual reconciliation follows three distinct immutable events: proposal,
approval consumption, and adjustment. A proposal binds signed bounded amount,
reason, affected fields, and evidence digest. The injected
`AccountingAdjustmentApproval` must match the exact account, proposal identity,
reason, amount, affected fields, and evidence. Its digest binds every term and
operator timestamp. Approval IDs are single use. Changed terms, account mismatch,
or reuse fail closed. The final adjustment preserves both proposal and approval
bindings and is never represented as broker-originated activity.

This is deliberately narrow and does not create a general approval system or an
“edit ledger” function.

## Recovery, corruption, and operator responsibilities

Audit, replay, source lookup, event lookup, coverage summary, and reconciliation
are read-only and offline. Corruption is an incident: isolate the repository,
preserve the bytes, compare against broker and Step 10/11 evidence, and restore
only through an operator-controlled process that preserves original history.
Recovery never calls a broker, fetches prices, trades, cancels, adjusts, closes,
or mutates historical files.

Operators must supply a secure local repository, complete normalized provider
history, explicit policy, injected clock values, fresh valuation inputs,
evidence-backed approvals, and independent review of discrepancies. They must
not describe partial coverage as lifetime performance.

## Limitations and remaining production requirements

Step 12 intentionally does not implement tax filing advice, wash sales,
broker-specific tax-lot elections, LIFO/HIFO/tax optimization, complex corporate
actions, mergers, spin-offs, options, crypto, margin, shorts, multi-currency
conversion, or unattended trading. It does not prove tax-lot correctness beyond
the implemented FIFO policy.

Production rollout still requires complete provider activity pagination and
capability certification; broker-specific cash/settlement semantics; operational
backup, retention, monitoring, and incident procedures; independent accounting
review; scale and crash testing on target filesystems; and explicit policy for
any future average-cost instrument. Until those requirements are met, source
coverage and performance completeness must remain conservative.
