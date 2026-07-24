# Governed Durable Execution Journal

## Purpose

Sigil Step 10 supplies the durable boundary deferred by Step 9B. It records the
governed lifecycle of one Public equity or ETF proposal as immutable local files so
an operator can determine what was proposed, safety-checked, approved, intended,
acknowledged, reconciled, cancelled, rejected, or quarantined after interruption.

The journal does not grant trading authority. Step 9B remains the only mutation
workflow and continues to require its finite execution policy, exact preflight, and
single-use human approval.

## Trust boundary

`DurableExecutionJournal` accepts an absolute, existing, caller-supplied directory.
There is no home-directory, environment-variable, or hidden global default. The
directory must not be a symlink. Execution identities are bounded safe identifiers;
path separators, traversal components, symlinked entries, non-file records, and
unexpected repository files fail closed.

The journal stores protected account bindings, not raw account identifiers. It never
stores Public access tokens, authorization headers, credentials, cookies, private
keys, or arbitrary transport material. Payload keys that could carry secrets are
rejected. Diagnostics are restricted to bounded, non-secret codes and digests.

## Append-only format and integrity chain

Each execution has a directory beneath `executions/`. Each committed record is
canonical JSON followed by a newline and is named from its eight-digit sequence and
SHA-256 entry hash. Filename information is only an additional check; validation uses
the record content.

Every record binds:

- journal version
- proposal-derived execution identity
- sequence number
- governed event type
- canonical payload
- previous entry hash
- current SHA-256 entry hash
- timezone-aware creation timestamp

The canonical payload carries the immutable order terms and the applicable account,
proposal, portfolio, preflight, approval, client-order, provider-order, correlation,
and response identities. Audit validates the complete chain and rejects missing,
duplicate, or reordered sequences; broken links; modified content; unsupported
versions; cross-execution injection; filename mismatch; and impossible transitions.
There is no silent repair or deletion of history.

## State machine

The closed event enum covers proposal creation, portfolio binding, preflight binding,
approval consumption, submission intent, broker acknowledgement, ambiguous
submission, provider-order association, reconciliation attempts/results, terminal
status, cancellation proposal/approval/intent, cancellation acknowledgement or
ambiguity, cancellation reconciliation, permanent rejection, and quarantine.

The predecessor allowlist is deterministic. It rejects acknowledgement before intent,
approval before proposal and preflight, duplicate submission intent, mutation after a
terminal event, cancellation without a cancellation approval, changed order terms,
cross-account injection, and reuse of a consumed mutation approval. Callers cannot
append arbitrary event names.

## Atomicity, durability, and concurrency

Writers hold a POSIX advisory exclusive lock for chain validation, capacity checking,
and append. A record is written to a same-directory temporary file, flushed and
`fsync`ed, hard-linked to its immutable final name, and followed by directory
`fsync`. New execution directories and the repository records directory are also
directory-synchronized.

A crash before the immutable link leaves no committed record. A committed record must
be complete canonical JSON with a trailing newline and a valid hash. Truncated
temporary files are not journal records. Unexpected pending files are treated as
corruption rather than repaired.

An exact repeat of the current canonical event is idempotent and returns the committed
event. A same-position event with different content is a conflict. Locking prevents
two processes from independently committing the same next sequence; no writer is
chosen silently.

Configured bounds cap bytes per record, records per execution, and total repository
records. Capacity exhaustion fails before broker mutation.

## Recovery classifications

Read-only inspection classifies each execution as:

- `complete`
- `rejected`
- `safely_retryable_before_submission`
- `reconciliation_required`
- `cancellation_reconciliation_required`
- `quarantined`
- `corrupt`

Inspection and audit read files only. They never authenticate, submit, cancel,
replace, or call a broker.

## Ambiguous submission reconciliation

Submission intent is committed before the outbound mutation and binds the Step 9B
UUID client order ID and exact body digest. A transport ambiguity is a distinct
immutable event, not a rejection. Recovery requires an exact provider status lookup
using the original client order ID. Each lookup attempt and result is appended.

No replacement order is created automatically. A second submission remains forbidden
until governed reconciliation proves that the original order does not exist; any
allowed operator-directed retry must reuse the exact original UUID and body.

## Cancellation recovery

Cancellation has its own proposal and single-use approval. Cancellation intent is
committed before the DELETE mutation. An ambiguous cancellation is classified
separately and must be reconciled before any further cancellation mutation.
Reconciliation preserves the order/cancellation identity and approval binding. A
genuinely new cancellation action requires a new valid approval when Step 9B permits
one.

## Corruption and quarantine

Audit reports a corrupt classification when record bytes, schema, identity, filename,
hash, link, order, or transition fail validation. Repository-shape violations fail
construction. The journal does not truncate, rewrite, delete, or synthesize records.
Operators must preserve the repository, stop broker mutation, investigate against
broker records, and use a separately governed process if quarantine evidence must be
added.

## Operator responsibilities

Operators must:

1. provision and permission the explicit local directory;
2. keep it on storage with reliable file and directory synchronization semantics;
3. run read-only audit before recovery;
4. stop mutations on corruption, ambiguity, capacity exhaustion, or durability error;
5. reconcile against the exact provider account and original UUID;
6. protect backups without rewriting historical records; and
7. retain final authority over approvals, broker mutations, releases, and deployment.

## Limitations and explicit non-capabilities

Step 10 does not schedule or enable unattended trading; create approvals; persist
credentials; expose a generic broker or HTTP API; replace orders; add margin, shorts,
options, crypto, bonds, recurring orders, or extended-hours trading; use hosted MCP
trading; write Hermes evidence or knowledge-graph stores; deploy; or start services.

Before production or unattended trading, operators still need an approved deployment
and backup design, storage/filesystem qualification, access-control review, operational
monitoring, retention and disaster-recovery procedures, broker-specific reconciliation
certification, incident runbooks, and independent security and compliance approval.
Unattended trading remains explicitly unsupported.
