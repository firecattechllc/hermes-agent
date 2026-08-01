import { PAGE_INSET_X } from '@hermes-desktop/app/layout-constants'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

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
import { GovernedNewsPanel } from './governed-news-panel'
import { sigilOperatorAdapter as mockSigilOperatorAdapter } from './mock-adapter'
import type {
  AlpacaMarketDataStatus,
  AssetCatalogStatus,
  AuditEvent,
  MarketUniverseQuote,
  MarketUniverseSearchResult,
  MarketUniverseStatus,
  PaperExecutionStatus,
  PipelineStage,
  ProductionResearchStatus,
  Proposal,
  SigilOperatorAdapter,
  SigilProviderSnapshot,
  SigilSnapshot,
  SigilTone,
  SimulatedOperatorAction
} from './types'

const RELEASE_STAGE = 'V2.9'

const SECTIONS = ['overview', 'portfolio', 'proposals', 'launch', 'executions', 'reconciliation', 'audit', 'news', 'settings'] as const
type Section = (typeof SECTIONS)[number]

const SECTION_LABELS: Record<Section, string> = {
  overview: 'Overview',
  portfolio: 'Portfolio',
  proposals: 'Proposals',
  launch: 'Launch',
  executions: 'Executions',
  reconciliation: 'Reconciliation',
  audit: 'Audit',
  news: 'News',
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

function timeUntil(timestamp: string | null | undefined): string {
  if (!timestamp) {
    return 'No cycle scheduled'
  }

  const seconds = Math.max(0, Math.ceil((Date.parse(timestamp) - Date.now()) / 1_000))

  if (seconds < 60) {
    return `${seconds}s until next cycle`
  }

  const minutes = Math.ceil(seconds / 60)

  return `${minutes}m until next cycle`
}

function RuntimeVisibilityCard({
  onOpenAudit,
  snapshot
}: {
  onOpenAudit: () => void
  snapshot: SigilSnapshot
}) {
  const visibility = snapshot.runtimeVisibility

  if (!visibility) {
    return null
  }

  const latestProposal = snapshot.proposals[0]
  const latestExecution = snapshot.receipts[0]

  const recentAudit = [...snapshot.auditEvents]
    .sort((left, right) => right.timestamp.localeCompare(left.timestamp))
    .slice(0, 10)

  const stateTone: SigilTone =
    visibility.operationalState === 'running'
      ? 'success'
      : visibility.operationalState === 'blocked'
        ? 'danger'
        : 'warning'

  const healthTone: SigilTone =
    visibility.health === 'healthy'
      ? 'success'
      : visibility.health === 'blocked'
        ? 'danger'
        : 'warning'

  return (
    <section
      aria-labelledby="runtime-visibility-title"
      className="border-b border-(--ui-stroke-tertiary)"
      data-testid="runtime-visibility"
    >
      <div className={cn('py-4', PAGE_INSET_X)}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-[0.1em]" id="runtime-visibility-title">
              Governed runtime status
            </h2>
            <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
              Paper-only · {visibility.automationMode}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusLabel tone={stateTone}>{visibility.operationalState.toUpperCase()}</StatusLabel>
            <StatusLabel tone={healthTone}>{visibility.health.toUpperCase()}</StatusLabel>
            <StatusLabel tone={visibility.connectionState === 'connected' ? 'success' : 'warning'}>
              {visibility.connectionState}
            </StatusLabel>
          </div>
        </div>

        <dl className="mt-4 grid gap-px overflow-hidden border border-(--ui-stroke-tertiary) bg-(--ui-stroke-tertiary) sm:grid-cols-2 xl:grid-cols-4">
          {[
            ['Completed cycles', String(visibility.counts.cycles), `Last: ${snapshot.automationLastCycleAt ?? 'Never'}`],
            [
              'Next scheduled cycle',
              snapshot.automationState === 'running'
                ? snapshot.automationNextCycleAt ?? 'No cycle scheduled'
                : 'No cycle scheduled',
              snapshot.automationState === 'running'
                ? timeUntil(snapshot.automationNextCycleAt)
                : 'Paused or stopped'
            ],
            [
              'Governed records',
              `${visibility.counts.proposals} proposals · ${visibility.counts.executions} executions`,
              `${visibility.counts.reconciliation} reconciliation · ${visibility.counts.auditEvents} audit`
            ],
            [
              'Latest activity',
              latestProposal ? `${latestProposal.id} · ${latestProposal.status}` : 'No proposal',
              latestExecution
                ? `${latestExecution.orderId} · ${latestExecution.brokerStatus}`
                : 'No execution'
            ]
          ].map(([label, value, detail]) => (
            <div className="bg-(--ui-bg-secondary) p-3" key={label}>
              <dt className="text-[0.625rem] uppercase tracking-[0.1em] text-(--ui-text-tertiary)">{label}</dt>
              <dd className="mt-1 break-words font-mono text-xs font-semibold">{value}</dd>
              <dd className="mt-1 break-words text-[0.6875rem] text-(--ui-text-tertiary)">{detail}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div>
            <h3 className="text-[0.625rem] font-semibold uppercase tracking-[0.1em]">Safety and next action</h3>
            <p className="mt-2 text-xs">{visibility.nextAction}</p>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <div className="border border-emerald-500/30 bg-emerald-500/8 p-3 text-xs">
                <strong>Local paper execution:</strong>{' '}
                {visibility.paperExecutionAvailable ? 'available' : 'currently blocked'}
              </div>
              <div className="border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-3 text-xs">
                <strong>Real broker submission:</strong>{' '}
                {visibility.brokerSubmissionAvailable ? 'available' : 'unavailable'}
              </div>
            </div>
            <ul aria-label="Runtime blocking reasons" className="mt-3 space-y-2">
              {visibility.blockingReasons.map(reason => (
                <li
                  className="flex items-start justify-between gap-3 border-l-2 border-(--ui-stroke-tertiary) pl-3 text-xs"
                  key={reason.code}
                >
                  <span>
                    <span className="block">{reason.summary}</span>
                    <span className="font-mono text-[0.625rem] text-(--ui-text-quaternary)">{reason.code}</span>
                  </span>
                  <StatusLabel
                    tone={reason.severity === 'critical' ? 'danger' : reason.severity === 'warning' ? 'warning' : 'info'}
                  >
                    {reason.severity}
                  </StatusLabel>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-[0.625rem] font-semibold uppercase tracking-[0.1em]">Recent audit timeline</h3>
              <Button onClick={onOpenAudit} size="xs" variant="outline">View all</Button>
            </div>
            <ol className="mt-2 divide-y divide-(--ui-stroke-tertiary)">
              {recentAudit.map(event => (
                <li className="py-2 text-[0.6875rem]" key={event.id}>
                  <div className="flex items-start justify-between gap-3">
                    <span>{event.summary}</span>
                    <StatusLabel tone="muted">{event.status}</StatusLabel>
                  </div>
                  <div className="mt-1 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
                    {event.timestamp} · proposal {event.proposalId} · order {event.orderId} · {event.evidenceReference}
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </section>
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
  const positions = snapshot.positions ?? []
  const emptyHoldingSlots = Math.max(0, 10 - positions.length)
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
          ['Unrealized P&L', snapshot.unrealizedPnl ?? 'Unavailable'],
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
              Quantity, cost, allocation, and P&L use persisted validated position marks.
            </p>
          </div>
          <span className="font-mono text-[0.625rem] text-(--ui-text-tertiary)">
            {positions.length} of 10 paper slots occupied
          </span>
        </div>
        {positions.length ? (
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
                {positions.map(position => (
                  <tr className="border-b border-(--ui-stroke-tertiary) last:border-b-0" key={position.symbol}>
                    <td className="px-3 py-3 font-mono font-semibold">{position.symbol}</td>
                    <td className="px-3 py-3 font-mono">{position.quantity}</td>
                    <td className="px-3 py-3 font-mono">{position.averageCost}</td>
                    <td className="px-3 py-3 font-mono">{position.marketValue}</td>
                    <td className="px-3 py-3 font-mono">{position.allocation}</td>
                    <td className="px-3 py-3 font-mono">
                      <span className="block">{position.unrealizedPnl}</span>
                      <span className="mt-1 block text-[0.625rem] text-(--ui-text-tertiary)">
                        {position.valuationStatus === 'fresh'
                          ? `${position.markPrice ?? 'Marked'} · ${position.markTimestamp ?? 'timestamp unavailable'}`
                          : `${position.valuationStatus} mark`}
                      </span>
                    </td>
                    <td className="px-3 py-3 font-mono">{position.realizedPnl}</td>
                    <td className="px-3 py-3">
                      <button className="font-mono text-[0.6875rem] text-primary hover:underline" onClick={onOpenAudit} type="button">
                        {position.auditReferences[0] ?? 'Inspect audit'}
                      </button>
                    </td>
                  </tr>
                ))}
                {Array.from({ length: emptyHoldingSlots }, (_, index) => (
                  <tr className="border-b border-(--ui-stroke-tertiary) last:border-b-0 text-(--ui-text-tertiary)" key={`empty-slot-${index}`}>
                    <td className="px-3 py-3 font-mono">Empty slot {index + 1}</td>
                    <td className="px-3 py-3" colSpan={7}>Available for a future paper position; no simulated holding.</td>
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
  alpacaMarketData,
  alpacaControlAction,
  alpacaControlMessage,
  onAlpacaControl,
  error,
  loading,
  onRefresh,
  snapshot
}: {
  alpacaMarketData: AlpacaMarketDataStatus | null
  alpacaControlAction: string | null
  alpacaControlMessage: string | null
  onAlpacaControl: (action: string) => void
  error: string | null
  loading: boolean
  onRefresh: () => void
  snapshot: SigilProviderSnapshot | null
}) {
  const [freshnessNow, setFreshnessNow] = useState(() => Date.now())
  const [freshnessObservedAt, setFreshnessObservedAt] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setFreshnessNow(Date.now()), 5_000)

    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    setFreshnessObservedAt(Date.now())
  }, [alpacaMarketData])

  const catalogAgeSeconds = (alpacaMarketData?.asset_catalog.age_seconds ?? 0)
    + Math.max(0, Math.floor((freshnessNow - freshnessObservedAt) / 1_000))

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
      {alpacaMarketData ? (
        <div className={cn('border-t border-(--ui-stroke-tertiary) py-4', PAGE_INSET_X)}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-xs font-semibold">Alpaca Market Data</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                <StatusLabel tone={alpacaMarketData.configured ? 'success' : 'muted'}>
                  {alpacaMarketData.authenticated ? 'Authenticated' : 'Unconfigured'}
                </StatusLabel>
                <StatusLabel tone={alpacaMarketData.provider_state === 'ready' ? 'success' : 'warning'}>
                  Market data {alpacaMarketData.provider_state.replaceAll('_', ' ')}
                </StatusLabel>
                <StatusLabel tone="warning">15-minute delayed SIP</StatusLabel>
                <StatusLabel tone="info">live partial-market IEX</StatusLabel>
                <StatusLabel tone="muted">Data-only mode</StatusLabel>
                <StatusLabel tone="danger">Live trading disabled</StatusLabel>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={alpacaControlAction !== null}
                onClick={() => onAlpacaControl('refresh_assets')}
                size="xs"
                variant="outline"
              >
                {alpacaControlAction === 'refresh_assets' ? 'Refreshing Alpaca assets…' : 'Refresh Alpaca assets'}
              </Button>
              <Button disabled size="xs" title="Delayed-SIP scanning is not available in this build." variant="outline">Start delayed-SIP scan</Button>
              <Button disabled size="xs" title="No delayed-SIP scan is running in this build." variant="outline">Stop delayed-SIP scan</Button>
              <Button disabled size="xs" title="Streaming IEX connections are not available in this build." variant="outline">Connect live IEX</Button>
              <Button disabled size="xs" title="No streaming IEX connection is active in this build." variant="outline">Disconnect live IEX</Button>
            </div>
          </div>
          <p className="mt-3 text-[0.6875rem] text-(--ui-text-tertiary)" role="status">
            {alpacaControlMessage ??
              'Asset refresh is read-only. Delayed-SIP and streaming IEX controls are unavailable in this build.'}
          </p>
          <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
            <div><dt className="text-(--ui-text-tertiary)">Asset catalog</dt><dd>{alpacaMarketData.asset_catalog.accepted_count} accepted · {alpacaMarketData.asset_catalog.excluded_count} excluded · {alpacaMarketData.asset_catalog.conflict_count} conflicts</dd></div>
            <div><dt className="text-(--ui-text-tertiary)">Catalog freshness</dt><dd>{alpacaMarketData.asset_catalog.stale ? 'Cached / stale' : `${catalogAgeSeconds}s old`}</dd></div>
            <div><dt className="text-(--ui-text-tertiary)">Delayed SIP scan</dt><dd>{alpacaMarketData.delayed_sip.scanned_count}/{alpacaMarketData.delayed_sip.universe_total} · batch {alpacaMarketData.delayed_sip.current_batch}/{alpacaMarketData.delayed_sip.total_batches}</dd></div>
            <div><dt className="text-(--ui-text-tertiary)">Live IEX capacity</dt><dd>{alpacaMarketData.live_iex.active_symbol_count}/{alpacaMarketData.live_iex.maximum_symbol_count} symbols · {alpacaMarketData.live_iex.stale ? 'stale/unavailable' : 'current'}</dd></div>
          </dl>
          <p className="mt-3 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
            Subscribed: {alpacaMarketData.live_iex.subscribed_symbols.join(', ') || 'none'} · Last message: {alpacaMarketData.live_iex.last_message_at ?? 'none'} · Provider: {alpacaMarketData.provider_state} · Error: {alpacaMarketData.asset_catalog.last_error ?? 'none'}
          </p>
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
                <h3 className="text-xs font-semibold">Alpaca catalog provider status</h3>
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
                      The full Alpaca asset catalog and the governed proposal universe are separate. IEX market data is partial-market, and broader SIP history remains delayed by 15 minutes.
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

function MarketUniversePanel({
  error,
  loading,
  onRefresh,
  onSearch,
  refreshing,
  results,
  status
}: {
  error: string | null
  loading: boolean
  onRefresh: () => void
  onSearch: (query: string, universe: string) => void
  refreshing: boolean
  results: MarketUniverseSearchResult | null
  status: MarketUniverseStatus | null
}) {
  const [query, setQuery] = useState('')
  const [universe, setUniverse] = useState('master')
  const [quotes, setQuotes] = useState<Readonly<Record<string, MarketUniverseQuote>>>({})
  const [quoteLoading, setQuoteLoading] = useState(false)
  const [quoteError, setQuoteError] = useState<string | null>(null)

  const applySearch = (nextQuery: string, nextUniverse = universe): void => {
    setQuery(nextQuery)
    onSearch(nextQuery, nextUniverse)
  }

  const rankedResults = useMemo(() => {
    const rows = results?.results ?? []
    const normalizedQuery = results?.query.trim().toUpperCase() ?? ''

    return [...rows].sort((left, right) => {
      const rank = (symbol: string, name: string): number => {
        const normalizedSymbol = symbol.toUpperCase()
        const normalizedName = name.toUpperCase()

        if (normalizedSymbol === normalizedQuery) {
          return 0
        }
        if (normalizedSymbol.startsWith(normalizedQuery)) {
          return 1
        }
        if (normalizedName.startsWith(normalizedQuery)) {
          return 2
        }
        if (normalizedName.includes(normalizedQuery)) {
          return 3
        }

        return 4
      }

      return (
        rank(left.symbol, left.name) - rank(right.symbol, right.name) ||
        left.symbol.localeCompare(right.symbol)
      )
    })
  }, [results])

  useEffect(() => {
    const api = window.sigilDesktop?.getMarketUniverseQuotes
    const symbols = rankedResults.slice(0, 20).map(item => item.symbol)

    if (!api || symbols.length === 0) {
      setQuotes({})
      setQuoteError(null)

      return
    }

    let cancelled = false

    const refreshQuotes = async (): Promise<void> => {
      setQuoteLoading(true)

      try {
        const response = await api({ symbols })

        if (cancelled) {
          return
        }

        if (!response.ok) {
          setQuoteError(response.message)
          setQuotes({})

          return
        }

        setQuoteError(null)
        setQuotes(
          Object.fromEntries(
            response.result.quotes.map(quote => [quote.symbol, quote])
          )
        )
      } catch (reason) {
        if (!cancelled) {
          setQuoteError(reason instanceof Error ? reason.message : String(reason))
          setQuotes({})
        }
      } finally {
        if (!cancelled) {
          setQuoteLoading(false)
        }
      }
    }

    void refreshQuotes()

    const timer = window.setInterval(() => {
      void refreshQuotes()
    }, 15_000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [rankedResults])

  const formatPrice = (quote: MarketUniverseQuote | undefined): string => {
    if (!quote || quote.price === null) {
      return '—'
    }

    return quote.price.toLocaleString(undefined, {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })
  }

  const formatChange = (quote: MarketUniverseQuote | undefined): string => {
    if (!quote || quote.change_percent === null) {
      return '—'
    }

    const prefix = quote.change_percent > 0 ? '+' : ''

    return `${prefix}${quote.change_percent.toFixed(2)}%`
  }

  const quoteAge = (quote: MarketUniverseQuote | undefined): string => {
    if (!quote || quote.age_seconds === null) {
      return 'age unavailable'
    }

    if (quote.age_seconds < 60) {
      return `${quote.age_seconds}s ago`
    }

    if (quote.age_seconds < 3_600) {
      return `${Math.floor(quote.age_seconds / 60)}m ago`
    }

    return `${Math.floor(quote.age_seconds / 3_600)}h ago`
  }

  return (
    <section className={cn('border-b border-(--ui-stroke-tertiary) py-4', PAGE_INSET_X)} data-testid="market-universe">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-[0.1em]">Governed market universe</h2>
          <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
            Canonical identity, source reconciliation, lifecycle controls, and deterministic monitoring tiers
          </p>
        </div>
        <StatusLabel tone={status?.target_capacity_validated ? 'success' : 'warning'}>
          {status?.catalog_scope ?? 'Catalog unavailable'}
        </StatusLabel>
        <Button disabled={refreshing} onClick={onRefresh} size="xs" variant="outline">
          <Codicon name="refresh" />
          {refreshing ? 'Refreshing catalog…' : 'Refresh catalog'}
        </Button>
      </div>
      {status ? (
        <>
          <div className="mt-3 grid gap-px bg-(--ui-stroke-tertiary) sm:grid-cols-3 xl:grid-cols-6">
            {[
              ['Discovered', status.source_record_count],
              ['Active', status.active_count],
              ['Tradable', status.broker_tradable_count],
              ['Fractionable', status.fractionable_count],
              ['Proposal eligible', status.proposal_eligible_count],
              ['Excluded', status.excluded_count]
            ].map(([label, value]) => (
              <div className="bg-(--ui-bg-secondary) p-3" key={label}>
                <div className="text-[0.625rem] uppercase text-(--ui-text-tertiary)">{label}</div>
                <div className="mt-1 font-mono text-sm">{value}</div>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[0.6875rem] text-amber-700 dark:text-amber-300">
            Coverage boundary: {status.coverage_limitation}
          </p>
          <p className="mt-1 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
            Source: {status.catalog_source} · catalog: {status.cache_state} · age: {status.cache_age_seconds ?? 'unavailable'}s · integrity: {status.integrity} · execution: paper only · broker submission disabled
          </p>
        </>
      ) : null}
      <div className="mt-4 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
        <SearchField
          aria-label="Search governed instruments"
          containerClassName="min-w-0"
          onChange={setQuery}
          placeholder="Search ticker or company name"
          value={query}
        />
        <Button
          disabled={loading || !query.trim() || !status?.target_capacity_validated}
          onClick={() => applySearch(query)}
          size="sm"
        >
          <Codicon name="search" />
          {loading ? 'Searching…' : 'Search'}
        </Button>
        <select
          aria-label="Filter governed universe"
          className="border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) px-2 text-xs"
          onChange={event => {
            setUniverse(event.target.value)
            onSearch(query, event.target.value)
          }}
          value={universe}
        >
          <option value="master">Master</option>
          <option value="broker_tradable">Broker tradable</option>
          <option value="actively_researched">Actively researched</option>
          <option value="proposal_eligible">Proposal eligible</option>
          <option value="excluded">Excluded</option>
        </select>
      </div>
      {error ? <p className="mt-3 text-xs text-destructive">{error}</p> : null}
      {loading ? <div className="mt-3"><Loader label="Searching governed universe" /></div> : null}
      {!status?.target_capacity_validated ? (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
          Catalog unavailable — refresh the asset catalog before searching.
        </p>
      ) : null}
      {!loading && results && results.results.length === 0 ? (
        <EmptyState
          description={`No governed instruments matched “${results.query}” in the selected universe.`}
          title="No matching instruments"
        />
      ) : null}
      {quoteError ? (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
          Quotes unavailable: {quoteError}. Catalog results remain available.
        </p>
      ) : null}
      {!loading && rankedResults.length > 0 ? (
        <div className="mt-4 overflow-hidden border border-(--ui-stroke-tertiary)">
          <div className="hidden grid-cols-[minmax(14rem,1fr)_8rem_7rem_8rem_10rem] gap-4 border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) px-4 py-2 text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-(--ui-text-tertiary) md:grid">
            <span>Instrument</span>
            <span className="text-right">Price</span>
            <span className="text-right">Change</span>
            <span>Exchange</span>
            <span>Market data</span>
          </div>

          <div className="divide-y divide-(--ui-stroke-tertiary)">
            {rankedResults.map(item => {
              const quote = quotes[item.symbol]
              const changePositive =
                quote?.change_percent !== null &&
                quote?.change_percent !== undefined &&
                quote.change_percent > 0
              const changeNegative =
                quote?.change_percent !== null &&
                quote?.change_percent !== undefined &&
                quote.change_percent < 0

              return (
                <div
                  className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(14rem,1fr)_8rem_7rem_8rem_10rem] md:items-center md:gap-4"
                  key={item.instrument_id}
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-bold">{item.symbol}</span>
                      {item.proposal_eligible ? (
                        <StatusLabel tone="success">ELIGIBLE</StatusLabel>
                      ) : (
                        <StatusLabel tone="muted">CATALOG ONLY</StatusLabel>
                      )}
                    </div>
                    <p className="mt-1 truncate text-xs text-(--ui-text-secondary)">
                      {item.name}
                    </p>
                    <p className="mt-1 text-[0.625rem] text-(--ui-text-quaternary)">
                      {item.asset_class.replaceAll('_', ' ')}
                      {item.broker_tradable ? ' · broker tradable' : ' · not tradable'}
                    </p>
                  </div>

                  <div className="md:text-right">
                    <span className="mr-2 text-[0.625rem] uppercase text-(--ui-text-tertiary) md:hidden">
                      Price
                    </span>
                    <span className="font-mono text-sm font-semibold">
                      {quoteLoading && !quote ? 'Loading…' : formatPrice(quote)}
                    </span>
                  </div>

                  <div className="md:text-right">
                    <span className="mr-2 text-[0.625rem] uppercase text-(--ui-text-tertiary) md:hidden">
                      Change
                    </span>
                    <span
                      className={cn(
                        'font-mono text-xs font-semibold',
                        changePositive && 'text-emerald-500',
                        changeNegative && 'text-rose-500',
                        !changePositive && !changeNegative && 'text-(--ui-text-tertiary)'
                      )}
                    >
                      {formatChange(quote)}
                    </span>
                  </div>

                  <div>
                    <span className="mr-2 text-[0.625rem] uppercase text-(--ui-text-tertiary) md:hidden">
                      Exchange
                    </span>
                    <span className="font-mono text-xs">{item.exchange}</span>
                  </div>

                  <div>
                    <p className="text-xs font-medium">{quote?.source ?? 'Price unavailable'}</p>
                    <p className="mt-1 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
                      {quoteAge(quote)}
                    </p>
                  </div>
                </div>
              )
            })}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) px-4 py-2 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
            <span>
              Showing {rankedResults.length} of {results?.total ?? 0}
            </span>
            <span>
              Quotes refresh every 15s · read-only Alpaca IEX · broker submission disabled
            </span>
          </div>
        </div>
      ) : null}
    </section>
  )
}

function AutonomousPaperPanel({
  onAction,
  status
}: {
  onAction: (action: 'deactivate') => void
  status: PaperExecutionStatus | null
}) {
  if (!status) {
    return (
      <section className={cn('border-b border-(--ui-stroke-tertiary) py-4', PAGE_INSET_X)}>
        <h2 className="text-xs font-semibold uppercase tracking-[0.1em]">Autonomous paper execution</h2>
        <p className="mt-2 text-xs text-(--ui-text-tertiary)">Execution status unavailable. No submission authority is assumed.</p>
      </section>
    )
  }

  const progress = status.progress
  const reasons = Object.entries(progress.leading_rejection_reasons)

  const active =
    status.activated &&
    !status.paused &&
    !status.kill_switch &&
    status.broker_submission

  return (
    <section
      aria-labelledby="autonomous-paper-title"
      className={cn('border-b border-(--ui-stroke-tertiary) py-4', PAGE_INSET_X)}
      data-testid="autonomous-paper-execution"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-[0.1em]" id="autonomous-paper-title">
            Governed autonomous Alpaca paper execution
          </h2>
          <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
            Long-only · $1,000/order · 10 positions · $10,000 deployed cap · $100 cash buffer
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusLabel tone={active ? 'success' : 'muted'}>
            {active ? 'ENABLED' : status.paused ? 'PAUSED' : 'DISABLED'}
          </StatusLabel>
          <StatusLabel tone={status.broker_submission ? 'warning' : 'muted'}>
            SUBMISSION {status.broker_submission ? 'ENABLED' : 'DISABLED'}
          </StatusLabel>
          <StatusLabel tone="success">LIVE EXECUTION DISABLED</StatusLabel>
        </div>
      </div>
      <div className="mt-3 grid gap-px bg-(--ui-stroke-tertiary) sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['Research state', progress.state.replaceAll('_', ' '), progress.scheduler_state],
          ['Catalog progress', `${progress.current_cursor} / ${progress.total_eligible_symbols}`, `${progress.coverage_percent}% · batch ${progress.current_batch}`],
          ['Current batch', `${progress.symbols_in_batch.length} symbols`, progress.symbols_in_batch.join(', ') || 'Awaiting batch'],
          ['Last researched', progress.last_completed_symbol ?? '—', progress.last_successful_research_at ?? 'Never'],
          ['Candidates', String(progress.candidates_produced), `${progress.proposals_produced} proposals`],
          ['Rejected', String(progress.proposals_rejected), reasons.map(([reason, count]) => `${reason}: ${count}`).join(' · ') || 'None'],
          ['Paper exposure', `$${status.deployed_paper_capital}`, `$${status.remaining_governed_allocation} governed allocation remains`],
          ['Broker state', status.broker_submission ? 'Paper mutations enabled' : 'No mutation authority', `${status.open_positions} positions · ${status.open_orders} open orders`]
        ].map(([label, value, detail]) => (
          <div className="min-w-0 bg-(--ui-bg-secondary) p-3" key={label}>
            <div className="text-[0.625rem] uppercase text-(--ui-text-tertiary)">{label}</div>
            <div className="mt-1 break-words font-mono text-xs">{value}</div>
            <div className="mt-1 truncate text-[0.625rem] text-(--ui-text-tertiary)" title={detail}>{detail}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {active ? (
          <Button onClick={() => onAction('deactivate')} size="xs" variant="destructive">Disable</Button>
        ) : null}
      </div>
      <p className="mt-3 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
        Environment: paper · broker: Alpaca paper · endpoint: paper-api.alpaca.markets · live activation unavailable
      </p>
    </section>
  )
}

function ProductionResearchPanel({ status }: { status: ProductionResearchStatus | null }) {
  if (!status) {
    return (
      <section className={cn('border-b border-(--ui-stroke-tertiary) py-4', PAGE_INSET_X)}>
        <h2 className="text-xs font-semibold uppercase tracking-[0.1em]">Production research and shadow validation</h2>
        <p className="mt-2 text-xs text-(--ui-text-tertiary)">Research status unavailable. No proposal or execution authority is assumed.</p>
      </section>
    )
  }

  const progress = status.progress
  const reasons = Object.entries(progress.leading_rejection_reasons)

  return (
    <section
      aria-labelledby="production-research-title"
      className={cn('border-b border-(--ui-stroke-tertiary) py-4', PAGE_INSET_X)}
      data-testid="production-research"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-[0.1em]" id="production-research-title">
            Validated production research and shadow mode
          </h2>
          <p className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
            {status.strategy_id} · v{status.strategy_version} · deterministic liquid-trend evidence
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusLabel tone={status.shadow_mode ? 'success' : 'warning'}>
            SHADOW {status.shadow_mode ? 'ENABLED' : 'DISABLED'}
          </StatusLabel>
          <StatusLabel tone={status.promotion.ready ? 'success' : 'warning'}>
            {status.promotion.status.replaceAll('_', ' ').toUpperCase()}
          </StatusLabel>
          <StatusLabel tone="success">LIVE EXECUTION DISABLED</StatusLabel>
        </div>
      </div>
      <div className="mt-3 grid gap-px bg-(--ui-stroke-tertiary) sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['Research state', progress.state.replaceAll('_', ' '), `${progress.provider_status} · ${progress.market_data_freshness}`],
          ['Batch and cursor', `${progress.current_batch} · ${progress.current_cursor}`, `${progress.symbols_researched} symbols researched`],
          [
            'Evidence',
            `${progress.evidence_complete_count ?? 0} complete · ${progress.evidence_incomplete_count ?? 0} incomplete`,
            `${progress.scored_count ?? progress.research_successes} scored · ${progress.hard_rejected_count ?? progress.research_failures} hard-gate rejected; counts may overlap`
          ],
          ['Candidates', String(progress.candidates_produced), `${progress.proposals_generated} proposals generated`],
          ['Shadow positions', String(status.active_shadow_positions), `${status.completed_shadow_outcomes} completed outcomes`],
          ['Shadow performance', status.shadow_simulated_return, `${status.shadow_win_rate} simulated win rate`],
          ['Promotion', status.promotion.status.replaceAll('_', ' '), status.promotion.failed_conditions.join(' · ') || 'All readiness checks satisfied'],
          ['Leading rejections', reasons.length ? reasons.map(([reason, count]) => `${reason}: ${count}`).join(' · ') : 'None', progress.last_completed_research ?? 'No completed research']
        ].map(([label, value, detail]) => (
          <div className="min-w-0 bg-(--ui-bg-secondary) p-3" key={label}>
            <div className="text-[0.625rem] uppercase text-(--ui-text-tertiary)">{label}</div>
            <div className="mt-1 break-words font-mono text-xs">{value}</div>
            <div className="mt-1 truncate text-[0.625rem] text-(--ui-text-tertiary)" title={detail}>{detail}</div>
          </div>
        ))}
      </div>
      <p className="mt-3 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
        Production evidence only · simulated shadow outcomes · no profitability claim · no broker order in shadow mode
      </p>
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
  getAlpacaMarketDataStatus?: () => Promise<
    { ok: true; result: AlpacaMarketDataStatus } | { ok: false; error: string; message: string }
  >
  controlAlpacaMarketData?: (action: string) => Promise<
    { ok: true; result: AlpacaMarketDataStatus } | { ok: false; error: string; message: string }
  >
  getMarketUniverseStatus?: () => Promise<
    { ok: true; result: MarketUniverseStatus } | { ok: false; error: string; message: string }
  >
  searchMarketUniverse?: (
    payload: Readonly<Record<string, unknown>>
  ) => Promise<
    { ok: true; result: MarketUniverseSearchResult } | { ok: false; error: string; message: string }
  >
  getAssetCatalogStatus?: () => Promise<
    { ok: true; result: AssetCatalogStatus } | { ok: false; error: string; message: string }
  >
  refreshAssetCatalog?: () => Promise<
    { ok: true; result: AssetCatalogStatus } | { ok: false; error: string; message: string }
  >
  paperExecution?: (
    operation: string,
    payload?: Readonly<Record<string, unknown>>
  ) => Promise<
    { ok: true; result: PaperExecutionStatus } | { ok: false; error: string; message: string }
  >
  productionResearch?: (
    operation: string,
    payload?: Readonly<Record<string, unknown>>
  ) => Promise<
    { ok: true; result: ProductionResearchStatus } | { ok: false; error: string; message: string }
  >
  buildInfo?: {
    version: string
    build: string
    commit: string
    buildTime: string
    channel: 'dev' | 'release'
    applicationMode: 'Live development' | 'Packaged release'
  }
  getUpdaterSnapshot?: () => Promise<UpdaterSnapshot>
  checkForUpdates?: () => Promise<UpdaterSnapshot>
  approveUpdateDownload?: () => Promise<UpdaterSnapshot>
  deferUpdate?: () => Promise<UpdaterSnapshot>
  restartAndInstallUpdate?: () => Promise<UpdaterSnapshot>
  subscribeToUpdaterState?: (listener: (snapshot: UpdaterSnapshot) => void) => () => void
}

type UpdaterSnapshot = {
  status: 'idle' | 'checking' | 'update-available' | 'up-to-date' | 'downloading' | 'downloaded' | 'installing' | 'deferred' | 'failed' | 'disabled'
  currentVersion: string
  availableVersion: string | null
  releaseNotes: string | null
  progress: { percent: number; transferred: number; total: number; bytesPerSecond: number } | null
  internalTest: boolean
  message: string
  error: { code: string; message: string } | null
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
  const [alpacaMarketData, setAlpacaMarketData] = useState<AlpacaMarketDataStatus | null>(null)
  const [alpacaControlAction, setAlpacaControlAction] = useState<string | null>(null)
  const [alpacaControlMessage, setAlpacaControlMessage] = useState<string | null>(null)
  const [universeStatus, setUniverseStatus] = useState<MarketUniverseStatus | null>(null)
  const [universeResults, setUniverseResults] = useState<MarketUniverseSearchResult | null>(null)
  const [universeLoading, setUniverseLoading] = useState(false)
  const [universeError, setUniverseError] = useState<string | null>(null)
  const [catalogRefreshing, setCatalogRefreshing] = useState(false)
  const [paperExecution, setPaperExecution] = useState<PaperExecutionStatus | null>(null)
  const [productionResearch, setProductionResearch] = useState<ProductionResearchStatus | null>(null)

  const [pendingPaperExecutionAction, setPendingPaperExecutionAction] = useState<
    'deactivate' | null
  >(null)
  const [paperExecutionActionInFlight, setPaperExecutionActionInFlight] = useState<
    'deactivate' | null
  >(null)
  const paperExecutionActionInFlightRef = useRef(false)

  const [pendingCycleAction, setPendingCycleAction] = useState<'start' | 'pause' | 'stop' | null>(null)
  const [cycleActionInFlight, setCycleActionInFlight] = useState<'start' | 'pause' | 'stop' | null>(null)
  const [pendingAuthorizationAction, setPendingAuthorizationAction] = useState<'grant' | 'revoke' | null>(null)
  const [authorizationActionInFlight, setAuthorizationActionInFlight] = useState<
    'grant' | 'revoke' | null
  >(null)
  const [pendingPaperReset, setPendingPaperReset] = useState(false)
  const [paperResetInFlight, setPaperResetInFlight] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [updater, setUpdater] = useState<UpdaterSnapshot | null>(null)
  const liveRuntime = typeof adapter.controlPaperCycle === 'function'
  const liveAuthorization = typeof adapter.controlPaperAuthorization === 'function'

  useEffect(() => {
    const api = desktopApi()
    void api?.getUpdaterSnapshot?.().then(setUpdater)

    return api?.subscribeToUpdaterState?.(setUpdater)
  }, [])

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

  const controlAlpaca = useCallback((action: string): void => {
    const request = action === 'refresh_status'
      ? desktopApi()?.getAlpacaMarketDataStatus?.()
      : desktopApi()?.controlAlpacaMarketData?.(action)

    if (!request) {
      setAlpacaControlMessage('Alpaca market-data controls are unavailable in this application context.')

      return
    }

    setAlpacaControlAction(action)
    setAlpacaControlMessage(null)
    setProviderError(null)
    void request
      .then(response => {
        if (!response.ok) {
          throw new Error(response.message)
        }

        setAlpacaMarketData(response.result)
        setAlpacaControlMessage(
          action === 'refresh_assets'
            ? response.result.asset_catalog.last_error
              ? `Read-only Alpaca asset refresh failed safely: ${response.result.asset_catalog.last_error}. ${response.result.asset_catalog.stale ? 'The cached catalog is stale or unavailable.' : 'The existing fresh catalog remains available.'}`
              : `Read-only Alpaca asset refresh completed: ${response.result.asset_catalog.accepted_count} accepted, ${response.result.asset_catalog.excluded_count} excluded.`
            : `Alpaca market-data status refreshed: ${response.result.provider_state}.`
        )
      })
      .catch(reason => {
        const message = reason instanceof Error ? reason.message : String(reason)
        setProviderError(message)
        setAlpacaControlMessage(`Alpaca market-data action failed safely: ${message}`)
      })
      .finally(() => setAlpacaControlAction(null))
  }, [])

  const searchUniverse = useCallback((query: string, universe: string): void => {
    const api = desktopApi()?.searchMarketUniverse

    if (!api) {
      return
    }

    setUniverseLoading(true)
    setUniverseError(null)
    void api({ query, universe, limit: 50, offset: 0 })
      .then(response => {
        if (!response.ok) {
          throw new Error(response.message)
        }

        setUniverseResults(response.result)
      })
      .catch(reason => setUniverseError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setUniverseLoading(false))
  }, [])

  const refreshCatalog = useCallback((): void => {
    const api = desktopApi()

    if (!api?.refreshAssetCatalog) {
      return
    }

    setCatalogRefreshing(true)
    setUniverseError(null)
    void api.refreshAssetCatalog()
      .then(response => {
        if (!response.ok) {
          throw new Error(response.message)
        }

        return api.getMarketUniverseStatus?.()
      })
      .then(response => {
        if (response?.ok) {
          setUniverseStatus(response.result)
          searchUniverse('', 'master')
        } else if (response && !response.ok) {
          throw new Error(response.message)
        }
      })
      .catch(reason => setUniverseError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setCatalogRefreshing(false))
  }, [searchUniverse])

  useEffect(() => {
    const api = desktopApi()?.paperExecution

    if (!api) {
      return
    }

    let cancelled = false

    const refresh = (): void => {
      void api('status').then(response => {
        if (!cancelled && response.ok) {
          setPaperExecution(response.result)
        }
      })
    }

    refresh()
    const timer = window.setInterval(refresh, 5_000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [reloadGeneration])

  useEffect(() => {
    const api = desktopApi()?.productionResearch

    if (!api) {
      return
    }

    let cancelled = false

    const refresh = (): void => {
      void api('status').then(response => {
        if (!cancelled && response.ok) {
          setProductionResearch(response.result)
        }
      })
    }

    refresh()
    const timer = window.setInterval(refresh, 5_000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [reloadGeneration])

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
    controlAlpaca('refresh_status')
  }, [controlAlpaca])

  useEffect(() => {
    const api = desktopApi()?.getMarketUniverseStatus

    if (!api) {
      return
    }

    void api().then(response => {
      if (response.ok) {
        setUniverseStatus(response.result)
      } else {
        setUniverseError(response.message)
      }
    })
    searchUniverse('', 'master')
  }, [searchUniverse])

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

  if (error && !snapshot) {
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
                  cycleActionInFlight !== null ||
                  (action === 'start'
                    ? snapshot.automationState === 'running' ||
                      snapshot.paperAuthorization?.status !== 'active'
                    : action === 'pause'
                      ? snapshot.automationState !== 'running'
                      : snapshot.automationState === 'stopped')
                }
                key={action}
                onClick={async () => {
                  if (!adapter.controlPaperCycle || cycleActionInFlight !== null) {
                    return
                  }

                  setCycleActionInFlight(action)
                  setControlError(null)

                  try {
                    setSnapshot(await adapter.controlPaperCycle(action))
                  } catch (reason) {
                    setControlError(
                      reason instanceof Error ? reason.message : String(reason)
                    )
                  } finally {
                    setCycleActionInFlight(null)
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
                {cycleActionInFlight === action
                  ? `${action === 'start' ? 'Starting' : action === 'pause' ? 'Pausing' : 'Stopping'}…`
                  : action === 'start' && snapshot.automationState === 'running'
                    ? '● Running'
                    : action === 'start' && snapshot.automationState === 'paused'
                      ? 'Resume'
                      : `${action[0]?.toUpperCase()}${action.slice(1)}`}
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
              <RuntimeVisibilityCard onOpenAudit={() => setSection('audit')} snapshot={snapshot} />
              <ProviderPanel
                alpacaControlAction={alpacaControlAction}
                alpacaControlMessage={alpacaControlMessage}
                alpacaMarketData={alpacaMarketData}
                error={providerError}
                loading={providerLoading}
                onAlpacaControl={controlAlpaca}
                onRefresh={refreshProviders}
                snapshot={providerSnapshot}
              />
              <AutonomousPaperPanel
                onAction={setPendingPaperExecutionAction}
                status={paperExecution}
              />
              <ProductionResearchPanel status={productionResearch} />
              <MarketUniversePanel
                error={universeError}
                loading={universeLoading}
                onRefresh={refreshCatalog}
                onSearch={searchUniverse}
                refreshing={catalogRefreshing}
                results={universeResults}
                status={universeStatus}
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
        {section === 'news' ? <GovernedNewsPanel /> : null}
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
        <span>Alpaca paper submission starts governed and enabled · live execution permanently disabled · account identity masked</span>
        <span>
          Adapter: governed local paper runtime · provider reads isolated in backend · {RELEASE_STAGE}
          {desktopApi()?.buildInfo
            ? ` · v${desktopApi()?.buildInfo?.version} · BUILD ${desktopApi()?.buildInfo?.build}`
            : ''}
        </span>
      </footer>
      <ConfirmDialog
        confirmLabel="Disable paper execution"
        description={
          pendingPaperExecutionAction
            ? 'Disable governed paper execution and restore the paper submission kill switch. Live execution remains unavailable.'
            : undefined
        }
        destructive
        onClose={() => setPendingPaperExecutionAction(null)}
        onConfirm={async () => {
          const action = pendingPaperExecutionAction
          const api = desktopApi()?.paperExecution

          if (
            action &&
            api &&
            paperExecutionActionInFlight === null &&
            !paperExecutionActionInFlightRef.current
          ) {
            paperExecutionActionInFlightRef.current = true
            setPaperExecutionActionInFlight(action)
            setControlError(null)

            try {
              const response = await api(action)

              if (response.ok) {
                setPaperExecution(response.result)
              } else {
                setControlError(response.message)
              }
            } catch (reason) {
              setControlError(reason instanceof Error ? reason.message : String(reason))
            } finally {
              paperExecutionActionInFlightRef.current = false
              setPaperExecutionActionInFlight(null)
            }
          }

          setPendingPaperExecutionAction(null)
        }}
        open={Boolean(pendingPaperExecutionAction)}
        title="Disable governed paper execution"
      />
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
          if (
            pendingAuthorizationAction &&
            adapter.controlPaperAuthorization &&
            authorizationActionInFlight === null
          ) {
            setAuthorizationActionInFlight(pendingAuthorizationAction)
            setControlError(null)

            try {
              setSnapshot(await adapter.controlPaperAuthorization(pendingAuthorizationAction))
            } catch (reason) {
              setControlError(reason instanceof Error ? reason.message : String(reason))
            } finally {
              setAuthorizationActionInFlight(null)
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
          if (adapter.resetPaperRuntime && !paperResetInFlight) {
            setPaperResetInFlight(true)
            setControlError(null)

            try {
              setSnapshot(await adapter.resetPaperRuntime())
              setSection('portfolio')
            } catch (reason) {
              setControlError(reason instanceof Error ? reason.message : String(reason))
            } finally {
              setPaperResetInFlight(false)
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
              {updater?.internalTest ? (
                <p className="mb-3 border border-amber-500/40 bg-amber-500/10 p-2 text-xs font-semibold">
                  INTERNAL TEST UPDATE
                </p>
              ) : null}
              <dl className="grid grid-cols-2 gap-2 text-xs">
                <div><dt className="text-(--ui-text-tertiary)">Installed</dt><dd className="font-mono">{updater?.currentVersion ?? desktopApi()?.buildInfo?.version}</dd></div>
                <div><dt className="text-(--ui-text-tertiary)">Available</dt><dd className="font-mono">{updater?.availableVersion ?? '—'}</dd></div>
                <div><dt className="text-(--ui-text-tertiary)">Status</dt><dd className="font-mono">{updater?.status ?? 'loading'}</dd></div>
              </dl>
              {updater?.progress ? (
                <div className="mt-3">
                  <progress aria-label="Update download progress" className="w-full" max={100} value={updater.progress.percent} />
                  <p className="font-mono text-[0.625rem] text-(--ui-text-tertiary)">
                    {updater.progress.percent.toFixed(1)}% · {updater.progress.transferred}/{updater.progress.total} bytes · {updater.progress.bytesPerSecond} B/s
                  </p>
                </div>
              ) : null}
              {updater?.releaseNotes ? <p className="mt-3 whitespace-pre-wrap text-xs">{updater.releaseNotes}</p> : null}
              <div className="mt-4 flex flex-wrap gap-2">
                {['idle', 'up-to-date', 'failed', 'disabled'].includes(updater?.status ?? 'idle') ? (
                  <Button disabled={updater?.status === 'checking' || updater?.status === 'disabled' || updater?.status === 'failed'} onClick={() => void desktopApi()?.checkForUpdates?.().then(setUpdater)} size="sm" variant="outline">Check for Updates</Button>
                ) : null}
                {updater?.status === 'update-available' ? (
                  <Button onClick={() => void desktopApi()?.approveUpdateDownload?.().then(setUpdater)} size="sm">Download Update</Button>
                ) : null}
                {['update-available', 'downloaded'].includes(updater?.status ?? '') ? (
                  <Button onClick={() => void desktopApi()?.deferUpdate?.().then(setUpdater)} size="sm" variant="outline">Later</Button>
                ) : null}
                {['downloaded', 'deferred'].includes(updater?.status ?? '') ? (
                  <Button onClick={() => void desktopApi()?.restartAndInstallUpdate?.().then(setUpdater)} size="sm">Restart and Install</Button>
                ) : null}
              </div>
              {updater ? <p aria-live="polite" className="mt-3 text-xs text-(--ui-text-tertiary)">{updater.message}</p> : null}
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}
