# Goose Worker Adapter

Status: optional, governed, **disabled by default**.

Goose (https://github.com/block/goose) is an optional local execution
backend for Sigil's governed AI layer. It is a **worker adapter only** —
Hermes/Sigil remain the control plane and the authoritative source of
identity, admission, policy, budgets, evidence/audit, and approvals. Goose
never gains direct Sigil execution authority or fleet-administrative
authority, and it is unavailable (not merely "off") when the `goose`
executable is not installed.

## Scope and non-goals

- Goose is reachable only through `sigil.ai.goose.GooseWorkerProvider`,
  which implements the same `sigil.ai.provider.ModelProvider` protocol as
  every other local AI adapter (see `sigil/ai/mac_ollama.py` for the
  precedent this module follows).
- Goose is **not** a second control plane, a second Mission Control, or a
  second dashboard. Its status is surfaced through the existing desktop
  bridge (`sigil.desktop_bridge.goose_bridge.goose_worker_visibility`) and
  the existing `GovernedVisibilityPanel` in Sigil Desktop, alongside the
  computer-use and Hermes WebUI visibility that already lives there.
- Goose's own extension system (Computer Controller, Memory, Top Of Mind,
  remote-host control, payment tools, unrestricted MCP servers) is **never**
  auto-enabled. Every governed invocation runs with `--no-profile` and no
  `--with-extension` / `--with-builtin` flags — see
  `sigil.ai.goose._build_goose_args`. This is enforced by a fixed argument
  list, not by policy alone: even if a user's local `~/.config/goose`
  profile has extensions configured, the governed invocation path ignores
  it entirely.
- Goose does not receive credentials, SSH keys, `.env` contents, or the
  full host process environment. Its subprocess environment is built from
  an explicit allowlist (`PATH`, `HOME`, `USER` only) — see
  `sigil.ai.goose._minimal_environment`.

## Architecture

| Concern | Reused mechanism |
|---|---|
| Provider identity / health | `sigil.ai.models.ProviderIdentity`, `ProviderHealth` (same vocabulary as every other adapter) |
| Invocation / result contract | `sigil.ai.provider.ProviderInvocation` / `ProviderResult` / `ProviderFailure` |
| Evidence | `sigil.ai.evidence.build_invocation_evidence` — digest-only, no raw model output persisted |
| Audit ledger | `sigil.ai.ledger.DurableAIEvidenceLedger` — the same hash-chained, append-only ledger Mac Ollama uses |
| Authority denial | `GOOSE_GOVERNANCE_BOUNDARIES` — same field set as `mac_ollama.GOVERNANCE_BOUNDARIES`, plus an explicit `fleet_administrative_authority: False` |
| Mission Control visibility | `desktop_bridge.goose_bridge.goose_worker_visibility`, wired into the existing `desktop_bridge/runner.py` allow-list and `GovernedVisibilityPanel` |

No parallel admission/evidence/audit system was introduced. Goose's
capability is intentionally scoped to `Capability.CODING` text-completion
requests — it does not implement or receive `Capability.EMBEDDINGS`,
`ORCHESTRATION`, or any capability that would imply broader authority.

## Non-interactive invocation

Hermes dispatches Goose via `goose run` in its machine-readable JSON mode,
never the interactive TUI:

```
goose run --no-profile --no-session -q --output-format json \
  --provider <configured provider> --model <configured model> \
  --max-turns <configured bound> --max-tool-repetitions <configured bound> \
  -t <instructions>
```

- `--output-format json` — a stable, documented machine-readable contract
  (`{"messages": [...], "metadata": {"status": ..., "input_tokens": ...}}`),
  not fragile terminal-UI scraping.
- `--no-session` — no session file is created or resumed; every governed
  call is stateless.
- Arguments are always passed as an argument array to `subprocess.Popen`
  (`sigil.ai.goose.SubprocessGooseRunner`). `shell=True` is never used, so
  shell metacharacters in instructions text cannot be interpreted.
- A hard wall-clock timeout and an explicit `cancel_event` are both
  enforced by polling the subprocess and terminating it (SIGTERM, then
  SIGKILL after a grace period) on either condition.

## Configuration

All configuration is environment-driven, following the same
`from_environment()` convention as `MacOllamaProfileConfig`. Every value
defaults to the safe/disabled state.

