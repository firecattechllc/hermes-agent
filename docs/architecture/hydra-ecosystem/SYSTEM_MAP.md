# System Map

## Hermes

Role:
Governed engineering command center.

Current location:
Development Mac.

Target location:
Titan as an always-on engineering control plane.

Responsibilities:

- planning
- governed task execution
- code generation coordination
- testing
- review
- release preparation
- recovery
- engineering memory
- Mission Control
- autonomous backlog management

## Prime

Role:
Identity and device control plane.

Expected responsibilities:

- user and device identity
- device registration
- membership
- licensing
- routing
- authorization
- revocation
- trust establishment

## Titan

Role:
Always-on private engineering and operations node.

Target responsibilities:

- host Hermes
- execute governed engineering workflows
- coordinate build and validation agents
- maintain engineering evidence
- prepare releases
- supervise distributed devices

## Hydra Live

Role:
Minimal hardened host operating environment.

Expected responsibilities:

- boot integrity
- hardware access
- network initialization
- isolation
- recovery
- Sigil OS hosting
- signed update installation

## Sigil OS

Role:
Restricted guest environment dedicated to Sigil.

Expected responsibilities:

- run Sigil application services
- isolate trading workloads
- enforce restricted application scope
- accept governed signed updates
- expose controlled health and telemetry interfaces

## Sigil

Role:
AI-assisted personal investment and paper-trading platform.

Current state:
Governed stabilization baseline opened as draft PR #7.

Safety posture:

- paper-first
- explicit approval gates
- no automatic live brokerage execution without authorization
- credentials remain outside Git

## Development Mac

Role:
Current source development, validation, and release workstation.

Future posture:
Operator console and emergency development environment after Titan assumes
always-on Hermes duties.
