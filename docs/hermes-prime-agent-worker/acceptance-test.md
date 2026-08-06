# Prime Agent Worker — Acceptance Test Record

Run live against the real Titan installation (`hermes-prime-agent` account,
Prime Agent v0.7.0), using a disposable local git repository owned by that
account at `/var/lib/hermes-prime-agent/workspace/acceptance-test-repo`
(one commit, one `README.txt`). Not a mock — every result below is a real
subprocess invocation of the real installed `prime-agent` binary through
`hermes_prime_agent_worker`, or a real read of the resulting evidence
ledger.

## Results

| # | Criterion | Result |
|---|---|---|
| 1 | Inspect a tiny repository | Not reached — blocked on model routing (see below) |
| 2 | Create/modify one harmless text file | Not reached — same blocker |
| 3 | Run a deterministic local validation (`--autonomous-gate`) | Plumbing present and unit-tested; not live-exercised (no completed turn to gate) |
| 4 | Produce an evidence record | **Passed** — 14 real entries, hash chain verified intact |
| 5 | Terminate within strict limits | **Passed** — real run completed in 16.06s, well inside the 300s bound, no timeout hit |
| 6 | list/status/stop/completion lifecycle | **Mostly passed** — `status` (live daemon pid tracked across calls), `list`, `doctor`, `shutdown` all demonstrated live; `send`/`stop` are implemented and unit-tested but not live-exercised, since governed runs are ephemeral (`--no-session`) by design and never create a listable named agent |
| 7 | Access outside allowlisted workspace denied | **Passed** — `workspace_not_allowlisted`, subprocess never spawned |
| 8 | Privileged commands denied | **Passed** — `privileged_command_denied` for `sudo cat /etc/shadow`, subprocess never spawned |
| 9 | Unapproved external network denied/unavailable | **Passed** — `provider_not_active` denies by default; when active, only the loopback OmniRoute endpoint is ever reachable (`--offline` always passed; provider fixed to `titan-omniroute`) |
| 10 | No paid model usage without explicit approval | **Passed** — only `titan-omniroute` (Titan-local, free) was ever invoked; no cloud API key present in this account's environment at all |

## The one real end-to-end pipeline run

With `HERMES_PRIME_AGENT_WORKER_PROVIDER_ACTIVE=true` (test-only override,
not the deployed default), one full run was executed:

- Policy admitted the run (no denial).
- A real `prime-agent` subprocess was spawned with the full bounded argv
  (`--autonomous`, `--autonomous-max-turns 6`, `--autonomous-max-tokens
  20000`, `--offline`, `--no-tools`, `--provider titan-omniroute`).
- It made a real network call to Titan's OmniRoute service
  (`127.0.0.1:8791`), authenticated successfully (a bad token would have
  been `401`, not `422`).
- OmniRoute rejected the request with `422 invalid_request` — Prime
  Agent's OpenAI-completions client sends `content` as an array of blocks;
  OmniRoute's minimal parser only accepts a plain string. Confirmed
  independently with a raw `curl` reproduction.
- Prime Agent's own resilience layer retried 3 times (2s/4s/8s backoff),
  then gave up (`auto_retry_end success:false`) — but the CLI process
  itself still exited 0.
- **This exit-code-0-despite-failure behavior is itself a real finding**:
  it was caught live during this acceptance run, not in a mock. It meant
  the adapter's cooldown/backoff governance control would never have
  engaged on repeated model-level failures. Fixed in
  `sessions._task_succeeded()`, which inspects the JSON-lines transcript
  for `auto_retry_end success:false` / `stopReason: "error"` rather than
  trusting exit code alone. The fix itself is unit-tested (5 tests,
  including a reproduction of the exact real transcript shape observed
  here); it was not re-exercised live a second time given the ~16s+retry
  cost of each live OmniRoute round-trip.

## Kill switch drill

`emergency-stop` was invoked live: it stopped the real running daemon
(`prime-agent shutdown --force`, clean exit) and wrote a persistent
`KILL_SWITCH` file to `/var/lib/hermes-prime-agent/state/`. A subsequent
`run` call — even with `provider_active=true` — was denied with
`kill_switch_active` before any subprocess was spawned. `PrimeAgentWorker`
has no method to clear the switch; only an operator with direct
filesystem access to Titan can remove the file.

**Titan was left in this state deliberately**: kill-switched, daemon
stopped, no systemd unit installed. This is the intended resting posture
until the OmniRoute compatibility gap is fixed and a full live acceptance
pass (criteria 1–3 above) is achieved.

## What would unblock the remaining criteria

Two independently-verified, pre-existing issues in shared Titan
infrastructure (not touched by this change, not owned by this worker's
branch):

1. `hermes_cli.prime.omniroute_server._extract_last_user_message` only
   accepts `content: str`, not `content: list[dict]`. A one-line fix
   (accept and flatten a list of `{"type": "text", "text": ...}` blocks)
   would resolve this for Prime Agent and likely any other
   OpenAI-completions-shaped client.
2. Separately, OmniRoute's `lightweight` alias currently fails a fleet
   model-admission check for `gemma4:e2b-it-qat` on node `titan`,
   independent of (1).

Neither was patched as part of this change — both live on
`feat/titan-omniroute-freellmapi`, a branch this worker does not own, and
"do not modify unrelated Titan services" applied.
