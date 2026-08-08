# Sigil Release Roadmap

Tracks the governed release train from Sigil 3.7.0 (Fleet Unification
integration) through 4.1.0 (strategy engine) and the optional Obsidian
knowledge track. See `SIGIL_RELEASE_STATUS.json` for machine-readable
current status and `SIGIL_RELEASE_EVIDENCE.md` for verification evidence.

Program rules: one branch and PR per release, based on the latest merged
`main` after its prerequisite release merges. No release is auto-merged. No
final version tag is created until CI passes, independent review is
complete, certification is green, and the PR is merged.

## Sigil 3.7.0 — Governed Fleet Release

Prime/Titan/Mac deployment, Hydra Live where reachable, Prime-governed Sigil
routing, Mission Control fleet UI, paper-only safeguards.

- Branch: `sigil-v3.7-fleet-ui-release`
- PR: https://github.com/firecattechllc/hermes-agent/pull/69
- Status: implemented, tested, committed, pushed. Awaiting CI, independent
  review, and merge. Not tagged.

## Sigil 3.8.0 — Supabase Platform Release

Supabase Auth, invite-only onboarding, profiles, secure cloud
configuration, migrations, RLS on all exposed tables, account
recovery/deletion, dev/prod separation, monitoring, backups, rate limits,
audit records. `service_role` keys never reach the desktop/frontend.

- Branch: `sigil-v3.8-supabase-platform`
- Status: not started. Blocked on 3.7 being safely committed/pushed (done)
  and on confirming real Supabase account/project access before any
  provisioning.

## Sigil 3.9.0 — Final UI Polish

Full native macOS UI refinement: typography, spacing, icons, responsive
windows, onboarding, loading/error/offline/degraded states, keyboard
navigation, accessibility, performance, reduced motion, final Xcode visual
validation. Real backend state only — never fake healthy nodes or
telemetry.

- Branch: `sigil-v3.9-final-ui-polish`
- Status: not started. Depends on 3.8 merging first.

## Sigil 4.0.0 — Public Release Ready

Signing, entitlements, sandbox review, Xcode archive, notarization/App
Store validation, privacy policy, terms, support/account deletion, crash
reporting, security and supply-chain review, beta remediation, release
notes, launch assets, rollback plan.

- Branch: `sigil-v4.0-public-release`
- Status: not started. Depends on 3.9 merging first. Public-release
  readiness will not be claimed without successful signing, archive,
  notarization, production Supabase validation, and final certification.

## Post-4.0 — Paper-Trading Maturation

Maturity scorecards and monitoring (paper performance, drawdown,
volatility, uptime, routing/approval reliability, recovery behavior,
incidents, evidence integrity, provider/model drift, market-regime
results), built now but not used to claim time-based maturity immediately.
Broker submission and execution authority remain disabled; live trading
never activates automatically.

- Status: not started.

## Sigil 4.1.0 — Governed Strategy Engine

Modular strategy-plugin framework (metadata, required data, asset classes,
holding periods, market-regime suitability, risk limits, rejection
conditions, backtesting, paper trading, ranking, comparison, ensembles,
evidence, quarantine, retirement, scorecards). Remains paper-only. Uses
Alpaca Paper, SEC EDGAR, FRED (via a governed macro-data service), and
admitted local Ollama models through Prime. `FRED_API_KEY` read only from
the environment, never printed or committed.

- Branch: `sigil-v4.1-strategy-engine`
- Status: not started. Depends on 4.0 merging first.

## Optional Hermes Knowledge Track — Obsidian

Read-only optional Obsidian vault ingestion: safe Markdown scanning,
wikilinks, aliases, tags, Web Clipper frontmatter, incremental sync,
hashes, graph relationships, Mission Control telemetry, path containment,
symlink escape prevention, no plugin execution. Hermes functions normally
without Obsidian. A real personal vault is never inspected without
explicit configuration.

- Branch: `feature/obsidian-knowledge-integration`
- Status: not started. Independent of the Sigil release train; may be
  prepared in parallel once it cannot bypass an earlier gate.