| Variable | Default | Notes |
|---|---|---|
| `SIGIL_AI_GOOSE_ENABLED` | `false` | Must be explicitly set to enable the worker at all |
| `SIGIL_AI_GOOSE_EXECUTABLE` | `goose` | Explicit path or a bare name resolved via `PATH` |
| `SIGIL_AI_GOOSE_PROVIDER` | `ollama` | Passed to `goose run --provider` |
| `SIGIL_AI_GOOSE_MODEL` | `gemma4:12b` | Passed to `goose run --model`. Configurable — this default only applies where the repository's verified local Ollama model set (`gemma4:12b`, `gemma4:e4b`, `gemma4:e2b`, `hermes-llama3.2:3b-64k`) is appropriate |
| `SIGIL_AI_GOOSE_ALLOWED_WORKSPACES` | *(empty)* | `os.pathsep`-separated absolute paths; a requested workspace outside this allowlist is denied |
| `SIGIL_AI_GOOSE_TIMEOUT_MS` | `120000` | Hard per-invocation timeout, bounded `[1000, 600000]` |
| `SIGIL_AI_GOOSE_MAX_TURNS` | `10` | `--max-turns` |
| `SIGIL_AI_GOOSE_MAX_TOOL_REPETITIONS` | `3` | `--max-tool-repetitions` |
| `SIGIL_AI_GOOSE_MAX_OUTPUT_BYTES` | `200000` | Model output is truncated (and flagged `truncated: true`) beyond this bound |
| `SIGIL_AI_GOOSE_MAX_CONCURRENT_JOBS` | `1` | Non-blocking concurrency gate; a job over the limit fails closed rather than queueing |

The displayed Goose context window (marketed as up to 128k) is **not**
assumed to reflect what a given local Ollama model/runtime actually
supports — model capability is governed entirely by the existing
`SIGIL_AI_GOOSE_MODEL` selection and Ollama's own model configuration, not
by anything Goose reports about itself.

## Enabling Goose

1. Install Goose locally (`https://github.com/block/goose`) and confirm
   `goose --version` runs.
2. Confirm a local Ollama model is available (`ollama list`).
3. Set `SIGIL_AI_GOOSE_ENABLED=true` plus any overrides above.
4. Confirm readiness via Mission Control's "Goose worker" panel, or the
   desktop bridge `goose_worker_visibility` command directly — it reports
   `installed`, `version`, `health` (`healthy` / `unavailable` / `disabled`),
   `active_jobs`, and the `last_execution` outcome.

## Disabling Goose

Unset `SIGIL_AI_GOOSE_ENABLED` (or set it to `false`). No other Hermes
worker (Codex, Claude Code, Ollama-backed providers) is affected — each is
independently configured and none of their code paths were changed by
this adapter.

## Troubleshooting

| Symptom | Cause | Resolution |
|---|---|---|
| `health: "disabled"` | `SIGIL_AI_GOOSE_ENABLED` is unset/false | Set it to `true` |
| `health: "unavailable"`, `reason: "executable_not_found"` | `goose` is not on `PATH` and no explicit `SIGIL_AI_GOOSE_EXECUTABLE` path resolves | Install Goose or set an explicit path |
| `health: "unavailable"`, `reason: "version_probe_failed"` | `goose --version` failed or timed out | Run `goose --version` manually to diagnose (permissions, corrupted install) |
| Invocation fails with `provider_unavailable` and a run did not complete message | Goose hit `--max-turns` or otherwise did not reach a `"completed"` status | Increase `SIGIL_AI_GOOSE_MAX_TURNS`, or the task genuinely needs more turns than governance allows |
| Invocation fails with `provider_unavailable` and a redacted exit message | Non-zero exit — usually the configured local model/provider (e.g. Ollama) is unreachable | Confirm `ollama serve` is running and the model in `SIGIL_AI_GOOSE_MODEL` is pulled |
| `workspace denied` | The requested workspace path is outside `SIGIL_AI_GOOSE_ALLOWED_WORKSPACES` | Add the directory to the allowlist, or omit `workspace` for text-only tasks |

## Dependencies

Goose is the only new optional dependency introduced by this adapter. It
does not require, install, enable, or reference Buzz, Buzznode, or
OpenJarvis in any way — those remain separate, independently
disabled-by-default ecosystem adapters (`sigil.buzz_relay_adapter`,
`sigil.buzznode_adapter`) unrelated to this change; `OpenJarvis` does not
appear anywhere in this codebase.

## Known limitations / unresolved risk

- Goose's own permission/extension system was intentionally bypassed
  entirely (`--no-profile`, no extension flags) rather than mapped 1:1
  against Sigil's `AuthorityDenials` vocabulary. If a future revision needs
  Goose to use *any* extension (even a narrowly-scoped one), that is new
  governed surface area requiring its own review — it is out of scope here
  and nothing in this change enables it.
- Concurrency and output-size enforcement are process-local (in-memory);
  they do not survive a Hermes process restart and are not yet integrated
  with the cross-node `hermes_cli/prime` fleet admission/budget system,
  consistent with how `mac_ollama.py` scopes itself to local, in-process
  advisory inference only (see `OLLAMA_ROUTING_BOUNDARY.md`).
