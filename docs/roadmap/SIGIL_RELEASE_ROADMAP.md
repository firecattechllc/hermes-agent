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

Prime/Titan/Mac deployment, Prime-governed Sigil routing, Mission Control
fleet UI, and paper-only safeguards. Hydra Live was part of the historical
3.7 evidence but is now retired from active fleet expectations.

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

- Branch: `sigil-v4.0-public-release` was never created. Instead, a native
  macOS 4.0.0 build (Swift/Xcode, replacing the earlier Electron app) was
  developed on `sigil/4.0-native-macos`, partially merged to `sigil-alpha`
  via PR #75/#76 (tag `v4.0.0`), then extended with additional commits on
  that branch (the Hermes Bridge XPC service, Agent Watch improvements)
  that were never merged back — meaning the actually-signed-and-installed
  `/Applications/Sigil.app` v4.0.0 could not be reproduced from `sigil-alpha`
  or from the `v4.0.0` tag alone. This gap was found during a 2026-08-12
  certification audit and closed by merging `sigil/4.0-native-macos` into
  `sigil-alpha` (see evidence below); `sigil-alpha` is now the single
  canonical source capable of reproducing the shipped app's feature set.
- Status: shipped (installed, signed, notarized) but **not formally
  certified**. The 2026-08-12 audit found trading-safety and governance
  strong (no live-trading path exists, kill switch and paper/live
  separation verified), but no CI-driven signing/notarization pipeline
  exists for `apps/sigil-macos` — v4.0.0 was built via manual Xcode steps,
  not a repeatable process — and several P1 findings remain open. See
  `Sigil-4.0-Certification-Report-2026-08-12.md` (Desktop) for full detail.
  Do not claim public-release readiness until that pipeline exists and the
  app is rebuilt/re-signed/re-notarized from the reconciled `sigil-alpha`
  source.

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
