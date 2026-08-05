# Architecture Summary

## Stage 1 status

Phase 9 live-node certification is complete and merged. Stage 1 is authorized
only for a descriptive, disabled-by-default integration registry with read-only
Mission Control projection. It grants no implementation authority to external
integrations and no installation or activation authority. The Stage 2 worker/job
contract and every later stage remain unimplemented and disabled.

Hermes is the control plane. WebUI and Sigil Mission Control are operator
surfaces; Buzz is collaboration; Paperclip is organization and assignments;
Buzznode and hosted agents are workers; Agent Reach is an optional internet
capability selector; wiki/catalog sources are advisory; GitHub is the reviewed
source of truth.

Every external request follows:

`intent -> Hermes identity/policy/budget/approval -> job admission -> pinned adapter/backend -> evidence validation -> audit projection`

Failure, missing policy, stale health, incompatible versions, missing evidence,
or ambiguous completion fails closed. Detailed architecture is in
[HERMES_ECOSYSTEM_ASSESSMENT.md](HERMES_ECOSYSTEM_ASSESSMENT.md) and
[HERMES_ECOSYSTEM_DECISIONS.md](HERMES_ECOSYSTEM_DECISIONS.md).

All described external components and request flows are target architecture, not
deployed or enabled behavior. Hermes WebUI, Paperclip, Buzz, Buzznode, Agent
Reach, Self-Evolution, and community integrations remain disabled by default and
have no independent execution authority. Sigil remains paper-only with broker
submission disabled.
