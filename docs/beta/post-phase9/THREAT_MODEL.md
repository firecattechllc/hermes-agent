# Threat Model Summary

## Provisional status

Phase 9 implementation is locally validated, while committed proof of its
live-node certification is missing. This Stage 0 planning document authorizes no
runtime implementation, installation, or activation.

Primary risks are authority confusion, duplicate harnesses, dependency drift,
credential leakage, policy-bypassing fallback, prompt injection, unbounded cost,
stale health, incomplete cancellation, worker compromise, and general tools
reaching financial actions. Canonical controls and recovery requirements are in
[HERMES_ECOSYSTEM_THREAT_MODEL.md](HERMES_ECOSYSTEM_THREAT_MODEL.md).

Treating local tests as live-node certification is an explicit release blocker.
