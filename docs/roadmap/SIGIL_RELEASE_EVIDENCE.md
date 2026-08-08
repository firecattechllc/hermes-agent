# Sigil Release Evidence Log

Verification evidence for each release in `SIGIL_RELEASE_ROADMAP.md`.
Entries are dated and describe exactly what was directly observed —
never claims without a corresponding command or test run.

## Sigil 3.7.0 — 2026-08-05

### Live hardware deployment

- **Prime** (`hydra-prime`, Raspberry Pi 5, Tailscale `100.119.205.44`):
  systemd service `hermes-prime.service`, active, bound only to its
  Tailscale interface (`ss -tlnp` confirms `100.119.205.44:8743`, not
  `0.0.0.0`). `curl http://100.119.205.44:8743/v1/fleet/health` from this
  Mac returned `{"status": "ok"}`. `GET /v1/fleet/nodes` without a bearer
  token returned `401`. Service restart verified clean (`systemctl restart`
  → `is-active` → `active`).
- **Titan** (`hydra-titan`, Raspberry Pi 5, Tailscale `100.103.4.38`):
  systemd service `hermes-titan.service`, active, on a dedicated release
  directory separate from the pre-existing `0.20.0` Nursery release.
  Pre-existing `hermes-nursery.service`, `hermes-nursery-health.service`
  (oneshot, confirmed `status=0/SUCCESS` after the deploy), and
  `hermes-link.service` all remained active throughout.
- **Mac worker** (this machine, Tailscale `100.68.14.37`): launchd agent
  `com.hermes.mac-worker`, `state = running`, `last exit code = 0` after a
  `launchctl kickstart -k` restart test.
- All three nodes appeared in `GET /v1/fleet/nodes` with `connection_state:
  connected` and real, non-fabricated `model_inventory` matching direct
  `ollama list` / `/api/tags` output on each machine.
- **Hydra Live**: `tailscale status` showed `hydra-live ... offline, last
  seen 1h ago` — left disconnected/unknown, not represented as healthy.

### Real governed dispatch

- `POST /v1/sigil/route` with `{"operation": "advisory_financial_sentiment"}`
  returned `outcome: "accepted"`, `routed_to: "titan"`, `model_alias:
  "sentiment"`, a real `output_reference`, and a real evidence ref
  (`model_execution_81e63763717fc01cb4d9861b`) — a real network call through
  Titan's Ollama (`qwen3:0.6b`), not a mock.
- Fail-closed proven along the way: dispatch to a revoked node was rejected
  before any network call (transport raises `AssertionError` in the test
  double if reached — never triggered); dispatch with no configured model
  alias was rejected `service_not_admitted`; dispatch to an unregistered
  caller was rejected `caller_not_admitted`.

### Real certification

- `python -m hermes_cli.prime.certification_cli --state-root
  /var/lib/hermes-prime` on Prime, with Stage 1 regression included (not
  skipped — `apps/sigil`'s real dependency, `websockets`, plus `pytest`/
  `ruff`, installed into Prime's venv), returned `status: "certified"`.
  `GET /v1/fleet/certification` confirmed the same live result over HTTP.

### Bugs found and fixed during this deployment (see commit `4a4051397`)

1. `FleetRuntime`'s default `MissionControlService` crashed under Prime's
   locked-down `User=hermes` systemd account (`FileNotFoundError:
   /nonexistent/.hermes/mission_control`) — reproduced live, fixed, and
   covered by a new regression test that simulates the unwritable-home
   condition.
2. Titan's Ollama was bound to `127.0.0.1` only — unreachable from Prime.
   Fixed via an additive systemd drop-in (`OLLAMA_HOST=100.103.4.38:11434`);
   confirmed the pre-existing Nursery health check still passed afterward,
   and that Titan's own worker health probe needed repointing to the same
   address (its `127.0.0.1` self-check broke when the bind address changed
   from a shared to an exclusive listener).
3. `certify_fleet()`'s six non-live-runtime boolean checks had no
   production implementation — verified by grepping the repository before
   writing `production_certification_selftests.py`.

### Test/build evidence

- `python -m pytest tests/hermes_cli/test_prime/` → 267 passed.
- `python -m pytest apps/sigil/tests/` → 2182 passed (240s).
- `apps/sigil-desktop`: `npx tsc --noEmit` clean; `npm test` → 101 passed;
  `npm run lint` clean; `npm run build` succeeded (includes
  `test:packaged-backend`, which re-runs the Python bridge tests against
  the staged package copy).
- `ruff check` and `.venv/bin/ty check` clean on all changed
  `hermes_cli/prime` and `apps/sigil` Python files.

### Known limitation

`sigil_routing.DEFAULT_OPERATION_ROUTES` sends 4 of 5 supported operations
to the `mac` node — the same node Sigil runs on — which
`SigilContractRequest`'s self-address validator rejects
(`caller_identity_id == service_identity_id`). Reproduced directly via
`POST /v1/sigil/route` with `advisory_valuation`, confirmed via the error
message, and documented rather than fixed by weakening the validator. See
`docs/certification/sigil-v3.7-release-notes.md`.

### Incident note

During Mac worker setup, an unfiltered `launchctl print` command exposed
real `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` values (present in the operator's
shell/launchd session environment, unrelated to anything this deployment
configured) into the terminal output. The operator was notified
immediately and advised to rotate those keys. No secret from this
deployment's own configuration (Prime's shared auth token, etc.) was ever
printed — those were retrieved and piped between machines without being
echoed to any visible output.

## Sigil 3.8.0 and later

No evidence yet — not started.
