import { useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SearchField } from '@/components/ui/search-field'

import { formatEasternDateTime } from '../lib/date-time'

import type { ExecutionReceipt, Proposal, SigilSnapshot } from './types'

// The desktop adapter falls back to this synthetic reference (see
// desktop-adapter.ts mapRuntime) when a fill has no matching entry in the
// backend's `reconciliation` list -- a real, reachable gap (e.g.
// simulate_paper_fill never inserts one). That prefix is the only signal
// available to tell "no reconciliation record was ever written" apart from
// "reconciled with a real evidence reference," so it doubles as our marker.
const SYNTHETIC_REFERENCE_PREFIX = 'PAPER-RUNTIME:'

type ReconciliationState = 'awaiting' | 'reconciled' | 'required'

function classifyReceipt(receipt: ExecutionReceipt): ReconciliationState {
  if (receipt.reconciliationRequired) {
    return 'required'
  }

  if (receipt.reconciliationReference.startsWith(SYNTHETIC_REFERENCE_PREFIX)) {
    return 'awaiting'
  }

  return 'reconciled'
}

const STATE_LABEL: Record<ReconciliationState, string> = {
  awaiting: 'Awaiting record',
  reconciled: 'Reconciled',
  required: 'Required'
}

const STATE_BADGE_VARIANT: Record<ReconciliationState, React.ComponentProps<typeof Badge>['variant']> = {
  awaiting: 'warn',
  reconciled: 'default',
  required: 'destructive'
}

function StateBadge({ state }: { state: ReconciliationState }) {
  return <Badge variant={STATE_BADGE_VARIANT[state]}>{STATE_LABEL[state]}</Badge>
}

