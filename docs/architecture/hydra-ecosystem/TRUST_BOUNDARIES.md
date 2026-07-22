# Trust Boundaries

## Human authority boundary

The user is the final authority for:

- production merges
- signed releases
- deployments
- new device admission
- credential installation
- brokerage enablement
- spending
- destructive recovery
- live trading

## Hermes boundary

Hermes may:

- inspect
- plan
- propose
- build isolated branches
- run tests
- prepare evidence
- prepare releases
- recommend recovery

Hermes must not independently:

- expose secrets
- authorize new devices
- deploy high-risk changes
- enable live trading
- spend outside approved budgets
- bypass approval gates

## Prime boundary

Prime should decide:

- whether a device is recognized
- whether a device remains trusted
- what services it may access
- whether its credentials are valid
- whether its membership is revoked

## Hydra Live boundary

Hydra Live should protect:

- host hardware
- boot chain
- local storage
- network access
- guest isolation
- update admission

## Sigil OS boundary

Sigil OS should allow only:

- approved Sigil services
- approved telemetry
- approved update channels
- approved outbound integrations

## Sigil boundary

Sigil must separate:

- research
- recommendations
- paper execution
- live execution
- brokerage synchronization
- user authorization
