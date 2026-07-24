# Governed Portfolio Risk Engine

## Purpose and boundary

Sigil Step 13 is an offline, deterministic, read-only analysis boundary. It
calculates portfolio exposure, concentration, liquidity, return statistics,
drawdowns, covariance and correlation, beta and tracking error, historical
value at risk (VaR), historical expected shortfall, caller-defined stress
scenarios, policy-limit violations, and proposed-trade comparisons.

The engine does not fetch broker or market data. It cannot place, cancel,
replace, schedule, or approve a trade; mutate a broker snapshot, ledger,
journal, approval, evidence store, or knowledge graph; optimize or rebalance a
portfolio; or provide tax or regulated investment advice. A risk-eligible
result is advisory and is never an approval.

## Architecture and trust boundaries

`risk.models` defines frozen, slotted contracts and deterministic identities.
`risk.input` binds Step 11 and Step 12 inputs. `risk.statistics` contains
Decimal-only algorithms. `risk.engine` performs exposure, liquidity, stress,
limits, report orchestration, and read-only audit lookups. `risk.pretrade`
builds a transient simulated portfolio without invoking or modifying any
execution or persistence service.

The source-of-truth hierarchy is:

1. Step 11 snapshots are authoritative point-in-time broker observations.
2. Step 12 accounting state is the deterministic derived accounting view.
3. Step 12 valuation is the authoritative normalized valuation input.
4. Return, benchmark, classification, liquidity, and scenario observations are
   exact caller-supplied normalized inputs.
5. Step 13 outputs are derived estimates, reproducible under the identified
   immutable inputs and explicit risk-policy version. They are not external
   facts.

The builder requires exact account, snapshot, accounting-state, currency,
symbol, quantity, price, and valuation compatibility. It rejects duplicate or
overlapping return periods, future observations outside policy tolerance,
unsupported instruments, non-reconciling market values, excessive counts,
malformed provenance, and secret-bearing data. It sorts all keyed collections.
Missing observations remain missing; no value is forward-filled, interpolated,
or interpreted as zero.

## Policy and numeric contract

`PortfolioRiskPolicy` is immutable, versioned, SHA-256 identified, USD-only,
long-only, and bounded. It controls observation counts and lengths,
observations per year, return and benchmark requirements, confidence and
horizon allowlists, participation rate, valuation tolerance, future-clock
tolerance, freshness, acquisition duration, scenario count, position count,
and exact metric limits. Duplicate limit identities, impossible ratios,
contradictory sample bounds, unsupported metrics/operators/severities,
unsupported confidence levels or horizons, and secret-bearing values fail
validation.

All financial inputs and outputs are canonical decimal strings or `Decimal`.
Binary floating point is rejected at the model boundary. Arithmetic uses
`Decimal`; square roots use an isolated local 34-digit Decimal context.
Durations in identities are exact integer microseconds. Canonical sorted JSON
and SHA-256 cover every material input, timestamp, classification, policy,
scenario, completeness/staleness flag, and derived result.

## Exposure and concentration

For the supported long-only portfolio:

- position market value is exact Step 12 quantity times price;
- total equity is cash plus all priced position values;
- gross long and net exposure are kept as separate fields, although equal;
- invested and cash percentages divide their values by complete positive
  equity;
- top-N concentration is the sum of the N largest priced position weights;
- issuer, sector, industry, and instrument concentrations aggregate verified
  caller metadata;
- missing classifications use `UNKNOWN`, never a real classification;
- HHI is the sum of squared position weights and is emitted only for a complete
  priced portfolio.

If any position is unpriced, total equity and its unknown weight remain
unavailable. A partial portfolio is never described as completely measured.
Stale price weight is labeled stale and stale prices are never called fresh.

## Liquidity

Liquidity observations contain exact average daily volume and dollar volume,
optional bid/ask/spread, source identity and digest, timestamps, and
completeness. For each priced position:

`percent ADV dollars = position market value / average daily dollar volume`

`estimated days = position market value / (average daily dollar volume × participation rate)`

Missing and stale liquidity weights remain explicit. These estimates do not
claim executable liquidity, guaranteed capacity, or slippage.

## Return statistics

Return observations bind an identity, exact non-overlapping period, exact
return, source and digest, acquisition timestamp, and completeness. Returns
below -100 percent are invalid. Metrics requiring two series align only exact
period-start/period-end pairs.

The arithmetic mean is `sum(r) / n`. Population variance divides squared
deviations by `n`; sample variance divides by `n-1`. Volatility is the Decimal
square root of variance. Annualized sample volatility is multiplied by
`sqrt(observations_per_year)` only when the policy supplies that value.
Downside deviation is:

