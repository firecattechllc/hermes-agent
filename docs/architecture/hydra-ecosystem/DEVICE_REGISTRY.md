# Device Registry

| Device | Current role | Target role | Authority | Status |
|---|---|---|---|---|
| MacBook Air | Development workstation | Operator and emergency workstation | Human-operated | Active |
| Titan | Development extension | Always-on Hermes command center | Governed by user | Planned |
| Prime | Control-plane device | Identity, routing, registration, revocation | Governed by user | Planned |
| Hydra host | Not finalized | Hardened host for Sigil OS | Prime-authorized | Planned |
| Sigil client devices | Not finalized | Approved user interfaces | Limited client authority | Planned |

## Required discovery

For each physical or virtual device record:

- hardware model
- CPU architecture
- RAM
- storage
- operating system
- hostname
- network interfaces
- Tailscale identity
- SSH posture
- disk encryption
- secure boot capability
- role
- owner
- trust level
- permitted services
- recovery path
