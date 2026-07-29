import type { RuntimeVisibility, SigilOperatorAdapter, SigilSnapshot, SimulatedOperatorAction } from './types'

type RuntimeHealth =
  | 'healthy'
  | 'degraded'
  | 'recovery_required'
  | 'corrupt'
  | 'locked'

type SigilRuntimeSnapshot = {
  revision: number
  runtime_health?: RuntimeHealth
  connection: {
    status: string
    last_refresh_at: string
  }
  balances: {
    cash: string
    portfolio_value: string
    buying_power?: string
    total_account_value?: string
    realized_pnl?: string
    unrealized_pnl?: string
    valuation_status?: 'fresh' | 'stale' | 'unavailable'
    unrealized_pnl_status?: 'fresh' | 'stale' | 'unavailable'
  }
  positions: Array<{
    symbol: string
    quantity: string
    average_cost?: string
    market_value: string
    unrealized_pnl?: string
    realized_pnl?: string
    mark_status?: 'fresh' | 'stale' | 'unavailable'
    mark_price?: string | null
    mark_timestamp?: string | null
    mark_source?: string | null
    mark_evidence_identity?: string | null
    unrealized_pnl_status?: 'fresh' | 'stale' | 'unavailable'
  }>
  automation: {
    state: string
    cycle_count?: number
    last_cycle_at?: string | null
    next_cycle_at?: string | null
  }
  runtime_visibility?: {
    operational_state: 'blocked' | 'paused' | 'running' | 'stopped'
    health: 'blocked' | 'degraded' | 'healthy'
    raw_health: string
    paper_execution_available: boolean
    broker_submission_available: boolean
    execution_authorized: boolean
    connection_state: string
    automation_mode: string
    pause_cause: 'manual' | 'safety' | null
    next_action: string
    blocking_reasons: Array<{
      code: string
      severity: 'critical' | 'info' | 'warning'
      summary: string
      requires_manual_resume: boolean
    }>
    counts: {
      cycles: number
      proposals: number
      executions: number
      reconciliation: number
      audit_events: number
    }
  }
  paper_authorization: {
    status: 'active' | 'expired' | 'required' | 'revoked'
    authorization_id: string | null
    authorization_month?: string
    automatic_monthly_policy?: boolean
    authorized_at: string | null
    expires_at: string | null
    revoked_at: string | null
    scope: string[]
  }
  orders: Record<string, {
    proposal_id?: string
  }>
  proposals: Array<{
    id: string
    symbol: string
    side: 'BUY' | 'SELL'
    quantity: number
    estimated_notional: string
    strategy: string
    status: string
    evidence_references: string[]
    risk_results: string[]
  }>
  executions: Array<{
    id: string
    order_id: string
    symbol: string
    side: 'BUY' | 'SELL'
    quantity: string
    price: string
    status: string
    timestamp: string
  }>
  reconciliation: Array<{
    order_id: string
    status: string
    required: boolean
    evidence_reference?: string
  }>
  audit: Array<{
    id: string
    timestamp: string
    order_id: string
    proposal_id: string
    status: string
    evidence_reference: string
    summary: string
    details: Readonly<Record<string, unknown>>
  }>
}

type RuntimeBridgeResponse =
  | { ok: true; result: SigilRuntimeSnapshot }
  | { ok: false; error: string; message: string }

type RuntimeDesktopApi = {
  getRuntimeSnapshot?: () => Promise<RuntimeBridgeResponse>
  controlPaperCycle?: (
    action: 'start' | 'pause' | 'stop'
  ) => Promise<RuntimeBridgeResponse>
  controlPaperAuthorization?: (
    action: 'grant' | 'revoke'
  ) => Promise<RuntimeBridgeResponse>
  resetPaperRuntime?: () => Promise<RuntimeBridgeResponse>
}

function runtimeDesktopApi(): RuntimeDesktopApi | undefined {
  return (window as Window & { sigilDesktop?: RuntimeDesktopApi }).sigilDesktop
}

function currency(value: string): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value))
}

export function runtimeHealthLabel(
  runtime: {
    connection: { status: string }
    runtime_health?: RuntimeHealth
  }
): string {
  if (runtime.connection.status !== 'connected') {
    return 'Runtime disconnected'
  }

  switch (runtime.runtime_health) {
    case 'healthy':
      return 'Runtime healthy'

    case 'degraded':
      return 'Runtime degraded'

    case 'recovery_required':
      return 'Recovery required'

    case 'corrupt':
      return 'Runtime corruption detected'

    case 'locked':
      return 'Runtime unavailable'

    default:
      return 'Runtime health unknown'
  }
}

