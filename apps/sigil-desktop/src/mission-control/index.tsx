import { PAGE_INSET_X } from '@hermes-desktop/app/layout-constants'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SearchField } from '@/components/ui/search-field'
import { cn } from '@/lib/utils'

import {
  type HermesAnalysisResult,
  type HermesSystemStatus,
  LocalHermesEngine,
  type SigilHermesEngine
} from '../hermes-engine'

import { desktopSigilOperatorAdapter } from './desktop-adapter'
import { sigilOperatorAdapter as mockSigilOperatorAdapter } from './mock-adapter'
import type {
  AuditEvent,
  PipelineStage,
  Proposal,
  SigilOperatorAdapter,
  SigilProviderSnapshot,
  SigilSnapshot,
  SigilTone,
  SimulatedOperatorAction
} from './types'

const RELEASE_STAGE = 'ALPHA 1.3'

const SECTIONS = ['overview', 'portfolio', 'proposals', 'launch', 'executions', 'reconciliation', 'audit', 'settings'] as const
type Section = (typeof SECTIONS)[number]

const SECTION_LABELS: Record<Section, string> = {
  overview: 'Overview',
  portfolio: 'Portfolio',
  proposals: 'Proposals',
  launch: 'Launch',
  executions: 'Executions',
  reconciliation: 'Reconciliation',
  audit: 'Audit',
  settings: 'Settings'
}

const TONE_BADGE: Record<SigilTone, React.ComponentProps<typeof Badge>['variant']> = {
  danger: 'destructive',
  info: 'default',
  muted: 'muted',
  success: 'default',
  warning: 'warn'
}

const STAGE_TONE: Record<PipelineStage['state'], SigilTone> = {
  blocked: 'danger',
  complete: 'success',
  pending: 'warning',
  ready: 'info',
  simulated: 'muted'
}

function StatusLabel({ children, tone = 'muted' }: { children: React.ReactNode; tone?: SigilTone }) {
  return (
    <Badge
      aria-label={`${children}: ${tone}`}
      className="rounded-[2px] font-mono tracking-wide"
      variant={TONE_BADGE[tone]}
    >
      <span aria-hidden className={cn('size-1.5 rounded-full', tone === 'danger' ? 'bg-destructive' : 'bg-current')} />
      {children}
    </Badge>
  )
}

function DataNotice({ snapshot }: { snapshot: SigilSnapshot }) {
  const disconnected = snapshot.brokerConnection === 'disconnected'

  if (snapshot.dataState !== 'stale' && !disconnected) {
    return null
  }

  return (
    <div
      className={cn(
        'flex flex-wrap items-center justify-between gap-2 border-b border-(--ui-stroke-tertiary) bg-amber-500/7 py-2 text-xs text-amber-700 dark:text-amber-300',
        PAGE_INSET_X
      )}
      role="status"
    >
      <span className="flex items-center gap-2">
        <Codicon name="warning" />
        {snapshot.dataState === 'stale'
          ? `Snapshot is stale · last verified ${snapshot.lastUpdated}`
          : 'Broker is disconnected.'}
      </span>
      <span>No broker submission is available.</span>
    </div>
  )
}

function MetricStrip({ snapshot }: { snapshot: SigilSnapshot }) {
  const metrics = [
    { label: 'System health', value: snapshot.systemHealth, detail: 'Local governance checks', tone: 'success' },
    { label: 'Masked account', value: snapshot.maskedAccountId, detail: 'Credentials never displayed', tone: 'muted' },
    { label: 'Cash', value: snapshot.cash, detail: 'Paper buying power', tone: 'info' },
    { label: 'Portfolio', value: snapshot.portfolioValue, detail: 'Simulated market value', tone: 'info' },
    { label: 'Strategies', value: String(snapshot.activeStrategies), detail: 'Active', tone: 'muted' },
    { label: 'Approvals', value: String(snapshot.pendingApprovals), detail: 'Pending', tone: 'warning' },
    {
      label: 'Kill switch',
      value: snapshot.killSwitch.toUpperCase(),
      detail: snapshot.killSwitch === 'engaged' ? 'All actions blocked' : 'Ready to engage',
      tone: snapshot.killSwitch === 'engaged' ? 'danger' : 'success'
    }
  ] as const

  return (
    <dl className="grid border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-7">
      {metrics.map(metric => (
        <div
          className="min-w-0 border-b border-(--ui-stroke-tertiary) px-4 py-2.5 last:border-b-0 sm:border-r lg:border-b-0"
          key={metric.label}
        >
          <dt className="text-[0.625rem] font-medium uppercase tracking-[0.12em] text-(--ui-text-tertiary)">
            {metric.label}
          </dt>
          <dd className="mt-1 truncate font-mono text-xs font-semibold">{metric.value}</dd>
          <dd className="mt-0.5 truncate text-[0.6875rem] text-(--ui-text-tertiary)">{metric.detail}</dd>
        </div>
      ))}
    </dl>
  )
}

function Pipeline({ stages, compact = false }: { stages: PipelineStage[]; compact?: boolean }) {
  return (
    <ol
      aria-label="Sigil governance stages"
      className={cn(
        'grid gap-px overflow-hidden border border-(--ui-stroke-tertiary) bg-(--ui-stroke-tertiary)',
        compact ? 'grid-cols-1 sm:grid-cols-3 2xl:grid-cols-9' : 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3'
      )}
    >
      {stages.map((stage, index) => (
        <li className="relative min-w-0 bg-(--ui-bg-secondary) px-3 py-3" key={stage.id}>
          <span
            aria-hidden
            className={cn(
              'absolute inset-x-0 top-0 h-px',
              stage.state === 'complete'
                ? 'bg-emerald-500'
                : stage.state === 'pending'
                  ? 'bg-amber-500'
                  : stage.state === 'blocked'
                    ? 'bg-destructive'
                    : 'bg-primary'
            )}
          />
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-[0.625rem] text-(--ui-text-quaternary)">
              {String(index + 1).padStart(2, '0')}
            </span>
            <StatusLabel tone={STAGE_TONE[stage.state]}>{stage.state}</StatusLabel>
          </div>
          <div className="mt-3 text-[0.6875rem] font-semibold uppercase tracking-[0.04em]">{stage.label}</div>
          <div className="mt-0.5 text-[0.6875rem] text-(--ui-text-tertiary)">{stage.detail}</div>
        </li>
      ))}
    </ol>
  )
}

function ProposalDetails({
  actionLocked,
  onAction,
  proposal
}: {
  actionLocked: boolean
  onAction: (action: SimulatedOperatorAction) => void
  proposal: Proposal
}) {
  return (
    <article className="border-b border-(--ui-stroke-tertiary) py-4 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">{proposal.symbol}</h3>
            <StatusLabel tone={proposal.side === 'BUY' ? 'info' : 'warning'}>{proposal.side}</StatusLabel>
            <StatusLabel
              tone={proposal.status === 'pending' ? 'warning' : proposal.status === 'approved' ? 'success' : 'danger'}
            >
              {proposal.status}
            </StatusLabel>
          </div>
          <p className="mt-1 text-xs text-(--ui-text-secondary)">
            {proposal.quantity} shares · est. {proposal.estimatedNotional} · {proposal.strategy}
          </p>
          <p className="mt-1 font-mono text-[0.625rem] text-(--ui-text-quaternary)">{proposal.id}</p>
        </div>
        <div className="flex gap-2">
          <Button
            disabled={actionLocked || proposal.status !== 'pending'}
            onClick={() => onAction({ type: 'reject-proposal', proposalId: proposal.id })}
            size="xs"
            variant="outline"
          >
            Reject
          </Button>
          <Button
            disabled={actionLocked || proposal.status !== 'pending'}
            onClick={() => onAction({ type: 'approve-proposal', proposalId: proposal.id })}
            size="xs"
          >
            Approve
          </Button>
        </div>
      </div>
      <div className="mt-3 grid gap-3 text-[0.6875rem] md:grid-cols-2">
        <div>
          <div className="font-medium text-(--ui-text-secondary)">Evidence references</div>
          <div className="mt-1 font-mono text-(--ui-text-tertiary)">{proposal.evidenceReferences.join(' · ')}</div>
        </div>
        <div>
          <div className="font-medium text-(--ui-text-secondary)">Risk results</div>
          <ul className="mt-1 space-y-0.5 text-(--ui-text-tertiary)">
            {proposal.riskResults.map(result => (
              <li key={result}>✓ {result}</li>
            ))}
          </ul>
        </div>
      </div>
    </article>
  )
}

