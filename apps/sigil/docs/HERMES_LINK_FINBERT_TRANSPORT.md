# Sigil Step 4 — Hermes-Link FinBERT Transport

## Purpose

Carry Sigil FinBERT inference requests over the governed Hermes `POST /task`
interface established by the Mac ↔ Titan communication layer.

## Flow

1. `TitanFinBERTAnalyzer` creates a local-only inference request.
2. `HermesLinkTitanFinBERTTransport` wraps it in a versioned Hermes task envelope.
3. `UrlLibHermesTaskClient` submits the envelope to `/task`.
4. The transport verifies schema, task correlation, completion status, and result shape.
5. The Step 3 analyzer validates the returned FinBERT model identity and scores.

## Security properties

- HTTPS is required except for loopback testing.
- Bearer authentication is required.
- Requests have bounded timeouts.
- Responses have a maximum byte size.
- No shell, sudo, external network, trading, spending, or publishing capability is granted.
- Task IDs must correlate exactly.
- Non-completed tasks fail closed.

## Deployment boundary

This step adds the production-capable client and transport code but does not
store a live bearer token in source control, expose Titan publicly, or start a
new service. Runtime configuration must inject the private Hermes-link URL and
secret through the existing governed deployment process.