function mapVisibility(runtime: SigilRuntimeSnapshot): RuntimeVisibility | undefined {
  const visibility = runtime.runtime_visibility

  if (!visibility) {
    return undefined
  }

  return {
    operationalState: visibility.operational_state,
    health: visibility.health,
    rawHealth: visibility.raw_health,
    paperExecutionAvailable: visibility.paper_execution_available,
    brokerSubmissionAvailable: visibility.broker_submission_available,
    executionAuthorized: visibility.execution_authorized,
    connectionState: visibility.connection_state,
    automationMode: visibility.automation_mode,
    pauseCause: visibility.pause_cause,
    nextAction: visibility.next_action,
    blockingReasons: visibility.blocking_reasons.map(reason => ({
      code: reason.code,
      severity: reason.severity,
      summary: reason.summary,
      requiresManualResume: reason.requires_manual_resume
    })),
    counts: {
      cycles: visibility.counts.cycles,
      proposals: visibility.counts.proposals,
      executions: visibility.counts.executions,
      reconciliation: visibility.counts.reconciliation,
      auditEvents: visibility.counts.audit_events
    }
  }
}

export function mapRuntime(runtime: SigilRuntimeSnapshot): SigilSnapshot {
  const totalAccountValue = Number(
    runtime.balances.total_account_value ?? runtime.balances.portfolio_value
  )

  const auditReferencesBySymbol = new Map<string, string[]>()

  for (const event of runtime.audit) {
    const symbol = typeof event.details.symbol === 'string' ? event.details.symbol : null

    if (symbol) {
      const references = auditReferencesBySymbol.get(symbol) ?? []
      references.push(event.evidence_reference)
      auditReferencesBySymbol.set(symbol, references)
    }
  }

  return {
    dataState: runtime.connection.status === 'connected' ? 'ready' : 'stale',
    lastUpdated: runtime.connection.last_refresh_at,
    environment: 'paper',
    simulation: true,
    brokerConnection: runtime.connection.status === 'connected' ? 'connected' : 'disconnected',
    maskedAccountId: 'PAPER LOCAL',
    systemHealth: runtimeHealthLabel(runtime),
    cash: currency(runtime.balances.cash),
    portfolioValue: currency(runtime.balances.portfolio_value),
    buyingPower: currency(runtime.balances.buying_power ?? runtime.balances.cash),
    totalAccountValue: currency(
      runtime.balances.total_account_value ?? runtime.balances.portfolio_value
    ),
    realizedPnl: currency(runtime.balances.realized_pnl ?? '0'),
    unrealizedPnl:
      runtime.balances.unrealized_pnl_status === 'fresh'
        ? currency(runtime.balances.unrealized_pnl ?? '0')
        : 'Unavailable',
    valuationStatus: runtime.balances.valuation_status ?? 'unavailable',
    positions: runtime.positions.map(position => ({
      symbol: position.symbol,
      quantity: position.quantity,
      averageCost: currency(position.average_cost ?? '0'),
      marketValue: currency(position.market_value),
      unrealizedPnl:
        position.unrealized_pnl_status === 'fresh'
          ? currency(position.unrealized_pnl ?? '0')
          : position.unrealized_pnl_status === 'stale'
            ? 'Stale'
            : 'Unavailable',
      realizedPnl: currency(position.realized_pnl ?? '0'),
      valuationStatus: position.mark_status ?? 'unavailable',
      markPrice: position.mark_price ? currency(position.mark_price) : null,
      markTimestamp: position.mark_timestamp ?? null,
      markSource: position.mark_source ?? null,
      markEvidenceIdentity: position.mark_evidence_identity ?? null,
      allocation:
        totalAccountValue > 0
          ? `${((Number(position.market_value) / totalAccountValue) * 100).toFixed(1)}%`
          : '0.0%',
      auditReferences: [...new Set(auditReferencesBySymbol.get(position.symbol) ?? [])].slice(0, 3)
    })),
    paperAuthorization: {
      status: runtime.paper_authorization.status,
      authorizationId: runtime.paper_authorization.authorization_id,
      authorizationMonth: runtime.paper_authorization.authorization_month,
      automaticMonthlyPolicy:
        runtime.paper_authorization.automatic_monthly_policy,
      authorizedAt: runtime.paper_authorization.authorized_at,
      expiresAt: runtime.paper_authorization.expires_at,
      revokedAt: runtime.paper_authorization.revoked_at,
      scope: runtime.paper_authorization.scope
    },
    activeStrategies: runtime.automation.state === 'running' ? 1 : 0,
    automationState:
      runtime.automation.state === 'running' || runtime.automation.state === 'paused'
        ? runtime.automation.state
        : 'stopped',
    automationCycleCount: runtime.automation.cycle_count ?? 0,
    automationLastCycleAt: runtime.automation.last_cycle_at ?? null,
    automationNextCycleAt: runtime.automation.next_cycle_at ?? null,
    runtimeVisibility: mapVisibility(runtime),
    pendingApprovals: runtime.proposals.filter(proposal => proposal.status === 'pending').length,
    killSwitch: 'armed',
    certificationStatus: 'Paper runtime',
    maximumLaunchNotional: 'Dynamic paper allocation',
    firstLaunchLimit: '5% buying power / 10% position',
    launchState: 'suspended',
    stages: [
      { id: 'backend', label: 'Backend bridge', detail: runtime.connection.status, state: 'complete' },
      { id: 'state', label: 'Runtime state', detail: `Revision ${runtime.revision}`, state: 'complete' },
      { id: 'analysis', label: 'Hermes analysis', detail: 'Paper proposals', state: 'ready' },
      { id: 'proposal', label: 'Proposal generation', detail: runtime.automation.state, state: 'pending' },
      {
        id: 'approval',
        label: 'Paper auto-approval',
        detail: runtime.paper_authorization.status,
        state: runtime.paper_authorization.status === 'active' ? 'complete' : 'blocked'
      },
      {
        id: 'execution',
        label: 'Simulated execution',
        detail: runtime.paper_authorization.status === 'active' ? 'Authorized locally' : 'Authorization required',
        state: runtime.paper_authorization.status === 'active' ? 'simulated' : 'blocked'
      },
      { id: 'broker', label: 'Broker transport', detail: 'Unavailable', state: 'blocked' }
    ],
    proposals: runtime.proposals.map(proposal => ({
      id: proposal.id,
      symbol: proposal.symbol,
      side: proposal.side,
      quantity: proposal.quantity,
      estimatedNotional: currency(proposal.estimated_notional),
      strategy: proposal.strategy,
      status:
        proposal.status === 'approved' || proposal.status === 'rejected'
          ? proposal.status
          : 'pending',
      evidenceReferences: proposal.evidence_references,
      riskResults: proposal.risk_results
    })),
    receipts: runtime.executions.map(execution => {
      const reconciliation = runtime.reconciliation.find(item => item.order_id === execution.order_id)

      return {
        id: execution.id,
        orderId: execution.order_id,
        proposalId:
          runtime.proposals.find(proposal =>
            proposal.id.endsWith(execution.order_id.replace('PAPER-ORD-', ''))
          )?.id ?? '—',
        symbol: execution.symbol,
        side: execution.side,
        quantity: execution.quantity,
        price: currency(execution.price),
        notional: currency(String(Number(execution.quantity) * Number(execution.price))),
        brokerStatus: execution.status,
        state: 'simulated',
        duplicatePrevention: reconciliation?.status ?? 'local paper identity retained',
        reconciliationRequired: reconciliation?.required ?? false,
        timestamp: execution.timestamp,
        reconciliationReference:
          reconciliation?.evidence_reference ?? `PAPER-RUNTIME:${execution.order_id}`
      }
    }),
    auditEvents: [...runtime.audit]
      .sort((left, right) => right.timestamp.localeCompare(left.timestamp))
      .map(event => ({
        id: event.id,
        timestamp: event.timestamp,
        orderId: event.order_id,
        proposalId: event.proposal_id,
        status: event.status,
        evidenceReference: event.evidence_reference,
        summary: event.summary,
        details: event.details
      }))
  }
}

