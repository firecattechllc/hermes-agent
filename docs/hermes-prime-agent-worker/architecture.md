# Prime Agent Worker Adapter — Architecture

Status: installed and native-validated on Titan. Model routing is
provisioned but blocked (see "Model routing" below). Not yet enabled for
autonomous scheduling.

Prime Agent (https://github.com/PrimeIntellect-ai/prime-agent) is a
third-party, MIT-licensed "self-improving RLM agent for coding workflows
and long-running autonomous tasks." It is a **worker only**. Hermes remains
the authority for admission, budgets, approvals, evidence, and shutdown —
this package never lets Prime Agent decide those things for itself. Prime
Agent's own worker/kernel process split is lifecycle isolation for
crash-recovery, **not** a security sandbox — see its own README, quoted
directly: "process isolation... [is] not a security sandbox."

## Scope and non-goals

- Reachable only through `hermes_prime_agent_worker.sessions.PrimeAgentWorker`.
  Every operation goes through `policy.py` first; a denial never reaches a
  subprocess.
- Not a second control plane or a second Mission Control. Evidence is
  local and hash-chained (`hermes_prime_agent_worker.evidence`); Mission
  Control / fleet integration is intentionally not wired up yet (Prime
  Agent is Titan-local, not a fleet admission participant like Hydra
  Prime — see the naming note below).
- No unrestricted autonomous execution. Every bounded run passes
  `--autonomous-max-turns`, `--autonomous-max-tokens`,
  `--autonomous-timeout-ms`, and (optionally) `--autonomous-gate` — Prime
  Agent's own CLI-level bounds, enforced in addition to (not instead of)
  this package's wall-clock `proc.py` timeout.
- Default read-only: a run with no `requested_tools` is invoked with
  `--no-tools`. Mutation tools require `--approve-mutation` and must be a
  member of the configured `mutation_tools` allowlist.

## Naming collision warning

`hermes_cli/prime/*` in this repository implements **Hydra Prime**, the
fleet identity/admission/routing control-plane node — unrelated to this
third-party CLI tool. Env vars here use the `HERMES_PRIME_AGENT_WORKER_*`
prefix specifically to avoid colliding with `HERMES_PRIME_*`, which Hydra
Prime already owns. Titan runs both; do not confuse them operationally.

Separately: Prime Agent must not be confused with the unrelated Raspberry
Pi machine named "Prime" elsewhere in this fleet.

## Module map

| Module | Responsibility |
|---|---|
| `config.py` | Frozen, env-driven, fail-closed configuration. Local copy of the Mac-dependency guard (same convention as `hermes_docs_worker.config` — see its docstring for why it's a local copy, not an import). |
| `proc.py` | The only place a `prime-agent` subprocess is started. Argv-only, minimal explicit environment allowlist, hard wall-clock timeout with SIGTERM→SIGKILL escalation, bounded output capture. |
| `policy.py` | All governance decisions. Closed reason-code vocabulary; every denial accumulates every applicable reason rather than short-circuiting. |
| `evidence.py` | Self-contained, hash-chained, `flock`-protected, retention-bounded JSONL evidence store. |
| `status.py` | Parses `prime-agent status`/`doctor` JSON into a closed health vocabulary; malformed input collapses to the most conservative reading rather than raising. |
| `sessions.py` | Orchestrates policy → subprocess → evidence, in that order, for every operation. The only module allowed to call `proc.py`. |
| `cli.py` / `__main__.py` | `python -m hermes_prime_agent_worker <command>`. |

## Installation (Titan)

- Dedicated unprivileged system account `hermes-prime-agent` (uid 996,
  own primary group, `nologin` shell, no supplementary groups — not in
  `sudo`, `docker`, `adm`, `shadow`, or `systemd-journal`).
- Prime Agent itself installed via the official installer
  (`https://app.primeintellect.ai/prime-agent/install.sh`, downloaded to a
  file and checksum-verified before execution, TLS certificate confirmed
  for `app.primeintellect.ai`) against a **private Node.js 22.23.2**
  runtime provisioned under `/var/lib/hermes-prime-agent/.local/share/prime-agent-node`
  — Titan's system Node (20.19.2, used by other services) was never
  touched. Version installed: **v0.7.0**, upstream commit
  `be9e2fa0714e7cd1c6bd9bdb1b554d2cc6550387`.
- Workspace/state/cache/log directories under `/var/lib/hermes-prime-agent/`,
  each `0700`, owned by the dedicated account.

## Model routing

Prime Agent supports custom OpenAI-compatible providers via
`~/.prime/agent/models.json`. Titan's governed OmniRoute service
(`hermes_cli.prime.omniroute_server`, on branch
`feat/titan-omniroute-freellmapi`) already exposes exactly this shape at
`http://127.0.0.1:8791/v1/chat/completions` — a real, currently-running,
bearer-authenticated, alias-only (never raw provider/model) endpoint.

`models.json` was provisioned to point at it (`titan-omniroute` provider,
aliases `lightweight`/`large`/`embedding`), with the bearer token read
directly from `/etc/hermes-prime/omniroute.env` on Titan and written with
`0600` permissions — the token was never printed to a terminal transcript
or committed anywhere.

**Provider activation is currently blocked** (`HERMES_PRIME_AGENT_WORKER_PROVIDER_ACTIVE=false`)
for two independently-verified, pre-existing reasons unrelated to this
adapter:

1. Prime Agent's OpenAI-completions client sends `content` as an array of
   content blocks; OmniRoute's minimal stdlib-only parser
   (`_extract_last_user_message`) only accepts a plain string, so every
   request currently fails with `422 invalid_request` before reaching a
   model. Confirmed by a direct `curl` reproduction with plain-string
   content, which does progress past this check.
2. Even bypassing (1) with a raw request, OmniRoute's `lightweight` alias
   currently fails a separate fleet model-admission gate — "no admitted
   model is configured for alias 'gemma4:e2b-it-qat' on node 'titan'" —
   independent of Prime Agent entirely.

Both are pre-existing issues in shared Titan infrastructure on a branch
this worker does not own (`feat/titan-omniroute-freellmapi`) and were left
unmodified rather than patched out-of-scope. Per the explicit fallback
instruction for this build: the harness is installed and validated, but
provider activation stays blocked with this documented reason rather than
falling back to a paid cloud provider or bypassing OmniRoute's governance
by calling Titan's Ollama directly.

## Service lifecycle

Prime Agent's own daemon **self-forks and detaches** from its invoking
process on first use — confirmed directly: after one `-p` invocation
returned, its daemon (two node processes, ~228MB RSS combined, <1% idle
CPU) kept running independently, and `prime-agent shutdown --force`
stopped it cleanly with no orphaned processes. This means the native
daemon is sufficient on its own; it is not a foreground process a
`Type=simple` systemd unit could correctly supervise, and wrapping it in
one would fight its own lifecycle management for no governance benefit.

The one systemd unit provided
(`deploy/titan/systemd/hermes-prime-agent-worker-doctor.service` +
`.timer`) runs `prime-agent doctor` (read-only; `--fix` is never passed)
every 15 minutes so a wedged or orphaned local daemon state is visible in
logs/evidence even when nothing has dispatched a task recently. It is
fully hardened (`NoNewPrivileges`, `ProtectSystem=strict`,
`ReadWritePaths` scoped to `/var/lib/hermes-prime-agent` only, capability
bounding set cleared, `MemoryMax=512M`/`CPUQuota=50%`/`TasksMax=16`) and
runs as the dedicated unprivileged account.

Actual bounded task dispatch happens through Hermes calling
`python -m hermes_prime_agent_worker run ...` directly through its own
scheduling/admission path — there is deliberately no self-scheduling
inside this package (see the "no self-modification of governance policy"
boundary below).

**This systemd unit is not yet installed or enabled on Titan.** Per the
explicit instruction to not enable autonomous schedules until the
governed acceptance test passes, and given end-to-end model routing is
currently blocked, installing/enabling it is left for a follow-up once
(a) OmniRoute's compatibility gap is fixed upstream/on its own branch and
(b) a live end-to-end acceptance run has actually passed.

## Governance boundaries enforced structurally (not just by policy)

- **No git mutation capability anywhere in this package.** `policy.py`'s
  `is_git_mutation_command` denies task/gate text referencing
  `merge`/`tag`/`push`/`commit`/`rebase`/`release`, and
  `sessions.PrimeAgentWorker` has no method named anything resembling
  one — enforced by `tests/hermes_prime_agent_worker/test_structural_absence.py`,
  which fails loudly if such a method is ever added.
- **No self-unit-mutation capability.** `policy.py` denies any task/gate
  text referencing this worker's own systemd unit name.
- **No privilege escalation capability.** The dedicated account has no
  sudo/docker/adm/shadow/systemd-journal group membership; `policy.py`
  additionally denies task text referencing `sudo`, `su`, `passwd`,
  `systemctl enable/edit`, `usermod`, etc. as defense in depth (the
  account genuinely cannot do these things regardless).
- **Kill switch, not just a policy flag.** `emergency_stop()` writes a
  file (`state_dir/KILL_SWITCH`) that `policy.py` checks before any run;
  `PrimeAgentWorker` has no method to clear it — only an operator with
  direct filesystem access can remove the file. A compromised or
  malfunctioning caller of this class can trip the switch but never
  un-trip it.
- **`doctor --fix` requires separately-tracked approval.** `--fix` is
  never invoked implicitly; `policy.evaluate_doctor_fix` denies it unless
  the caller passes an explicit `fix_approved=True` distinct from just
  requesting it.

## Testing levels — what "tested" means at each layer

Per the explicit requirement to distinguish these:

- **Installed and native-validated**: yes — `prime-agent --version`,
  `--help`, `status`, `doctor`, install/idle-resource/shutdown behavior
  all verified directly on Titan.
- **Provider-authenticated**: no interactive OAuth login was performed
  (by design — this requires a human running `/login` interactively; see
  the installation runbook). The OmniRoute custom-provider path does not
  require OAuth, and was configured, but see "Model routing" above for
  why it's not yet functional end-to-end.
- **Local-model-routed**: blocked, documented reason above. Not achieved
  this pass.
- **Hermes-adapter-tested**: yes — 96 tests in
  `tests/hermes_prime_agent_worker/`, all running real subprocesses
  against a fake `prime-agent` executable (not mocked at the Python
  level), covering config validation, policy denial accumulation, argv/
  environment safety, timeout/output-truncation behavior, evidence
  hash-chain integrity and tamper detection, status parsing, session
  orchestration (including cooldown/kill-switch), systemd unit hardening,
  and structural absence of forbidden capabilities.
- **Live end-to-end accepted**: not achieved this pass — blocked on model
  routing (above). See `docs/hermes-prime-agent-worker/acceptance-test.md`
  for exactly what was and was not exercised against the real Titan
  installation.
