# Paperclip Integration — Security Analysis

**Status: engineer-authored analysis, not an independent review.** This
document was written by the same agent that implemented
`apps/sigil/src/sigil/paperclip_transport.py` and
`apps/sigil/src/sigil/desktop_bridge/paperclip_bridge.py`. It does not
satisfy the `RISK_BLOCKED` / "independent security review" gate recorded in
`ecosystem_catalog_seed.py`'s assessment of the Paperclip discovery entries —
that gate requires a reviewer who did not write the code. Treat this as
preparatory material for that reviewer, not a substitute for them. The
catalog's `assess_discovery()` result for both Paperclip entries should
remain `RISK_BLOCKED` / `REJECT` until a genuinely independent review
completes.

## Scope reviewed

- `paperclipai/paperclip` (upstream product, read-only GitHub inspection: `package.json`, `SECURITY.md`, `.env.example`, `docs/api/*`, `docs/deploy/*`). No code from this repository was cloned, installed, or executed.
- This codebase's new client code: `paperclip_transport.py`, `paperclip_bridge.py`.

## Findings

1. **Upstream identity: high confidence, not certainty.** `paperclipai/paperclip` is corroborated by NousResearch's own `hermes-paperclip-adapter` (this repo's upstream org), a real MIT license, a real docs site, and consistent architecture across the whole repo (Postgres + better-auth + documented REST API). Residual risk: GitHub metadata (stars, forks) can still be gamed; a reviewer with no time pressure should independently confirm via non-GitHub signals (Discord activity, external mentions, package registry download counts) before treating this as fully proven.
2. **This codebase never runs Paperclip's server.** `paperclip_transport.py` is exclusively an outbound HTTP client. There is no code path in this repository that executes Paperclip's `postinstall` script, Dockerfile, or any of its source. This eliminates the largest class of supply-chain risk (arbitrary code execution from a third-party package) at the cost of leaving actual deployment as an unreviewed operator responsibility — flagged, not solved, by this run.
3. **Credential handling.** The bearer token is read once from `PAPERCLIP_API_KEY`/`SIGIL_PAPERCLIP_API_KEY`, passed by argument only (never stored on the frozen, potentially-logged `PaperclipTransportConfig`), and `PaperclipCredential.__repr__` is overridden to redact it. Verified by test (`test_credential_repr_never_leaks_token`, `test_status_never_leaks_the_credential_token`). Not verified: whether any exception path elsewhere in the call stack could still include the token in a traceback if the token itself were embedded in a URL by a future change — current code never puts it in a URL, only a header, so this is a design constraint to preserve, not a current bug.
4. **TLS verification is relaxed for private/loopback targets only.** `_request()` disables hostname/cert verification when the target resolves to a private, loopback, or `.ts.net` address — matching Paperclip's own documented self-hosted deployment model (`docs/deploy/tailscale-private-access.md`) where a CA-signed cert usually doesn't exist. A public-looking hostname still gets full verification. Residual risk: an operator who points `SIGIL_PAPERCLIP_BASE_URL` at a public address that happens to resolve to a private range (unlikely but not impossible) would silently get relaxed verification. Not treated as blocking since the bearer token is still required and this only affects transport confidentiality, not authorization.
5. **No mutation path is wired to any UI-triggered command.** `update_issue_status` (the only state-changing call) exists in the transport module but is deliberately not exposed via `desktop_bridge/runner.py` in this run — only the read-only `paperclip_status` identity check is. A future job-dispatch integration must design its own approval gate before wiring that call to anything reachable from the UI; this run does not create that path.
6. **No new local execution or filesystem surface.** `PaperclipTransportConfig.can_execute_shell` and `.can_access_local_filesystem` are hardcoded `False`, matching the `AuthorityDenials` pattern used throughout this codebase, and are asserted by test.
7. **SSRF is not fully mitigated for `base_url`.** Unlike `agent_reach_public_reads.py`, this module does not pin the resolved IP for the actual connection (it connects by hostname directly, since the target is operator-configured infrastructure rather than an arbitrary caller-supplied URL). This is an intentional scope difference, but it means a compromised or malicious `SIGIL_PAPERCLIP_BASE_URL` value (e.g., set by a compromised config-management path) could redirect requests to an internal service. Mitigated in practice by requiring `enabled=True` and the base URL being an explicit, operator-set value, not runtime-supplied input — but a genuinely independent reviewer should confirm this scope decision is acceptable for the deployment model actually used.

## Recommendation

Do not change `RISK_BLOCKED` status. Commission a reviewer independent of this implementation before enabling `SIGIL_PAPERCLIP_ENABLED=true` in any real deployment, and before designing the job-dispatch (mutating) integration this run deliberately left unwired.