export function ReconciliationPanel({
  onOpenAudit,
  onRefresh,
  snapshot
}: {
  onOpenAudit: () => void
  onRefresh: () => void
  snapshot: SigilSnapshot
}): React.JSX.Element {
  const [query, setQuery] = useState('')

  const rows = useMemo(
    () => snapshot.receipts.map(receipt => ({ receipt, state: classifyReceipt(receipt) })),
    [snapshot.receipts]
  )

  const executedProposalIds = useMemo(
    () => new Set(snapshot.receipts.map(receipt => receipt.proposalId)),
    [snapshot.receipts]
  )

  // desktop-adapter.ts's proposalId join is a best-effort heuristic (it only
  // strips a "PAPER-ORD-" prefix, so it silently misses "PAPER-PROD-ORD-*"
  // order ids and falls back to '-'). Evidence reference is the value both
  // sides genuinely share -- runtime.py sets a proposal's evidence_identity
  // as both its own evidence_references entry and the resulting fill's
  // reconciliation evidence_reference -- so checking it too avoids treating
  // an already-executed proposal as a false-positive gap.
  const executedEvidenceReferences = useMemo(
    () => new Set(snapshot.receipts.map(receipt => receipt.reconciliationReference)),
    [snapshot.receipts]
  )

  // Real join across two already-present datasets: an approved proposal with
  // no receipt sharing its id or evidence is governed activity Sigil expected
  // to execute but has no recorded execution/evidence for -- a genuine
  // reconciliation gap, not a fabricated one.
  const awaitingProposals = useMemo(
    () =>
      snapshot.proposals.filter(
        proposal =>
          proposal.status === 'approved' &&
          !executedProposalIds.has(proposal.id) &&
          !proposal.evidenceReferences.some(reference => executedEvidenceReferences.has(reference))
      ),
    [snapshot.proposals, executedProposalIds, executedEvidenceReferences]
  )

  if (snapshot.dataState === 'loading') {
    return (
      <div className="grid min-h-48 place-items-center">
        <Loader label="Loading reconciliation state" />
      </div>
    )
  }

  const normalizedQuery = query.trim().toLowerCase()

  const filteredRows = rows.filter(({ receipt, state }) =>
    [receipt.symbol, receipt.orderId, receipt.proposalId, receipt.reconciliationReference, STATE_LABEL[state]]
      .join(' ')
      .toLowerCase()
      .includes(normalizedQuery)
  )

  const filteredAwaiting = awaitingProposals.filter(proposal =>
    [proposal.symbol, proposal.id, proposal.strategy].join(' ').toLowerCase().includes(normalizedQuery)
  )

  const summary = {
    awaiting: rows.filter(row => row.state === 'awaiting').length + awaitingProposals.length,
    reconciled: rows.filter(row => row.state === 'reconciled').length,
    required: rows.filter(row => row.state === 'required').length
  }

  const isStale =
    snapshot.dataState === 'stale' || snapshot.dataState === 'disconnected' || snapshot.dataState === 'empty'

  const nothingToReconcile = rows.length === 0 && awaitingProposals.length === 0

  const noMatchesForQuery =
    !nothingToReconcile && normalizedQuery.length > 0 && filteredRows.length === 0 && filteredAwaiting.length === 0

  return (
    <div data-testid="reconciliation-panel">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Execution reconciliation</h2>
          <p className="mt-1 text-xs text-(--ui-text-tertiary)">
            Compares governed execution receipts against recorded reconciliation and evidence state.
          </p>
        </div>
        <Button onClick={onRefresh} size="xs" variant="outline">
          <Codicon name="refresh" />
          Refresh
        </Button>
      </div>

      {isStale ? (
        <ErrorBanner className="mb-4">
          Reconciliation state may be stale — last verified {formatEasternDateTime(snapshot.lastUpdated)}.
        </ErrorBanner>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-2" data-testid="reconciliation-summary">
        <Badge variant={STATE_BADGE_VARIANT.reconciled}>{summary.reconciled} reconciled</Badge>
        <Badge variant={STATE_BADGE_VARIANT.required}>{summary.required} required</Badge>
        <Badge variant={STATE_BADGE_VARIANT.awaiting}>{summary.awaiting} awaiting record</Badge>
      </div>

      {nothingToReconcile ? (
        <EmptyState
          description="No governed executions or approved proposals are pending reconciliation."
          title="Nothing to reconcile"
        />
      ) : (
        <>
          <SearchField
            aria-label="Filter reconciliation records"
            containerClassName="mb-3"
            onChange={setQuery}
            placeholder="Filter by symbol, order, or status"
            value={query}
          />

          {noMatchesForQuery ? (
            <EmptyState description="Try a different symbol, order, or status filter." title="No matching records" />
          ) : (
            <div className="flex flex-col gap-6">
              {filteredRows.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[56rem] text-left text-xs">
                    <thead className="text-[0.625rem] uppercase tracking-[0.1em] text-(--ui-text-tertiary)">
                      <tr>
                        {['Time', 'Symbol', 'Order', 'Status', 'Detail', 'Evidence'].map(label => (
                          <th className="border-b border-(--ui-stroke-tertiary) px-3 py-2 font-medium" key={label}>
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {filteredRows.map(({ receipt, state }) => (
                        <tr className="border-b border-(--ui-stroke-tertiary) last:border-b-0" key={receipt.id}>
                          <td className="px-3 py-3 font-mono text-[0.6875rem]">
                            {formatEasternDateTime(receipt.timestamp)}
                          </td>
                          <td className="px-3 py-3 font-semibold">
                            {receipt.symbol}
                            <span className="ml-1 font-normal text-(--ui-text-tertiary)">{receipt.side}</span>
                          </td>
                          <td className="px-3 py-3 font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
                            {receipt.orderId}
                          </td>
                          <td className="px-3 py-3">
                            <StateBadge state={state} />
                          </td>
                          <td className="px-3 py-3 text-(--ui-text-secondary)">{receipt.duplicatePrevention}</td>
                          <td className="px-3 py-3">
                            <button
                              className="font-mono text-[0.6875rem] text-primary hover:underline"
                              onClick={onOpenAudit}
                              type="button"
                            >
                              {receipt.reconciliationReference}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {filteredAwaiting.length > 0 ? (
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.1em] text-(--ui-text-tertiary)">
                    Approved, no recorded execution yet
                  </h3>
                  <div className="divide-y divide-(--ui-stroke-tertiary) border-y border-(--ui-stroke-tertiary)">
                    {filteredAwaiting.map(proposal => (
                      <AwaitingProposalRow key={proposal.id} proposal={proposal} />
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function AwaitingProposalRow({ proposal }: { proposal: Proposal }) {
  return (
    <article className="grid gap-3 py-3 md:grid-cols-[1.2fr_.6fr_1fr_auto]">
      <div>
        <div className="flex items-center gap-2">
          <span className="font-mono font-semibold">{proposal.symbol}</span>
          <span className="text-(--ui-text-tertiary)">{proposal.side}</span>
        </div>
        <p className="mt-1 font-mono text-[0.625rem] text-(--ui-text-tertiary)">{proposal.id}</p>
      </div>
      <div>
        <span className="text-(--ui-text-tertiary)">Strategy</span>
        <strong className="mt-1 block font-mono text-[0.6875rem]">{proposal.strategy}</strong>
      </div>
      <div>
        <span className="text-(--ui-text-tertiary)">Evidence</span>
        <strong className="mt-1 block font-mono text-[0.6875rem]">
          {proposal.evidenceReferences[0] ?? 'None recorded'}
        </strong>
      </div>
      <div className="flex items-start">
        <Badge variant="warn">No reconciliation record yet</Badge>
      </div>
    </article>
  )
}
