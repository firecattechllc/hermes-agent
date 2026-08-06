# Ollama Routing Boundary

Hermes add-on Phase A consolidation. This document records the operator
decision resolving the "ollama-routing-duplication" entry in the
`HERMES_ADDON_AUDIT.md` duplication register.

## Two non-overlapping implementations, not duplicates

| | `hermes_cli/prime/ollama_node.py` | `apps/sigil/src/sigil/ai/{mac_ollama,gemma}.py` |
|---|---|---|
| Scope | Cross-node fleet dispatch (Prime routing a request over the network to Titan's or Mac's Ollama endpoint) | Local, in-process Sigil advisory inference on the Mac desktop host only |
| Caller | `hermes_cli/prime/sigil_route_server.py` governed dispatch gate | Sigil's own `GovernedModelRouter` / AI service layer |
| Network path | HTTP request across the tailnet to a remote node's `/api/generate` | Loopback-only (`127.0.0.1`), enforced by `mac_ollama.py`'s endpoint validation |
| Governance | `SigilContractRequest` (paper-only, self-address rejection, execution-authority-denied) | `GOVERNANCE_BOUNDARIES` dict, `profile_default=disabled` |
| Authoritative for | All real fleet-dispatched inference | All real local-only Sigil advisory inference |

## Operator decision

Both are authoritative within their own, non-overlapping scope. Neither is
deprecated or merged into the other. A future request should be routed to
`hermes_cli/prime/ollama_node.py` if and only if it crosses a node boundary
(Prime → Titan or Prime → Mac over the network); it should be routed to
`apps/sigil/src/sigil/ai/{mac_ollama,gemma}.py` if and only if it is Sigil
performing local, in-process inference on the same host it runs on. No code
path may call both for the same logical request, and no new code may
duplicate either transport.

This boundary does not change any runtime behavior; it documents behavior
that was already true prior to this decision.