function ContextInspector({ proposal }: { proposal: Proposal }) {
  return (
    <aside
      aria-label="Contextual inspector"
      className="min-h-0 border-l border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) xl:w-[21rem] xl:shrink-0"
    >
      <div className="border-b border-(--ui-stroke-tertiary) px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-(--ui-text-tertiary)">
              Contextual inspector
            </p>
            <h2 className="mt-1 text-sm font-semibold">{proposal.id}</h2>
          </div>
          <StatusLabel tone={proposal.status === 'pending' ? 'warning' : 'danger'}>{proposal.status}</StatusLabel>
        </div>
      </div>
      <div className="space-y-6 overflow-y-auto px-5 py-4 text-xs">
        <section>
          <h3 className="border-b border-(--ui-stroke-tertiary) pb-2 text-[0.625rem] font-semibold uppercase tracking-[0.12em] text-(--ui-text-tertiary)">
            Contextual metadata
          </h3>
          <dl className="mt-3 space-y-3">
            {[
              ['Symbol', proposal.symbol],
              ['Side / quantity', `${proposal.side} ${proposal.quantity}`],
              ['Estimated notional', proposal.estimatedNotional],
              ['Strategy', proposal.strategy],
              ['Account protection', 'Masked identity']
            ].map(([label, value]) => (
              <div className="flex items-start justify-between gap-4" key={label}>
                <dt className="text-(--ui-text-tertiary)">{label}</dt>
                <dd className="text-right font-mono text-[0.6875rem]">{value}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section>
          <h3 className="border-b border-(--ui-stroke-tertiary) pb-2 text-[0.625rem] font-semibold uppercase tracking-[0.12em] text-(--ui-text-tertiary)">
            Governance operator log
          </h3>
          <div className="mt-3 border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) p-3 font-mono text-[0.6875rem] leading-5 text-(--ui-text-secondary)">
            <p>&gt;&gt; INIT policy_check({proposal.id})</p>
            {proposal.riskResults.map(result => (
              <p key={result}>&gt;&gt; PASS {result}</p>
            ))}
            <p className="text-amber-500">&gt;&gt; HOLD: manual confirmation required</p>
            <p className="text-primary">&gt;&gt; SYSTEM: broker transport unavailable</p>
          </div>
        </section>
        <section>
          <h3 className="border-b border-(--ui-stroke-tertiary) pb-2 text-[0.625rem] font-semibold uppercase tracking-[0.12em] text-(--ui-text-tertiary)">
            Evidence &amp; verification
          </h3>
          <ul className="mt-3 space-y-2">
            {proposal.evidenceReferences.map(reference => (
              <li
                className="flex items-center gap-2 border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 font-mono text-[0.6875rem]"
                key={reference}
              >
                <Codicon name="verified-filled" />
                {reference}
              </li>
            ))}
          </ul>
        </section>
      </div>
      <div className="border-t border-(--ui-stroke-tertiary) px-5 py-3 text-[0.6875rem] text-(--ui-text-tertiary)">
        Local simulation only · confirmation required
      </div>
    </aside>
  )
}

function LaunchControl({
  actionLocked,
  onAction,
  snapshot
}: {
  actionLocked: boolean
  onAction: (action: SimulatedOperatorAction) => void
  snapshot: SigilSnapshot
}) {
  return (
    <div>
      <div className="grid gap-px bg-(--ui-stroke-tertiary) sm:grid-cols-2">
        {[
          ['Certification', snapshot.certificationStatus],
          ['Launch state', snapshot.launchState],
          ['Maximum launch notional', snapshot.maximumLaunchNotional],
          ['Paper sizing policy', snapshot.firstLaunchLimit]
        ].map(([label, value]) => (
          <dl className="bg-(--ui-bg-primary) p-3" key={label}>
            <dt className="text-[0.625rem] uppercase tracking-[0.1em] text-(--ui-text-tertiary)">{label}</dt>
            <dd className="mt-1 text-sm font-semibold">{value}</dd>
          </dl>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          disabled={actionLocked || snapshot.launchState === 'armed' || snapshot.killSwitch === 'engaged'}
          onClick={() => onAction({ type: 'arm-launch' })}
          size="sm"
        >
          Arm simulated launch
        </Button>
        <Button
          disabled={actionLocked || snapshot.launchState === 'suspended'}
          onClick={() => onAction({ type: 'suspend-launch' })}
          size="sm"
          variant="outline"
        >
          Suspend
        </Button>
        <Button
          disabled={actionLocked || snapshot.killSwitch === 'engaged'}
          onClick={() => onAction({ type: 'engage-kill-switch' })}
          size="sm"
          variant="destructive"
        >
          Engage kill switch
        </Button>
      </div>
      <p className="mt-3 text-[0.6875rem] text-(--ui-text-tertiary)">
        Paper sizing is dynamic and portfolio-bounded. No live capital limit or broker execution control exists.
      </p>
    </div>
  )
}

function ExecutionTable({ snapshot }: { snapshot: SigilSnapshot }) {
  if (snapshot.receipts.length === 0) {
    return <EmptyState description="No immutable execution receipts match this snapshot." title="No receipts" />
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[48rem] text-left text-xs">
        <thead className="text-[0.625rem] uppercase tracking-[0.1em] text-(--ui-text-tertiary)">
          <tr>
            {['Time', 'Side', 'Symbol', 'Quantity', 'Simulated price', 'Notional', 'Status', 'Reconciliation'].map(label => (
              <th className="border-b border-(--ui-stroke-tertiary) px-3 py-2 font-medium" key={label}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {snapshot.receipts.map(receipt => (
            <tr className="border-b border-(--ui-stroke-tertiary) last:border-b-0" key={receipt.id}>
              <td className="px-3 py-3 font-mono text-[0.6875rem]">
                <span className="block">{receipt.timestamp}</span>
                <span className="mt-1 block text-[0.625rem] text-(--ui-text-quaternary)">{receipt.id}</span>
              </td>
              <td className="px-3 py-3">
                <StatusLabel tone={receipt.side === 'BUY' ? 'info' : 'warning'}>{receipt.side}</StatusLabel>
              </td>
              <td className="px-3 py-3 font-semibold">{receipt.symbol}</td>
              <td className="px-3 py-3 font-mono">{receipt.quantity}</td>
              <td className="px-3 py-3 font-mono">{receipt.price}</td>
              <td className="px-3 py-3 font-mono">{receipt.notional}</td>
              <td className="px-3 py-3">
                <StatusLabel tone={receipt.state === 'simulated' ? 'muted' : 'danger'}>
                  {receipt.brokerStatus}
                </StatusLabel>
              </td>
              <td className="px-3 py-3">
                {receipt.reconciliationRequired ? (
                  <StatusLabel tone="danger">Required</StatusLabel>
                ) : (
                  <StatusLabel tone="success">Clear</StatusLabel>
                )}
                {receipt.reconciliationReference ? (
                  <span className="mt-1 block font-mono text-[0.625rem] text-(--ui-text-quaternary)">
                    {receipt.reconciliationReference}
                  </span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PaperPortfolio({
  onOpenAudit,
  snapshot
}: {
  onOpenAudit: () => void
  snapshot: SigilSnapshot
}) {
  const [activityQuery, setActivityQuery] = useState('')
  const [activitySide, setActivitySide] = useState<'ALL' | 'BUY' | 'SELL'>('ALL')

  const activities = snapshot.receipts.filter(receipt => {
    const sideMatches = activitySide === 'ALL' || receipt.side === activitySide

    const queryMatches = [receipt.symbol, receipt.orderId, receipt.proposalId]
      .join(' ')
      .toLowerCase()
      .includes(activityQuery.trim().toLowerCase())

    return sideMatches && queryMatches
  })

  return (
    <div className={cn('py-5', PAGE_INSET_X)} data-testid="paper-portfolio">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Paper portfolio and activity</h2>
            <StatusLabel tone="info">PAPER</StatusLabel>
            <StatusLabel tone="muted">SIMULATED</StatusLabel>
          </div>
          <p className="mt-1 text-xs text-(--ui-text-tertiary)">
            Local accounting only · providers remain read-only · every fill links to immutable evidence
          </p>
        </div>
        <Button onClick={onOpenAudit} size="xs" variant="outline">
          Inspect all audit evidence
        </Button>
      </div>

      <dl className="mt-4 grid gap-px border border-(--ui-stroke-tertiary) bg-(--ui-stroke-tertiary) sm:grid-cols-2 xl:grid-cols-6">
        {[
          ['Cash', snapshot.cash],
          ['Buying power', snapshot.buyingPower ?? snapshot.cash],
          ['Holdings value', snapshot.portfolioValue],
          ['Total account value', snapshot.totalAccountValue ?? snapshot.portfolioValue],
          ['Unrealized P&L', snapshot.unrealizedPnl ?? '$0.00'],
          ['Realized P&L', snapshot.realizedPnl ?? '$0.00']
        ].map(([label, value]) => (
          <div className="bg-(--ui-bg-secondary) px-3 py-3" key={label}>
            <dt className="text-[0.625rem] uppercase tracking-[0.1em] text-(--ui-text-tertiary)">{label}</dt>
            <dd className="mt-1 font-mono text-sm font-semibold">{value}</dd>
          </div>
        ))}
      </dl>

      <section aria-labelledby="paper-holdings-title" className="mt-6">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-[0.1em]" id="paper-holdings-title">
              Current simulated holdings
            </h3>
            <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
              Quantity, cost, allocation, and P&L refresh with the local five-second snapshot.
            </p>
          </div>
          <span className="font-mono text-[0.625rem] text-(--ui-text-tertiary)">
            {snapshot.positions?.length ?? 0} paper positions
          </span>
        </div>
        {snapshot.positions?.length ? (
          <div className="overflow-x-auto border border-(--ui-stroke-tertiary)">
            <table className="w-full min-w-[62rem] text-left text-xs">
              <thead className="bg-(--ui-bg-secondary) text-[0.625rem] uppercase tracking-[0.1em] text-(--ui-text-tertiary)">
                <tr>
                  {['Symbol', 'Quantity', 'Average cost', 'Market value', 'Allocation', 'Unrealized P&L', 'Realized P&L', 'Evidence'].map(label => (
                    <th className="border-b border-(--ui-stroke-tertiary) px-3 py-2 font-medium" key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {snapshot.positions.map(position => (
                  <tr className="border-b border-(--ui-stroke-tertiary) last:border-b-0" key={position.symbol}>
                    <td className="px-3 py-3 font-mono font-semibold">{position.symbol}</td>
                    <td className="px-3 py-3 font-mono">{position.quantity}</td>
                    <td className="px-3 py-3 font-mono">{position.averageCost}</td>
                    <td className="px-3 py-3 font-mono">{position.marketValue}</td>
                    <td className="px-3 py-3 font-mono">{position.allocation}</td>
                    <td className="px-3 py-3 font-mono">{position.unrealizedPnl}</td>
                    <td className="px-3 py-3 font-mono">{position.realizedPnl}</td>
                    <td className="px-3 py-3">
                      <button className="font-mono text-[0.6875rem] text-primary hover:underline" onClick={onOpenAudit} type="button">
                        {position.auditReferences[0] ?? 'Inspect audit'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState description="The local paper ledger has no open simulated positions." title="No paper holdings" />
        )}
      </section>

      <section aria-labelledby="paper-activity-title" className="mt-6">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-[0.1em]" id="paper-activity-title">
              Simulated buy and sell activity
            </h3>
            <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
              Most recent fills first · no broker submission path
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SearchField
              aria-label="Filter paper activity"
              onChange={setActivityQuery}
              placeholder="Filter symbol or order"
              value={activityQuery}
            />
            {(['ALL', 'BUY', 'SELL'] as const).map(side => (
              <Button
                key={side}
                onClick={() => setActivitySide(side)}
                size="xs"
                variant={activitySide === side ? 'secondary' : 'outline'}
              >
                {side}
              </Button>
            ))}
          </div>
        </div>
        {activities.length ? (
          <div className="divide-y divide-(--ui-stroke-tertiary) border-y border-(--ui-stroke-tertiary)">
            {activities.map(receipt => (
              <article className="grid gap-3 py-3 md:grid-cols-[1.2fr_.5fr_.5fr_.7fr_.7fr_1fr]" key={receipt.id}>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold">{receipt.symbol}</span>
                    <StatusLabel tone={receipt.side === 'BUY' ? 'info' : 'warning'}>{receipt.side}</StatusLabel>
                    <StatusLabel tone="muted">SIMULATED</StatusLabel>
                  </div>
                  <p className="mt-1 font-mono text-[0.625rem] text-(--ui-text-tertiary)">
                    {receipt.timestamp}
                  </p>
                </div>
                <div><span className="text-(--ui-text-tertiary)">Qty</span><strong className="mt-1 block font-mono">{receipt.quantity}</strong></div>
                <div><span className="text-(--ui-text-tertiary)">Price</span><strong className="mt-1 block font-mono">{receipt.price}</strong></div>
                <div><span className="text-(--ui-text-tertiary)">Notional</span><strong className="mt-1 block font-mono">{receipt.notional}</strong></div>
                <div><span className="text-(--ui-text-tertiary)">Status</span><strong className="mt-1 block font-mono">{receipt.brokerStatus}</strong></div>
                <button className="text-left font-mono text-[0.6875rem] text-primary hover:underline" onClick={onOpenAudit} type="button">
                  {receipt.reconciliationReference}
                </button>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState description="Start authorized paper automation or clear the activity filters." title="No matching paper fills" />
        )}
      </section>
    </div>
  )
}

function AuditTable({ events }: { events: AuditEvent[] }) {
  const [query, setQuery] = useState('')
  const normalized = query.trim().toLowerCase()

  const filtered = events.filter(event =>
    [event.orderId, event.proposalId, event.status, event.evidenceReference, event.summary]
      .join(' ')
      .toLowerCase()
      .includes(normalized)
  )

  return (
    <div>
      {events.length > 0 ? (
        <SearchField
          aria-label="Filter audit evidence"
          containerClassName="mb-3"
          onChange={setQuery}
          placeholder="Filter by order, proposal, status, or evidence"
          value={query}
        />
      ) : null}
      {filtered.length === 0 ? (
        <EmptyState
          description={events.length === 0 ? 'No governed events are present.' : 'Try a different evidence filter.'}
          title={events.length === 0 ? 'No audit evidence' : 'No matching evidence'}
        />
      ) : (
        <div className="divide-y divide-(--ui-stroke-tertiary)">
          {filtered.map(event => (
            <details className="group py-3" key={event.id}>
              <summary className="grid cursor-pointer list-none gap-2 text-xs md:grid-cols-[1fr_1fr_1fr_2fr_auto]">
                <span className="font-mono text-[0.6875rem]">{event.timestamp}</span>
                <span className="font-mono text-[0.6875rem] text-(--ui-text-tertiary)">{event.orderId}</span>
                <span className="font-mono text-[0.6875rem] text-(--ui-text-tertiary)">{event.evidenceReference}</span>
                <span>{event.summary}</span>
                <StatusLabel
                  tone={event.status === 'rejected' || event.status === 'outcome-uncertain' ? 'danger' : 'muted'}
                >
                  {event.status}
                </StatusLabel>
              </summary>
              <pre className="mt-3 overflow-x-auto border-l border-(--ui-stroke-tertiary) pl-4 text-[0.6875rem] leading-relaxed text-(--ui-text-secondary)">
                {JSON.stringify(event.details, null, 2)}
              </pre>
            </details>
          ))}
        </div>
      )}
    </div>
  )
}

function ProviderPanel({
  error,
  loading,
  onRefresh,
  snapshot
}: {
  error: string | null
  loading: boolean
  onRefresh: () => void
  snapshot: SigilProviderSnapshot | null
}) {
  const [query, setQuery] = useState('')
  const [descending, setDescending] = useState(false)

  const symbols = [...(snapshot?.alpaca.symbols ?? [])]
    .filter(item => item.symbol.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((left, right) => {
      const result = left.symbol.localeCompare(right.symbol)

      return descending ? -result : result
    })

  const tone = (status: string): SigilTone =>
    status === 'connected' ? 'success' : status === 'degraded' ? 'warning' : 'muted'

  return (
    <section
      aria-labelledby="provider-health-title"
      className="border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary)"
      data-testid="provider-health"
    >
      <div className={cn('flex flex-wrap items-center justify-between gap-3 py-3', PAGE_INSET_X)}>
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-[0.1em]" id="provider-health-title">
            Read-only provider health
          </h2>
          <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
            Market and account data only · credentials remain in the local backend
          </p>
        </div>
        <div className="flex items-center gap-2">
          {snapshot ? (
            <>
              <StatusLabel tone={tone(snapshot.alpaca.status)}>Alpaca {snapshot.alpaca.status}</StatusLabel>
              <StatusLabel tone={tone(snapshot.public.status)}>Public {snapshot.public.status}</StatusLabel>
            </>
          ) : null}
          <Button disabled={loading} onClick={onRefresh} size="xs" variant="outline">
            <Codicon name="refresh" />
            {loading ? 'Refreshing…' : 'Refresh providers'}
          </Button>
        </div>
      </div>
      {error ? (
        <div className={cn('border-t border-(--ui-stroke-tertiary) py-3 text-xs text-destructive', PAGE_INSET_X)} role="alert">
          Provider refresh degraded safely: {error}
        </div>
      ) : null}
      {!snapshot && loading ? (
        <div className={cn('border-t border-(--ui-stroke-tertiary) py-4', PAGE_INSET_X)}>
          <Loader label="Loading read-only provider status" />
        </div>
      ) : null}
      {snapshot ? (
        <div className={cn('grid gap-px border-t border-(--ui-stroke-tertiary) bg-(--ui-stroke-tertiary) lg:grid-cols-2', PAGE_INSET_X)}>
          <div className="bg-(--ui-bg-primary) py-4 lg:pr-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-xs font-semibold">U.S.-listed paper screening universe</h3>
                <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">{snapshot.alpaca.message}</p>
                {snapshot.alpaca.universe ? (
                  <>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <StatusLabel tone="success">
                        Alpaca IEX · {snapshot.alpaca.universe.iex_status ?? 'real-time'}
                      </StatusLabel>
                      <StatusLabel tone="warning">
                        Broader U.S. data · {snapshot.alpaca.universe.broader_us_status ?? '15-minute delayed'}
                      </StatusLabel>
                    </div>
                    <p className="mt-1 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
                      Coverage {snapshot.alpaca.universe.available}/{snapshot.alpaca.universe.total} symbols · source: {snapshot.alpaca.universe.catalog_source ?? snapshot.alpaca.universe.scope} · freshness: {snapshot.alpaca.universe.catalog_freshness ?? 'unverified'}
                    </p>
                    <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
                      IEX quotes are real-time. Broader U.S. historical data is delayed by 15 minutes. Every active U.S. stock is not claimed as watched because provider asset-catalog access is not verified.
                    </p>
                    {snapshot.alpaca.universe.coverage_limitation ? (
                      <p className="mt-1 max-w-3xl text-[0.6875rem] text-amber-700 dark:text-amber-300">
                        Coverage boundary: {snapshot.alpaca.universe.coverage_limitation}
                      </p>
                    ) : null}
                  </>
                ) : null}
              </div>
              <button
                className="font-mono text-[0.6875rem] text-primary hover:underline"
                onClick={() => setDescending(value => !value)}
                type="button"
              >
                Symbol {descending ? '↓' : '↑'}
              </button>
            </div>
            <SearchField
              aria-label="Filter market symbols"
              containerClassName="mb-3"
              onChange={setQuery}
              placeholder="Filter symbols"
              value={query}
            />
            {symbols.length ? (
              <div className="divide-y divide-(--ui-stroke-tertiary)">
                {symbols.map(item => (
                  <details className="group py-2.5" key={item.symbol}>
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-4">
                      <span>
                        <span className="font-mono text-xs font-semibold">{item.symbol}</span>
                        {item.name ? <span className="ml-2 text-[0.6875rem] text-(--ui-text-tertiary)">{item.name}</span> : null}
                      </span>
                      <span className="flex items-center gap-3">
                        {item.daily_change_percent ? (
                          <span className="font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
                            {item.daily_change_percent === 'unavailable' ? 'change unavailable' : `${Number(item.daily_change_percent) >= 0 ? '+' : ''}${item.daily_change_percent}%`}
                          </span>
                        ) : null}
                        <span className="font-mono text-sm">{new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(item.price))}</span>
                      </span>
                    </summary>
                    <p className="mt-2 text-[0.6875rem] text-(--ui-text-tertiary)">
                      {item.sector ? `${item.sector} · ` : ''}{item.source} · {item.screen_status ?? 'available'} · observed {item.observed_at}
                    </p>
                  </details>
                ))}
              </div>
            ) : (
              <EmptyState description="Refresh providers or clear the symbol filter." title="No market rows" />
            )}
          </div>
          <div className="bg-(--ui-bg-primary) py-4 lg:pl-5">
            <h3 className="text-xs font-semibold">Public account view</h3>
            <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">{snapshot.public.message}</p>
            {snapshot.public.accounts.length ? (
              <div className="mt-3 space-y-3">
                {snapshot.public.accounts.map(account => (
                  <details className="border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-3" key={account.masked_account_id}>
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-4">
                      <span className="font-mono text-xs font-semibold">{account.masked_account_id}</span>
                      <span className="text-[0.6875rem] text-(--ui-text-tertiary)">Inspect account</span>
                    </summary>
                    <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                      <div>
                        <dt className="text-[0.625rem] uppercase text-(--ui-text-tertiary)">Cash buying power</dt>
                        <dd className="mt-1 font-mono text-xs">{account.cash}</dd>
                      </div>
                      <div>
                        <dt className="text-[0.625rem] uppercase text-(--ui-text-tertiary)">Portfolio value</dt>
                        <dd className="mt-1 font-mono text-xs">{account.portfolio_value}</dd>
                      </div>
                    </dl>
                    <div className="mt-3 border-t border-(--ui-stroke-tertiary) pt-3 font-mono text-[0.6875rem] text-(--ui-text-secondary)">
                      {account.positions.length
                        ? account.positions.map(position => `${position.symbol} ${position.quantity}`).join(' · ')
                        : 'No reported equity positions'}
                    </div>
                  </details>
                ))}
              </div>
            ) : (
              <div className="mt-3">
                <EmptyState description="No readable account data is available. Broker execution remains disabled." title="No account rows" />
              </div>
            )}
          </div>
        </div>
      ) : null}
      {snapshot ? (
        <div className={cn('border-t border-(--ui-stroke-tertiary) py-2 font-mono text-[0.625rem] text-(--ui-text-quaternary)', PAGE_INSET_X)}>
          Checked {snapshot.checked_at} · secrets exposed: no · broker submission: unavailable
        </div>
      ) : null}
    </section>
  )
}

const localHermesEngine = new LocalHermesEngine()

type ProviderResponse =
  | { ok: true; result: SigilProviderSnapshot }
  | { ok: false; error: string; message: string }

interface MissionControlDesktopApi {
  getRuntimeSnapshot?: () => Promise<unknown>
  getProviderSnapshot?: () => Promise<ProviderResponse>
  buildInfo?: {
    version: string
    build: string
    commit: string
    buildTime: string
    channel: 'dev' | 'release'
    applicationMode: 'Live development' | 'Packaged release'
  }
  checkForUpdates?: () => Promise<{ status: string; message: string }>
}

function desktopApi(): MissionControlDesktopApi | undefined {
  return (window as Window & { sigilDesktop?: MissionControlDesktopApi }).sigilDesktop
}

function defaultOperatorAdapter(): SigilOperatorAdapter {
  return desktopApi()?.getRuntimeSnapshot
    ? desktopSigilOperatorAdapter
    : mockSigilOperatorAdapter
}

interface SigilOperatorViewProps {
  adapter?: SigilOperatorAdapter
  engine?: SigilHermesEngine
}

export function SigilOperatorView({
  adapter = defaultOperatorAdapter(),
  engine = localHermesEngine
}: SigilOperatorViewProps) {
  const [section, setSection] = useState<Section>('overview')
  const [snapshot, setSnapshot] = useState<SigilSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [operatorActionsEnabled, setOperatorActionsEnabled] = useState(false)
  const [pendingAction, setPendingAction] = useState<SimulatedOperatorAction | null>(null)
  const [reloadGeneration, setReloadGeneration] = useState(0)
  const [selectedProposalId, setSelectedProposalId] = useState<string | null>(null)
  const [hermesStatus, setHermesStatus] = useState<HermesSystemStatus | null>(null)
  const [hermesAnalysis, setHermesAnalysis] = useState<HermesAnalysisResult | null>(null)
  const [hermesLoading, setHermesLoading] = useState(false)
  const [hermesError, setHermesError] = useState<string | null>(null)
  const [providerSnapshot, setProviderSnapshot] = useState<SigilProviderSnapshot | null>(null)
  const [providerLoading, setProviderLoading] = useState(false)
  const [providerError, setProviderError] = useState<string | null>(null)
  const [pendingCycleAction, setPendingCycleAction] = useState<'start' | 'pause' | 'stop' | null>(null)
  const [pendingAuthorizationAction, setPendingAuthorizationAction] = useState<'grant' | 'revoke' | null>(null)
  const [pendingPaperReset, setPendingPaperReset] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [updateMessage, setUpdateMessage] = useState<string | null>(null)
  const liveRuntime = typeof adapter.controlPaperCycle === 'function'
  const liveAuthorization = typeof adapter.controlPaperAuthorization === 'function'

  const refreshProviders = useCallback((): void => {
    const providerApi = desktopApi()?.getProviderSnapshot

    if (!providerApi) {
      return
    }

    setProviderLoading(true)
    setProviderError(null)
    void providerApi()
      .then(response => {
        if (!response.ok) {
          throw new Error(response.message)
        }

        setProviderSnapshot(response.result)
      })
      .catch(reason => setProviderError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setProviderLoading(false))
  }, [])

  useEffect(() => {
    let cancelled = false

    const refresh = (): void => {
      void adapter
        .readSnapshot()
        .then(next => {
          if (!cancelled) {
            setSnapshot(next)
            setError(null)
          }
        })
        .catch(reason => {
          if (!cancelled) {
            setError(reason instanceof Error ? reason.message : String(reason))
          }
        })
    }

    refresh()
    const refreshTimer = window.setInterval(refresh, 5_000)

    return () => {
      cancelled = true
      window.clearInterval(refreshTimer)
    }
  }, [adapter, reloadGeneration])

  useEffect(() => {
    if (!desktopApi()?.getProviderSnapshot) {
      return
    }

    refreshProviders()
    const providerTimer = window.setInterval(refreshProviders, 30_000)

    return () => window.clearInterval(providerTimer)
  }, [refreshProviders])

  useEffect(() => {
    let cancelled = false

    void engine
      .getSystemStatus()
      .then(status => {
        if (!cancelled) {
          setHermesStatus(status)
        }
      })
      .catch(reason => {
        if (!cancelled) {
          setHermesError(reason instanceof Error ? reason.message : String(reason))
        }
      })

    return () => {
      cancelled = true
    }
  }, [engine])

  const confirmation = useMemo(() => {
    if (!pendingAction) {
      return null
    }

    if (pendingAction.type === 'approve-proposal') {
      return {
        title: 'Confirm simulated approval',
        description: `Approve ${pendingAction.proposalId} in the local simulator? This does not authorize or submit an order.`,
        label: 'Confirm approval',
        destructive: false
      }
    }

    if (pendingAction.type === 'reject-proposal') {
      return {
        title: 'Confirm simulated rejection',
        description: `Reject ${pendingAction.proposalId} in the local simulator?`,
        label: 'Confirm rejection',
        destructive: true
      }
    }

    return {
      title: `Confirm ${pendingAction.type.replaceAll('-', ' ')}`,
      description:
        'This updates only the local simulated operator state. No broker endpoint or capital-limit control is available.',
      label: 'Confirm simulated action',
      destructive: pendingAction.type === 'engage-kill-switch'
    }
  }, [pendingAction])

  if (error) {
    return (
      <div className="grid h-full place-items-center p-6">
        <ErrorState description={error} title="Sigil snapshot unavailable">
          <Button
            onClick={() => {
              setError(null)
              setReloadGeneration(generation => generation + 1)
            }}
            size="sm"
            variant="outline"
          >
            Retry local snapshot
          </Button>
        </ErrorState>
      </div>
    )
  }

  if (!snapshot || snapshot.dataState === 'loading') {
    return (
      <div className="grid h-full place-items-center" role="status">
        <Loader label="Loading verified Sigil snapshot" />
      </div>
    )
  }

  const actionLocked = !operatorActionsEnabled || snapshot.brokerConnection !== 'disconnected'

  const selectedProposal =
    snapshot.proposals.find(proposal => proposal.id === selectedProposalId) ??
    snapshot.proposals.find(proposal => proposal.status === 'pending') ??
    snapshot.proposals[0]

  async function explainSelectedProposal(): Promise<void> {
    if (!selectedProposal) {
      return
    }

    setHermesLoading(true)
    setHermesError(null)

    try {
      const result = await engine.explainProposal({
        proposalId: selectedProposal.id,
        symbol: selectedProposal.symbol,
        side: selectedProposal.side,
        estimatedNotional: Number(selectedProposal.estimatedNotional.replace(/[^0-9.-]/g, '')),
        strategy: selectedProposal.strategy,
        evidenceReferences: selectedProposal.evidenceReferences.map(reference => ({
          id: reference,
          label: reference,
          source: 'sigil'
        }))
      })

      setHermesAnalysis(result)
    } catch (reason) {
      setHermesError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setHermesLoading(false)
    }
  }

  return (
    <section
      className="flex h-full min-h-0 flex-col bg-(--ui-bg-primary) text-[0.8125rem]"
      data-testid="sigil-operator"
    >
      <header
        className={cn('shrink-0 border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) py-3', PAGE_INSET_X)}
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-baseline gap-3">
              <h1 className="text-base font-bold uppercase tracking-[0.025em]">Sigil Operator</h1>
              <span
                className="
                  rounded-sm
                  border border-lime-400/70
                  bg-lime-400/8
                  px-2 py-0.5
                  font-mono text-[0.625rem] font-semibold
                  uppercase tracking-[0.12em]
                  text-lime-300/90
                  shadow-[0_0_7px_rgba(163,230,53,0.38)]
                "
              >
                ● {RELEASE_STAGE}
              </span>
              <span className="font-mono text-[0.625rem] uppercase tracking-[0.12em] text-(--ui-text-tertiary)">
                Mission control
              </span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <StatusLabel tone={snapshot.environment === 'paper' ? 'info' : 'danger'}>
              {snapshot.environment.toUpperCase()}
            </StatusLabel>
            <StatusLabel tone="muted">{snapshot.simulation ? 'SIMULATED' : 'NOT SIMULATED'}</StatusLabel>
            <StatusLabel tone={snapshot.brokerConnection === 'connected' ? 'success' : 'danger'}>
              {snapshot.brokerConnection.toUpperCase()}
            </StatusLabel>
            <span className="hidden h-5 w-px bg-(--ui-stroke-tertiary) sm:block" />
            <span className="font-mono text-[0.6875rem] font-semibold text-primary">
              HEALTH · {snapshot.systemHealth === 'Governance healthy' ? '99.8%' : snapshot.systemHealth}
            </span>
            <StatusLabel tone="success">{snapshot.certificationStatus}</StatusLabel>
            {desktopApi()?.buildInfo ? (
              <button
                className="
                  rounded-sm
                  border border-lime-400/70
                  bg-lime-400/8
                  px-3 py-1
                  font-mono text-[0.625rem] font-semibold
                  uppercase tracking-[0.08em]
                  text-lime-300/90
                  shadow-[0_0_8px_rgba(163,230,53,0.35)]
                  transition-colors
                  hover:bg-lime-400/12
                  hover:text-lime-200
                "
                data-sigil-build-badge
                onClick={() => setAboutOpen(true)}
                type="button"
              >
                {RELEASE_STAGE} · v{desktopApi()?.buildInfo?.version} · BUILD {desktopApi()?.buildInfo?.build}
              </button>
            ) : null}
            <Button onClick={() => setReloadGeneration(value => value + 1)} size="xs" variant="outline">
              <Codicon name="refresh" />
              Refresh runtime
            </Button>
          </div>
        </div>
      </header>
      <DataNotice snapshot={snapshot} />
      {liveAuthorization ? (
        <div
          className={cn(
            'flex flex-wrap items-center justify-between gap-3 border-b py-2',
            snapshot.paperAuthorization?.status === 'active'
              ? 'border-emerald-500/30 bg-emerald-500/8'
              : 'border-amber-500/30 bg-amber-500/8',
            PAGE_INSET_X
          )}
          data-testid="paper-authorization"
        >
          <div className="flex flex-wrap items-center gap-3">
            <StatusLabel tone={snapshot.paperAuthorization?.status === 'active' ? 'success' : 'warning'}>
              Paper month {snapshot.paperAuthorization?.authorizationMonth ?? 'unavailable'} · {snapshot.paperAuthorization?.status ?? 'required'}
            </StatusLabel>
            <span className="text-xs text-(--ui-text-secondary)">
              {snapshot.paperAuthorization?.status === 'active'
                ? `This calendar month started automatically authorized for local simulated buys and sells; expires ${snapshot.paperAuthorization.expiresAt}`
                : 'Revoked for this calendar month. Automatic paper approval and execution remain denied until next month.'}
            </span>
            <span className="font-mono text-[0.625rem] text-(--ui-text-tertiary)">
              {snapshot.paperAuthorization?.authorizationId ?? 'No active authorization'}
            </span>
          </div>
          <div className="flex gap-2">
            <Button
              disabled={snapshot.paperAuthorization?.status !== 'active'}
              onClick={() => setPendingAuthorizationAction('revoke')}
              size="xs"
              variant="outline"
            >
              Revoke
            </Button>
          </div>
        </div>
      ) : null}
      {liveRuntime ? (
        <div className={cn('flex flex-wrap items-center justify-between gap-3 border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) py-2', PAGE_INSET_X)}>
          <div className="flex items-center gap-3 text-xs">
            <StatusLabel tone={snapshot.automationState === 'running' ? 'success' : 'muted'}>
              Paper automation {snapshot.automationState ?? 'stopped'}
            </StatusLabel>
            <span className="font-mono text-(--ui-text-tertiary)">
              {snapshot.automationCycleCount ?? 0} cycles · refreshed {snapshot.lastUpdated}
            </span>
          </div>
          <div className="flex gap-2">
            {(['start', 'pause', 'stop'] as const).map(action => (
              <Button
                className={cn(
                  action === 'start' &&
                    snapshot.automationState === 'running' &&
                    'border-emerald-400 bg-emerald-500 text-white opacity-100 shadow-[0_0_0_1px_rgba(52,211,153,.25)]'
                )}
                disabled={
                  action === 'start'
                    ? snapshot.automationState === 'running' ||
                      snapshot.paperAuthorization?.status !== 'active'
                    : action === 'pause'
                      ? snapshot.automationState !== 'running'
                      : snapshot.automationState === 'stopped'
                }
                key={action}
                onClick={async () => {
                  if (!adapter.controlPaperCycle) {
                    return
                  }

                  try {
                    setControlError(null)
                    setSnapshot(await adapter.controlPaperCycle(action))
                  } catch (reason) {
                    setControlError(
                      reason instanceof Error ? reason.message : String(reason)
                    )
                  }
                }}
                size="xs"
                variant={
                  action === 'stop'
                    ? 'destructive'
                    : action === 'start' && snapshot.automationState === 'running'
                      ? 'default'
                      : 'outline'
                }
              >
                {action === 'start' && snapshot.automationState === 'running' ? '● Running' : `${action[0]?.toUpperCase()}${action.slice(1)}`}
              </Button>
            ))}
          </div>
        </div>
      ) : null}
      {controlError ? (
        <div className={cn('border-b border-destructive/30 bg-destructive/8 py-2 text-xs text-destructive', PAGE_INSET_X)} role="alert">
          Paper control denied safely: {controlError}
        </div>
      ) : null}
      <nav
        aria-label="Sigil sections"
        className={cn(
          'flex shrink-0 gap-5 overflow-x-auto border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary)',
          PAGE_INSET_X
        )}
      >
        {SECTIONS.map(item => (
          <button
            aria-current={section === item ? 'page' : undefined}
            className={cn(
              'shrink-0 border-b-2 px-0 py-2.5 text-[0.6875rem] font-semibold uppercase tracking-[0.06em] transition-colors',
              section === item
                ? 'border-primary text-foreground'
                : 'border-transparent text-(--ui-text-tertiary) hover:text-foreground'
            )}
            key={item}
            onClick={() => setSection(item)}
            type="button"
          >
            {SECTION_LABELS[item]}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {section === 'overview' ? (
          <div className="flex min-h-full flex-col xl:flex-row">
            <div className="min-w-0 flex-1">
              <MetricStrip snapshot={snapshot} />
              <ProviderPanel
                error={providerError}
                loading={providerLoading}
                onRefresh={refreshProviders}
                snapshot={providerSnapshot}
              />
              <div className={cn('py-4', PAGE_INSET_X)}>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-xs font-semibold uppercase tracking-[0.1em]">Governance pipeline</h2>
                  <span className="font-mono text-[0.625rem] text-(--ui-text-tertiary)">
                    09 immutable control stages
                  </span>
                </div>
                <Pipeline compact stages={snapshot.stages} />
                <div className="mt-5 grid gap-6 2xl:grid-cols-[1.35fr_1fr]">
                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <h2 className="text-xs font-semibold uppercase tracking-[0.1em]">Pending proposals</h2>
                      <span className="font-mono text-[0.625rem] text-(--ui-text-tertiary)">
                        {snapshot.pendingApprovals} awaiting review
                      </span>
                    </div>
                    {snapshot.proposals
                      .filter(proposal => proposal.status === 'pending')
                      .slice(0, 2)
                      .map(proposal => (
                        <button
                          aria-pressed={selectedProposal?.id === proposal.id}
                          className={cn(
                            'grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-t border-(--ui-stroke-tertiary) px-2 py-3 text-left transition-colors last:border-b hover:bg-(--chrome-action-hover)',
                            selectedProposal?.id === proposal.id && 'border-l-2 border-l-primary bg-(--ui-bg-secondary)'
                          )}
                          key={proposal.id}
                          onClick={() => setSelectedProposalId(proposal.id)}
                          type="button"
                        >
                          <span className="min-w-0">
                            <span className="flex items-center gap-2">
                              <span className="font-mono text-xs font-semibold">{proposal.symbol}</span>
                              <StatusLabel tone={proposal.side === 'BUY' ? 'info' : 'warning'}>
                                {proposal.side}
                              </StatusLabel>
                            </span>
                            <span className="mt-1 block truncate text-[0.6875rem] text-(--ui-text-tertiary)">
                              {proposal.id} · {proposal.strategy}
                            </span>
                          </span>
                          <span className="text-right font-mono text-[0.6875rem]">
                            <span className="block">{proposal.estimatedNotional}</span>
                            <span className="text-(--ui-text-tertiary)">{proposal.quantity} shares</span>
                          </span>
                        </button>
                      ))}
                  </div>
                  <div>
                    <h2 className="mb-3 text-xs font-semibold uppercase tracking-[0.1em]">Launch control</h2>
                    <LaunchControl actionLocked onAction={setPendingAction} snapshot={snapshot} />
                  </div>
                </div>
              </div>
            </div>
            {selectedProposal ? <ContextInspector proposal={selectedProposal} /> : null}
          </div>
        ) : null}
        {section === 'proposals' ? (
          <div className={cn('py-5', PAGE_INSET_X)}>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">Proposals and approvals</h2>
                <p className="mt-1 text-xs text-(--ui-text-tertiary)">
                  Decisions are local simulation only and require confirmation.
                </p>
              </div>
              {!liveRuntime ? <Button
                onClick={() => setOperatorActionsEnabled(enabled => !enabled)}
                size="sm"
                variant={operatorActionsEnabled ? 'outline' : 'secondary'}
              >
                {operatorActionsEnabled ? 'Lock operator actions' : 'Enable simulated operator actions'}
              </Button> : null}
            </div>
            {snapshot.proposals.length === 0 ? (
              <EmptyState description="No proposals are waiting in this snapshot." title="Proposal queue is empty" />
            ) : (
              snapshot.proposals.map(proposal => (
                <ProposalDetails
                  actionLocked={actionLocked}
                  key={proposal.id}
                  onAction={setPendingAction}
                  proposal={proposal}
                />
              ))
            )}
          </div>
        ) : null}
        {section === 'portfolio' ? (
          <PaperPortfolio onOpenAudit={() => setSection('audit')} snapshot={snapshot} />
        ) : null}
        {section === 'launch' ? (
          <div className={cn('py-5', PAGE_INSET_X)}>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">Governed launch control</h2>
                <p className="mt-1 text-xs text-(--ui-text-tertiary)">
                  Read-only by default · simulated state changes require confirmation
                </p>
              </div>
              {!liveRuntime ? <Button
                onClick={() => setOperatorActionsEnabled(enabled => !enabled)}
                size="sm"
                variant={operatorActionsEnabled ? 'outline' : 'secondary'}
              >
                {operatorActionsEnabled ? 'Lock operator actions' : 'Enable simulated operator actions'}
              </Button> : null}
            </div>
            <LaunchControl actionLocked={actionLocked} onAction={setPendingAction} snapshot={snapshot} />
          </div>
        ) : null}
        {section === 'executions' || section === 'reconciliation' ? (
          <div className={cn('py-5', PAGE_INSET_X)}>
            <div className="mb-4">
              <h2 className="text-sm font-semibold">
                {section === 'executions' ? 'Simulated executions' : 'Execution reconciliation'}
              </h2>
              <p className="mt-1 text-xs text-(--ui-text-tertiary)">
                Immutable receipts · outcome-uncertain states never retry automatically
              </p>
            </div>
            <ExecutionTable snapshot={snapshot} />
          </div>
        ) : null}
        {section === 'audit' ? (
          <div className={cn('py-5', PAGE_INSET_X)}>
            <div className="mb-4">
              <h2 className="text-sm font-semibold">Chronological audit evidence</h2>
              <p className="mt-1 text-xs text-(--ui-text-tertiary)">Expand a row to inspect sanitized event details.</p>
            </div>
            <AuditTable events={snapshot.auditEvents} />
          </div>
        ) : null}
        {section === 'settings' ? (
          <div className={cn('py-5', PAGE_INSET_X)}>
            <h2 className="text-sm font-semibold">Sigil settings</h2>
            <dl className="mt-4 max-w-xl divide-y divide-(--ui-stroke-tertiary) border-y border-(--ui-stroke-tertiary)">
              {[
                ['Product theme', 'Dark institutional'],
                ['Execution mode', 'Paper and simulation only'],
                [
                  'Analysis assistance',
                  hermesStatus?.status === 'connected'
                    ? 'Governed Python bridge'
                    : 'Local safety fallback'
                ],
                ['Paper sizing policy', snapshot.firstLaunchLimit],
                ['Account display', 'Masked identifiers only']
              ].map(([label, value]) => (
                <div className="flex justify-between gap-6 py-3" key={label}>
                  <dt className="text-(--ui-text-tertiary)">{label}</dt>
                  <dd className="font-mono text-xs">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-4 text-xs text-(--ui-text-tertiary)">
              Provider credentials stay in the local backend. Provider access is read-only; live submission and
              capital-limit controls are not available in this product.
            </p>
            {adapter.resetPaperRuntime ? (
              <section className="mt-6 max-w-3xl border border-destructive/30 bg-destructive/5 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-xs font-semibold uppercase tracking-[0.08em]">Local paper ledger reset</h3>
                    <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
                      Clears only simulated holdings, proposals, and fills after writing hash-chained reset evidence.
                      Settings, credentials, and provider access are preserved.
                    </p>
                  </div>
                  <Button onClick={() => setPendingPaperReset(true)} size="sm" variant="destructive">
                    Reset local paper portfolio
                  </Button>
                </div>
              </section>
            ) : null}

            <section
              aria-labelledby="hermes-intelligence-title"
              className="mt-6 max-w-3xl border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary)"
            >
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-(--ui-stroke-tertiary) px-4 py-3">
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-[0.08em]" id="hermes-intelligence-title">
                    Hermes Intelligence
                  </h3>
                  <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">Analysis and explanation only</p>
                </div>
                <StatusLabel tone={hermesStatus?.status === 'connected' ? 'success' : 'danger'}>
                  {(hermesStatus?.status ?? engine.status).toUpperCase()}
                </StatusLabel>
              </div>

              <dl className="grid gap-px bg-(--ui-stroke-tertiary) sm:grid-cols-2">
                {[
                  ['Route', hermesStatus?.modelRoute ?? 'local-disconnected'],
                  ['Mode', 'Analysis only'],
                  ['Execution authorization', 'Never'],
                  ['Broker submission', 'Unavailable']
                ].map(([label, value]) => (
                  <div className="bg-(--ui-bg-primary) px-4 py-3" key={label}>
                    <dt className="text-[0.625rem] uppercase tracking-[0.1em] text-(--ui-text-tertiary)">{label}</dt>
                    <dd className="mt-1 font-mono text-xs">{value}</dd>
                  </div>
                ))}
              </dl>

              <div className="px-4 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium">
                      {selectedProposal
                        ? `${selectedProposal.id} · ${selectedProposal.symbol} ${selectedProposal.side}`
                        : 'No proposal selected'}
                    </p>
                    <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
                      Explanation uses local proposal and evidence metadata.
                    </p>
                  </div>
                  <Button
                    disabled={!selectedProposal || hermesLoading}
                    onClick={() => void explainSelectedProposal()}
                    size="sm"
                    variant="outline"
                  >
                    {hermesLoading ? 'Explaining…' : 'Explain selected proposal'}
                  </Button>
                </div>

                {hermesError ? (
                  <p className="mt-4 text-xs text-destructive" role="alert">
                    {hermesError}
                  </p>
                ) : null}

                {hermesAnalysis ? (
                  <div className="mt-4 border-l-2 border-primary/60 pl-4" data-testid="hermes-analysis">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusLabel tone="muted">{hermesAnalysis.source.toUpperCase()}</StatusLabel>
                      <span className="font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
                        {hermesAnalysis.modelRoute}
                      </span>
                    </div>
                    <p className="mt-3 text-xs font-semibold">{hermesAnalysis.summary}</p>
                    <p className="mt-2 text-xs leading-relaxed text-(--ui-text-secondary)">
                      {hermesAnalysis.explanation}
                    </p>
                    <p className="mt-3 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
                      Execution authorized: no · Broker submission available: no
                    </p>
                  </div>
                ) : null}
              </div>
            </section>
          </div>
        ) : null}
      </div>
      <footer
        className={cn(
          'flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-(--ui-stroke-tertiary) py-2 text-[0.6875rem] text-(--ui-text-tertiary)',
          PAGE_INSET_X
        )}
      >
        <span>No broker submission available · no live submit control · account identity masked</span>
        <span>
          Adapter: governed local paper runtime · provider reads isolated in backend · {RELEASE_STAGE}
          {desktopApi()?.buildInfo
            ? ` · v${desktopApi()?.buildInfo?.version} · BUILD ${desktopApi()?.buildInfo?.build}`
            : ''}
        </span>
      </footer>
      <ConfirmDialog
        confirmLabel={confirmation?.label}
        description={confirmation?.description}
        destructive={confirmation?.destructive}
        onClose={() => setPendingAction(null)}
        onConfirm={async () => {
          if (pendingAction) {
            setSnapshot(await adapter.applySimulatedAction(pendingAction))
          }
        }}
        open={Boolean(pendingAction)}
        title={confirmation?.title}
      />
      <ConfirmDialog
        confirmLabel={pendingCycleAction ? `${pendingCycleAction[0]?.toUpperCase()}${pendingCycleAction.slice(1)} paper automation` : undefined}
        description={
          pendingCycleAction
            ? `This will ${pendingCycleAction} only the local paper cycle and append audit evidence. It cannot submit to a broker.`
            : undefined
        }
        destructive={pendingCycleAction === 'stop'}
        onClose={() => setPendingCycleAction(null)}
        onConfirm={async () => {
          if (pendingCycleAction && adapter.controlPaperCycle) {
            try {
              setControlError(null)
              setSnapshot(await adapter.controlPaperCycle(pendingCycleAction))
            } catch (reason) {
              setControlError(reason instanceof Error ? reason.message : String(reason))
            }
          }

          setPendingCycleAction(null)
        }}
        open={Boolean(pendingCycleAction)}
        title={pendingCycleAction ? `Confirm paper automation ${pendingCycleAction}` : undefined}
      />
      <ConfirmDialog
        confirmLabel={pendingAuthorizationAction === 'grant' ? 'Authorize paper automation' : 'Revoke authorization'}
        description={
          pendingAuthorizationAction === 'grant'
            ? 'Authorize automatic approval and simulated buys and sells for 30 days in this local paper ledger? Dynamic paper sizing, audit evidence, and oversell prevention remain enforced. No broker or provider mutation is possible.'
            : pendingAuthorizationAction === 'revoke'
              ? 'Revoke the local monthly paper authorization now? Running automation will pause and further automatic approvals and simulated fills will be denied.'
              : undefined
        }
        destructive={pendingAuthorizationAction === 'revoke'}
        onClose={() => setPendingAuthorizationAction(null)}
        onConfirm={async () => {
          if (pendingAuthorizationAction && adapter.controlPaperAuthorization) {
            try {
              setControlError(null)
              setSnapshot(await adapter.controlPaperAuthorization(pendingAuthorizationAction))
            } catch (reason) {
              setControlError(reason instanceof Error ? reason.message : String(reason))
            }
          }

          setPendingAuthorizationAction(null)
        }}
        open={Boolean(pendingAuthorizationAction)}
        title={pendingAuthorizationAction === 'grant' ? 'Confirm monthly paper authorization' : 'Confirm authorization revocation'}
      />
      <ConfirmDialog
        confirmLabel="Reset empty paper ledger"
        description="This records a hash-chained reset receipt, then clears only the local simulated cash ledger history, holdings, proposals, and fills. Application settings, local provider credentials, source files, and all broker restrictions remain unchanged."
        destructive
        onClose={() => setPendingPaperReset(false)}
        onConfirm={async () => {
          if (adapter.resetPaperRuntime) {
            try {
              setControlError(null)
              setSnapshot(await adapter.resetPaperRuntime())
              setSection('portfolio')
            } catch (reason) {
              setControlError(reason instanceof Error ? reason.message : String(reason))
            }
          }

          setPendingPaperReset(false)
        }}
        open={pendingPaperReset}
        title="Confirm local paper portfolio reset"
      />
      {aboutOpen && desktopApi()?.buildInfo ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
          data-sigil-about-overlay
          style={{ display: 'flex' }}
        >
          <section className="w-full max-w-lg border border-(--ui-stroke-secondary) bg-(--ui-bg-primary) shadow-2xl">
            <div className="flex items-center justify-between border-b border-(--ui-stroke-tertiary) px-5 py-4">
              <div>
                <h2 className="text-sm font-semibold">About Sigil</h2>
                <p className="mt-1 text-xs text-(--ui-text-tertiary)">Verified local release identity</p>
              </div>
              <Button aria-label="Close About Sigil" onClick={() => setAboutOpen(false)} size="xs" variant="outline">
                Close
              </Button>
            </div>
            <dl className="grid grid-cols-2 gap-px bg-(--ui-stroke-tertiary)">
              {[
                ['RELEASE STAGE', RELEASE_STAGE],
                ['VERSION', desktopApi()?.buildInfo?.version],
                ['CHANNEL', desktopApi()?.buildInfo?.channel.toUpperCase()],
                ['BUILD ID', desktopApi()?.buildInfo?.build],
                ['COMMIT', desktopApi()?.buildInfo?.commit],
                ['BUILD TIME', desktopApi()?.buildInfo?.buildTime],
                ['APPLICATION MODE', desktopApi()?.buildInfo?.applicationMode]
              ].map(([label, value]) => (
                <div className="bg-(--ui-bg-secondary) px-4 py-3" key={label}>
                  <dt className="text-[0.625rem] tracking-[0.1em] text-(--ui-text-tertiary)">{label}</dt>
                  <dd className="mt-1 break-all font-mono text-xs">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="px-5 py-4">
              <Button
                onClick={() => {
                  void desktopApi()?.checkForUpdates?.().then(result => setUpdateMessage(result.message))
                }}
                size="sm"
                variant="outline"
              >
                Check for Updates
              </Button>
              {updateMessage ? <p className="mt-3 text-xs text-(--ui-text-tertiary)">{updateMessage}</p> : null}
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}
