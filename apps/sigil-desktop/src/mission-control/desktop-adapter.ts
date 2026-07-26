import type { SigilOperatorAdapter, SigilSnapshot, SimulatedOperatorAction } from './types'

type SigilRuntimeSnapshot = {
  revision: number
  connection: {
    status: string
    last_refresh_at: string
  }
  balances: {
    cash: string
    portfolio_value: string
  }
  automation: {
    state: string
  }
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
    status: string
    timestamp: string
  }>
  reconciliation: Array<{
    order_id: string
    status: string
    required: boolean
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
}

function runtimeDesktopApi(): RuntimeDesktopApi | undefined {
  return (window as Window & { sigilDesktop?: RuntimeDesktopApi }).sigilDesktop
}

function currency(value: string): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value))
}

function mapRuntime(runtime: SigilRuntimeSnapshot): SigilSnapshot {
  return {
    dataState: runtime.connection.status === 'connected' ? 'ready' : 'stale',
    lastUpdated: runtime.connection.last_refresh_at,
    environment: 'paper',
    simulation: true,
    brokerConnection: runtime.connection.status === 'connected' ? 'connected' : 'disconnected',
    maskedAccountId: 'PAPER LOCAL',
    systemHealth: runtime.connection.status === 'connected' ? 'Runtime connected' : 'Runtime degraded',
    cash: currency(runtime.balances.cash),
    portfolioValue: currency(runtime.balances.portfolio_value),
    activeStrategies: runtime.automation.state === 'running' ? 1 : 0,
    pendingApprovals: runtime.proposals.filter(proposal => proposal.status === 'pending').length,
    killSwitch: 'armed',
    certificationStatus: 'Paper runtime',
    maximumLaunchNotional: '$25.00',
    firstLaunchLimit: '$25.00',
    launchState: 'suspended',
    stages: [
      { id: 'backend', label: 'Backend bridge', detail: runtime.connection.status, state: 'complete' },
      { id: 'state', label: 'Runtime state', detail: `Revision ${runtime.revision}`, state: 'complete' },
      { id: 'analysis', label: 'Hermes analysis', detail: 'Proposal only', state: 'ready' },
      { id: 'proposal', label: 'Proposal generation', detail: runtime.automation.state, state: 'pending' },
      { id: 'approval', label: 'Approval', detail: 'Operator unavailable', state: 'blocked' },
      { id: 'execution', label: 'Paper execution', detail: 'Disabled', state: 'blocked' },
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
        symbol: execution.symbol,
        brokerStatus: execution.status,
        state: 'simulated',
        duplicatePrevention: reconciliation?.status ?? 'local paper identity retained',
        reconciliationRequired: reconciliation?.required ?? false,
        timestamp: execution.timestamp
      }
    }),
    auditEvents: runtime.audit.map(event => ({
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

  async applySimulatedAction(_action: SimulatedOperatorAction): Promise<SigilSnapshot> {
    throw new Error('Approvals and execution actions are disabled in the live paper runtime.')
  }
}

export const desktopSigilOperatorAdapter = new DesktopSigilOperatorAdapter()
