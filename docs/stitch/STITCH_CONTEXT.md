# Sigil — Google Stitch Project Context

## 1. Product identity

Sigil is a governed AI-assisted market research and paper-trading desktop application. It is designed as an operator-controlled financial intelligence system, not an autonomous brokerage bot.

The product combines:

- market-universe discovery and live read-only quotes;
- news and research intelligence;
- deterministic portfolio and financial calculations;
- AI-generated proposals with evidence and risk context;
- explicit operator authorization;
- a governed paper runtime;
- persistent audit evidence and restart recovery.

The Beta redesign should make Sigil feel premium, calm, precise, fast, and trustworthy. The visual standard is closer to a high-end cockpit or luxury vehicle dashboard than a noisy retail trading terminal.

## 2. Current lifecycle

Sigil Alpha is in final manual certification. Beta begins only after Alpha functionality, safety, packaging, recovery, and button behavior are frozen and certified.

This handoff must therefore preserve behavior. Stitch is being used to redesign the body of the application, not its authority model or runtime semantics.

## 3. Primary operator journey

The complete governed path is:

```text
Launch Sigil
→ load persisted runtime snapshot
→ authenticate market data
→ start or verify read-only market services
→ search the market universe
→ inspect current quote freshness and session status
→ receive news and research evidence
→ generate an investment proposal
→ review thesis, risks, confidence, sizing, and evidence
→ approve or reject the proposal
→ authorize a paper action when required
→ execute a governed paper cycle
→ update positions, cash, and P&L
→ persist audit evidence
→ restart the application
→ recover the same governed state
```

The UI must make the current step, available next action, blocked actions, and reason for every block obvious.

## 4. Non-negotiable safety invariants

These requirements override visual preferences:

1. **Paper-only execution** — The certified Alpha runtime is paper-only.
2. **Broker submission disabled** — No design may imply that live broker submission is available.
3. **Explicit authority** — AI output is a proposal, not permission to execute.
4. **Governed actions** — Sensitive actions must remain tied to confirmations and authorization state.
5. **Fail closed** — Missing providers, invalid configuration, stale state, authentication failure, or unsupported conditions must disable dependent actions.
6. **Visible unavailability** — Disabled controls must explain why they are unavailable.
7. **No fake functionality** — Placeholder, simulated, or future controls must never appear operational.
8. **Quote truthfulness** — Quote source, age, stale state, market session, and after-hours state must remain visible.
9. **Auditability** — Material actions and state transitions must produce or preserve audit evidence.
10. **Recovery fidelity** — Restarted UI state must reflect persisted governed state rather than optimistic local assumptions.
11. **No hidden side effects** — A visual interaction must not silently trigger a materially different backend action.
12. **Exact financial meaning** — Cash, quantity, price, P&L, and valuation data must retain deterministic precision and clear units.

## 5. Information architecture

### Mission Control

The main operational dashboard. It should answer, at a glance:

- Is Sigil healthy?
- Is the runtime loaded?
- Is market data authenticated and fresh?
- Is automation stopped or running?
- Are actions proposal-only?
- What requires operator attention?
- What happened most recently?

### Market Universe

Search and discovery surface for symbols. Current certified behavior includes:

- automatic debounced search;
- exact ticker prioritization;
- governed market quote bridge;
- live read-only Alpaca IEX quotes;
- price and change display;
- quote source and quote age;
- stale and after-hours indication;
- periodic quote refresh.

### Research and Intelligence

Evidence-centered workspace for news, research, signals, and model output. The UI should distinguish:

- source facts;
- deterministic calculations;
- model interpretation;
- confidence or uncertainty;
- missing evidence;
- freshness.

### Proposal Review

A decision workspace containing the proposal, symbol, amount or sizing, recommendation, confidence, thesis, bull case, bear case, key risks, evidence, paper action, approval status, and timestamps.

Approval and rejection must be deliberate and visibly recorded.

### Paper Runtime

Operational state for paper cycles, including:

- automation state;
- cycle count;
- proposal-only state;
- current positions;
- cash;
- P&L;
- latest runtime event;
- last persisted snapshot;
- authorization state.

### Portfolio

A clear view of positions, quantities, current values, cost basis where available, allocation, cash, and P&L. Data should be legible without becoming visually noisy.

### Audit and Evidence

Chronological, filterable records of governed events. Each important event should expose:

- event type;
- timestamp;
- actor or authority source;
- affected object;
- resulting state;
- evidence identifier;
- errors or warnings;
- paper-only and broker-submission status when relevant.

### Settings and Providers

Configuration and health surface for local runtime, market-data authentication, provider availability, API credentials, model routing, and packaged backend status. Secrets must never be displayed in full.