`sqrt(sum(min(r - minimum_acceptable_return, 0)^2) / n)`

Insufficient samples return a structured unavailable reason.

## Drawdown

The wealth path begins at one and compounds each supplied return:
`wealth_t = wealth_(t-1) × (1 + r_t)`. Drawdown is
`wealth_t / running_peak - 1`. Reports preserve every peak-to-trough period,
recovery when observed, maximum and current drawdown, longest duration, and the
exact observation range. Incomplete history is never called lifetime history.

## Covariance, correlation, beta, and tracking error

Pairwise sample covariance and correlation use exact matched periods.
Correlation is symmetric; its diagonal is one only when variance is valid.
Zero variance and insufficient overlap are structured unavailable results.
Missing periods are neither filled nor interpolated.

Benchmark beta is portfolio/benchmark sample covariance divided by benchmark
sample variance. Active returns are portfolio return minus benchmark return;
tracking error is their sample standard deviation and is annualized only with
explicit observations per year. Partial, stale, misaligned, insufficient, or
zero-variance benchmark evidence fails closed.

## Historical VaR and expected shortfall

Step 13 implements historical one-day VaR only, at 90, 95, or 99 percent.
Returns are ordered ascending. The lower-tail nearest-rank boundary is
`max(1, ceil(n × (1-confidence)))`; the selected return is converted to a
nonnegative loss and multiplied by exact portfolio equity. VaR is an estimate,
not a maximum possible loss and not a guarantee.

Expected shortfall uses every observation at or below the same VaR boundary.
It reports tail count, mean nonnegative tail loss percentage and amount, and
worst observed loss. An insufficient tail returns structured unavailability.
No normality, Monte Carlo, or external numerical service is used.

## Stress scenarios and limits

Caller-supplied immutable scenarios support bounded portfolio, symbol, issuer,
sector, industry, instrument, and operational cash percentage shocks, including
combined shocks. Duplicate/conflicting targets, unsupported targets/currencies,
ordinary-long losses below -100 percent, formula strings, secret-bearing data,
and excessive counts are rejected. Results report shocked values, cash,
equity/loss, affected and unknown symbols, missing classifications,
completeness, and a deterministic identity. Scenarios are hypothetical and
cannot trigger execution.

Limits use exact closed metric identities, thresholds, comparison operators,
and informational, warning, or blocking severity. Violations bind the limit,
metric, observed value, threshold, subjects, input provenance, calculation,
explanation code, and deterministic identity. Warnings remain visible.
Blocking violations fail pre-trade risk eligibility. No violation is silently
suppressed.

## Proposed-trade simulation

The simulator accepts one supported long-only equity or ETF buy or sell bound
to the same account, an exact quantity or notional, price and price timestamp,
fees, proposal identity, and simulation timestamp. It derives transient
post-trade positions/cash and regenerates risk.

Account mismatch, partial portfolio, stale valuation or proposal price,
negative cash/margin implication, holdings-exceeding sell/short implication,
blocking violation, or unavailable post-trade calculation fails closed.
Comparisons list new, resolved, and worsened violations and improved metrics.
`trade_approved` is invariantly false. No approval is created or consumed and
no broker, journal, or ledger method exists in this package.

## Completeness, provenance, audit, and recovery

The top-level report identifies its policy, account, Step 11 snapshot, Step 12
state and valuation, report time, acquisition duration, all subreports,
scenarios, violations, incomplete/unavailable/stale classifications,
completeness, advisory eligibility, limitations, provenance, and SHA-256
identity.

Provenance includes source kind and identity, digest, source and acquisition
timestamps, completeness, truncation, observation range, and account binding.
Read-only helpers verify report identity, summarize provenance, list
violations, and locate stress results. Regeneration from immutable inputs is
the recovery mechanism. Step 13 selects no hidden report repository.

Operators must validate source custody and digests, supply verified
classifications and complete normalized histories, choose bounded policy and
scenario assumptions, inspect every stale/incomplete/unavailable result and
warning, and obtain independent execution approval through the existing
governed execution boundary.

## Limitations and remaining production requirements

Historical estimates reflect only supplied observations and may not predict
future losses. Correlation does not prove diversification. Average volume is
not executable liquidity. The engine excludes shorts, leverage, margin,
options, crypto, bonds, derivatives, tax analysis, slippage modeling, opaque
ML scoring, optimization, rebalancing, live fetching, and mutable risk
history. Production adoption still requires independently reviewed policy
values, verified classification and liquidity sources, operational source
retention, report persistence owned by a caller when needed, monitoring,
operator procedures, and independent regulatory/security review.
