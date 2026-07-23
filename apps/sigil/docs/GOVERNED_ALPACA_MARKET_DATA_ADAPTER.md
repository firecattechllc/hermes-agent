# Governed Alpaca Read-Only Market Data Adapter

## Boundary

Sigil Step 9A acquires bounded stock market-data JSON through the provider-neutral
Step 9 boundary. It supports only latest bars, quotes, trades, and bounded historical
bars from `data.alpaca.markets`. It performs no analysis and cannot place trades or
access or modify orders, positions, balances, brokerage accounts, watchlists,
transfers, or portfolio state. It has no streaming, scheduling, background work,
repository writes, knowledge-graph writes, or paper-trading capability.

The adapter constructs every HTTPS Market Data API v2 endpoint internally. Callers
cannot provide a URL, host, path, headers, or credential names. Exact provider,
operation, symbol, timeframe, adjustment, feed, timestamp, limit, timeout, response
size, and query-name allowlists fail closed before transport.

## Runtime-only credentials

Alpaca authentication requires two runtime-only values:

```bash
export SIGIL_ALPACA_API_KEY_ID="<runtime-key-id>"
export SIGIL_ALPACA_API_SECRET_KEY="<runtime-secret-key>"
```

Never commit keys or place them in source, requests, responses, provenance, cache
keys, logs, exceptions, examples, fixtures, or committed environment files. Sigil
does not add dotenv support or scan the environment. Construct two
`EnvironmentCredentialResolver` instances, each with the exact
`alpaca_market_data` binding for its one variable, and pass them as
`key_id_resolver` and `secret_key_resolver`. Tests and other controlled callers may
instead inject separate exact mapping resolvers.

Callers build a credential-free request with `alpaca_request`, then pass it to an
explicitly registered `AlpacaMarketDataProvider`. Credentials are resolved immediately
before transport and inserted only into `APCA-API-KEY-ID` and
`APCA-API-SECRET-KEY` at the outbound boundary.

## Provenance, cache, limits, and failures

Canonical request JSON determines request and cache identity without credentials.
Provenance records the non-secret endpoint, query names, response hash and size,
timestamps, status, adapter version, correlation ID, safe response headers, and
explicit cache hit or miss. Payload numbers and meaning are preserved; the adapter
only freezes JSON and validates the operation's high-level envelope.

An injected deterministic local rate limiter governs acquisition. The default is a
conservative 100 requests per 60 seconds and assumes no subscription tier. The
optional bounded in-memory cache uses explicit TTL, entry, and byte limits. Health is
local, never probes the network, and reports availability only when both credentials
are configured.

Malformed requests or payloads, partial credentials, authentication rejection, rate
limits, redirects, non-HTTPS or non-Alpaca endpoints, oversized bodies, non-JSON
responses, and unallowlisted parameters fail closed. The adapter never retries
authentication or validation failures and never turns acquired data into indicators,
returns, sentiment, rankings, or investment recommendations.