## 6. State model the UI must represent

Every relevant screen should support these states where applicable:

- loading;
- ready;
- empty but valid;
- unavailable;
- blocked with reason;
- authentication required;
- authentication failed;
- degraded;
- stale;
- disconnected;
- reconnecting;
- success;
- rejected;
- pending authorization;
- authorized;
- executing;
- persisted;
- recovered after restart;
- error with safe retry.

Do not rely on color alone. Use text, icons, labels, and accessible status semantics.

## 7. Visual direction

### Desired feeling

- premium and composed;
- highly legible;
- trustworthy rather than flashy;
- dense enough for serious operation, but never cluttered;
- smooth and responsive;
- consistent hierarchy;
- strong attention states without alarm fatigue.

### Recommended design language

- restrained dark-first desktop interface;
- generous spacing around major decision areas;
- compact data tables with excellent alignment;
- strong typography for numbers and state;
- soft depth and carefully limited glass effects;
- subtle motion for state changes and navigation;
- persistent global health and environment indicators;
- focused use of charts only when they improve a decision.

### Avoid

- casino-style green/red overload;
- excessive gradients or glow;
- unexplained icon-only controls;
- tiny low-contrast metadata;
- decorative charts without decision value;
- hidden actions inside ambiguous menus;
- mobile-first layouts stretched onto desktop;
- fake live-trading language.

## 8. Navigation principles

- Mission Control is the home surface.
- The environment and paper-only status should remain globally visible.
- The user should reach any active proposal, authorization request, runtime warning, or failed provider quickly.
- Preserve context when moving between a symbol, its research, proposal, and resulting paper position.
- Back navigation must not discard governed state.
- Destructive or authority-changing actions need explicit confirmation.

## 9. Component principles

Reusable components should include:

- global environment badge;
- runtime health summary;
- provider status row;
- quote card with source, age, and session state;
- symbol search result;
- governed action button with disabled reason;
- confirmation dialog;
- proposal summary;
- evidence/source card;
- risk list;
- authorization panel;
- position table;
- cash and P&L metrics;
- audit event row;
- stale-data warning;
- reconnect state;
- empty state with truthful next action;
- error panel with safe retry.

## 10. Accessibility and interaction

- Full keyboard navigation for primary workflows.
- Visible focus indicators.
- Sufficient contrast for text, statuses, and charts.
- Tooltips supplement labels; they do not replace critical labels.
- Every disabled action exposes a readable reason.
- Confirmation dialogs identify the exact action and consequence.
- Numeric values use stable alignment and formatting.
- Live updates should not unexpectedly steal focus or reorder content under the pointer.
- Motion should respect reduced-motion preferences.

## 11. Integration boundaries

The generated frontend must integrate with the existing Electron desktop and governed backend bridge. It must not:

- replace backend truth with local mock state;
- directly call broker endpoints;
- persist authority only in renderer memory;
- duplicate runtime handlers;
- bypass the preload/bridge boundary;
- assume successful authentication;
- conceal packaged backend failures;
- introduce a new execution path.

Generated mocks are acceptable only during visual exploration and must be clearly isolated from production integration.

## 12. Stitch prompt guidance

When generating designs, treat this file and `sigil-ui-manifest.json` as hard product context.

Recommended first prompt:

> Design a premium dark desktop Mission Control interface for Sigil, a governed AI-assisted market research and paper-trading application. Preserve every safety invariant, represent blocked and stale states explicitly, keep paper-only status globally visible, and prioritize operational clarity over decorative trading visuals. Use the supplied manifest as the authoritative screen and action inventory.

Generate one workflow at a time rather than asking Stitch to redesign the entire application in a single pass.

## 13. Required screenshot set

Before the first production-oriented Stitch pass, add screenshots for:

- Mission Control ready state;
- initial/loading state;
- market search with exact ticker result;
- fresh quote;
- stale or after-hours quote;
- proposal pending review;
- proposal approved;
- proposal rejected;
- paper authorization required;
- paper runtime stopped;
- paper runtime active or cycling;
- portfolio with positions;
- audit evidence view;
- provider authentication failure;
- packaged backend failure;
- restart recovery state;
- every modal and confirmation;
- every disabled control with its explanation.

## 14. Definition of successful Beta integration

The redesign succeeds when:

- every certified Alpha control still maps to the same governed action;
- no new authority is introduced;
- all blocked states remain explainable;
- paper-only and broker-disabled boundaries remain unmistakable;
- all runtime workflows pass end to end;
- persisted state survives restart;
- automated parity tests pass;
- packaged desktop certification passes;
- the interface feels substantially more polished without changing financial meaning.
