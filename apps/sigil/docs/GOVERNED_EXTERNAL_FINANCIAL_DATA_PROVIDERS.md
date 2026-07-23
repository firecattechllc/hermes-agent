# Governed External Financial Data Providers

## Purpose and trust boundary

Sigil Step 9 is the governed acquisition boundary between an external financial-data
provider and future Sigil normalization and evidence workflows:

```text
external provider
    -> governed adapter and HTTPS transport
    -> validated provider-neutral response and provenance
    -> future normalized financial-data records
    -> future evidence fusion and knowledge-graph registration
```

Step 9 obtains data. It does not interpret the data, recommend or rank investments,
manage portfolios, trade, schedule collection, run models, or write to the Step 8
repository or Hermes knowledge graph.

## Registry and adapters

`FinancialDataProviderRegistry` contains only adapters explicitly supplied by its
caller. Provider IDs are exact, duplicates are rejected, and listings are sorted.
There is no module discovery, dynamic execution, hidden global registry, or hidden
network probe. A future adapter must declare a stable ID and version, exact operations,
exact HTTPS hosts, credential requirements, deterministic endpoint construction, and
strict response normalization.

Normal callers construct a `FinancialDataRequest`; they cannot supply a URL, headers,
or credentials. Requests carry a provider ID, allowlisted operation, exact resource ID,
validated query pairs, bounded timeout and response bytes, purpose, cache TTL, and
optional correlation ID. Canonical JSON produces the deterministic request and cache
identity. Timestamps and credentials never contribute to that identity.

## Governed HTTPS transport

The standard-library transport permits HTTPS only, checks the exact hostname before
opening a request, forbids user information and fragments, uses GET only, and disables
redirect following. It bounds timeout, response bytes, transient retries, and
`Retry-After`. Authentication failures (401/403), rate limits (429), other client
errors, oversized bodies, non-JSON content, malformed JSON, and invalid normalized
payloads fail explicitly. Only approved transient 5xx responses are retried, at most
the configured bounded count. Tests inject an opener and never access the network.

Provenance records the request ID, provider and operation, wall-clock request and
response timestamps, HTTP status, non-secret endpoint identity, safe query-name list,
raw-content SHA-256 and byte count, cache status, adapter version, correlation ID, and
an allowlist of safe response headers. It excludes request headers, authorization
values, credential query values, and raw credentials. Timestamps do not affect content
hashes.

## Credentials and configuration

Credentials are resolved only immediately before transport. A mapping resolver supports
dependency injection. The environment resolver accepts a fixed
provider-to-variable allowlist and refuses all other environment-variable names.
Header and query credential placement are reusable for future keyed providers. Neither
placement nor credentials appear in requests, responses, provenance, normalized output,
cache entries, or cache keys. Redaction removes configured secret values and common
labelled credential forms before diagnostic text is exposed.

Hermes can later supply short-lived secrets by implementing the credential-resolver
interface at runtime. It must preserve the same exact provider binding and must not
persist or log returned values. Step 9 does not create `.env` files, modify process
environment, or read arbitrary environment variables.

## SEC EDGAR

The initial `sec_edgar` adapter supports exactly:

- `company_submissions` for a validated CIK
- `company_facts` for a validated CIK

CIKs contain one to ten decimal digits and normalize to ten digits. Endpoints are
deterministically constructed under the sole allowed host `data.sec.gov`. Operations
are GET-only JSON calls; the adapter does not scrape pages, crawl filings, traverse
archives, recurse, or perform bulk downloads.

SEC requests require an honest identifying user agent with an application identity and
contact method. Sigil never invents this value and fails closed when it is absent or
malformed. Supply it explicitly to a resolver or allowlist the exact non-secret
configuration variable `SIGIL_SEC_USER_AGENT`.

Safe shell configuration example (replace every placeholder with honest information):

```bash
export SIGIL_SEC_USER_AGENT="<application-name> <contact-email>"
```

Tests use only fictional identities such as
`Example Sigil Test test@example.invalid`. Do not place real API credentials in source,
documentation, tests, request objects, or committed configuration.

## Rate limiting, caching, and health

Each adapter owns an injected local sliding-window limiter. Allowance and window are
explicit; rejection is the default. Optional waiting must be explicitly selected and
bounded. The monotonic clock and sleeper are injectable, so tests neither sleep nor use
the network. Remote `Retry-After` seconds are parsed defensively and capped.

The optional in-memory cache has caller-supplied entry and byte limits, explicit TTL,
an injectable monotonic clock, and deterministic LRU eviction. Expired values are never
returned as fresh. Cache hits are explicit in provenance. A cache failure cannot
invalidate an otherwise successful acquisition. There is no global cache or filesystem
write.

Provider health is local and read-only: configuration presence, whether a credential is
required and available, supported operations, allowed hosts, cache enablement, local
rate-limit capacity, and local availability. It never performs an implicit network
call.

## Failure behavior and non-capabilities

All malformed, ambiguous, unauthorized, excessive, rate-limited, unallowlisted, and
partially valid inputs fail closed. No response is accepted silently after an error,
and retries are neither indefinite nor applied to authentication, validation, or most
client failures. Production code performs no shell or subprocess execution, background
work, home-directory writes, environment mutation, semantic analysis, or LLM use.

Future milestones may validate Step 9 envelopes into domain-specific normalized
financial-data records, then submit independently governed records to evidence fusion
and knowledge-graph registration. Those mutations are deliberately outside this API
boundary.
