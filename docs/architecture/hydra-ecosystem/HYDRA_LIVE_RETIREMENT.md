# Hydra Live retirement

Hydra Live is retired from the active production fleet as of 2026-08-11.
`hydra-hostinger` supersedes it as the always-on Hermes/Sigil production host.

The active governed topology is Prime, Titan, Mac, and hydra-hostinger, with
iPhone acting as a client/device where applicable. Hydra Live is not required
for fleet completeness, health, certification, admission, startup, routing,
repair, or Sigil healthy-state calculations.

Historical evidence, discovery captures, release records, schemas, and the
fake-only governed repair implementation remain in the repository for audit
and backwards-compatible decoding. Their presence does not make Hydra Live an
active node or authorize repair activity against the retired VM.

This repository migration does not power down the VM, change Tailscale, revoke
credentials, or modify any fleet service. Those are separate operator actions.