export class DesktopSigilOperatorAdapter implements SigilOperatorAdapter {
  async readSnapshot(): Promise<SigilSnapshot> {
    const response = await runtimeDesktopApi()?.getRuntimeSnapshot?.()

    if (!response?.ok) {
      throw new Error(response?.message ?? 'The governed local backend bridge is unavailable.')
    }

    return mapRuntime(response.result)
  }

  async controlPaperCycle(action: 'start' | 'pause' | 'stop'): Promise<SigilSnapshot> {
    const response = await runtimeDesktopApi()?.controlPaperCycle?.(action)

    if (!response?.ok) {
      throw new Error(response?.message ?? 'Paper automation control failed safely.')
    }

    return mapRuntime(response.result)
  }

  async controlPaperAuthorization(action: 'grant' | 'revoke'): Promise<SigilSnapshot> {
    const response = await runtimeDesktopApi()?.controlPaperAuthorization?.(action)

    if (!response?.ok) {
      throw new Error(response?.message ?? 'Paper authorization control failed safely.')
    }

    return mapRuntime(response.result)
  }

  async resetPaperRuntime(): Promise<SigilSnapshot> {
    const response = await runtimeDesktopApi()?.resetPaperRuntime?.()

    if (!response?.ok) {
      throw new Error(response?.message ?? 'Paper runtime reset failed safely.')
    }

    return mapRuntime(response.result)
  }

  async applySimulatedAction(_action: SimulatedOperatorAction): Promise<SigilSnapshot> {
    throw new Error('Approvals and execution actions are disabled in the live paper runtime.')
  }
}

export const desktopSigilOperatorAdapter = new DesktopSigilOperatorAdapter()
