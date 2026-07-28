import type {
  AuditEvent,
  SigilDataState,
  SigilOperatorAdapter,
  SigilSnapshot,
  SimulatedOperatorAction
} from './types'

const BASE_TIME = '2026-07-25T14:32:18Z'

export const SIGIL_FIRST_LAUNCH_LIMIT = '5% buying power / 10% position'

export const INITIAL_SIGIL_SNAPSHOT: SigilSnapshot = {
  dataState: 'stale',
  lastUpdated: BASE_TIME,
  environment: 'paper',
  simulation: true,
  brokerConnection: 'disconnected',
  maskedAccountId: '•••• 23F4',
  systemHealth: 'Governance healthy',
  cash: '$10,000.00',
  portfolioValue: '$10,842.16',
  buyingPower: '$10,000.00',
  totalAccountValue: '$10,842.16',
  realizedPnl: '$18.42',
  unrealizedPnl: '$42.16',
  positions: [
    {
      symbol: 'MSFT',
      quantity: '2',
      averageCost: '$396.00',
      marketValue: '$834.16',
      unrealizedPnl: '$42.16',
      realizedPnl: '$18.42',
      allocation: '7.69%',
      auditReferences: ['AUD-20260725-108', 'REC-20260725-018']
    }
  ],
  paperAuthorization: {
    status: 'active',
    authorizationId: 'PAPER-AUTH-DEMO',
    authorizationMonth: '2026-07',
    automaticMonthlyPolicy: true,
    authorizedAt: '2026-07-25T14:00:00Z',
    expiresAt: '2026-08-24T14:00:00Z',
    revokedAt: null,
    scope: ['automatic-paper-approval', 'simulated-paper-buy', 'simulated-paper-sell']
  },
  automationState: 'paused',
  automationCycleCount: 3,
  automationLastCycleAt: '2026-07-25T14:32:13Z',
  automationNextCycleAt: null,
  runtimeVisibility: {
    operationalState: 'paused',
    health: 'healthy',
    rawHealth: 'healthy',
    paperExecutionAvailable: true,
    brokerSubmissionAvailable: false,
    executionAuthorized: false,
    connectionState: 'connected',
    automationMode: 'monthly-authorized-paper-execution',
    pauseCause: 'manual',
    nextAction: 'Explicitly resume local paper automation',
    blockingReasons: [
      {
        code: 'automation_paused',
        severity: 'warning',
        summary: 'Automation is paused by the owner',
        requiresManualResume: true
      },
      {
        code: 'broker_submission_unavailable',
        severity: 'info',
        summary: 'Real broker submission is unavailable; local paper simulation remains separate',
        requiresManualResume: false
      }
    ],
    counts: {
      cycles: 3,
      proposals: 3,
      executions: 3,
      reconciliation: 3,
      auditEvents: 3
    }
  },
  activeStrategies: 4,
  pendingApprovals: 2,
  killSwitch: 'armed',
  certificationStatus: 'Paper certified',
  maximumLaunchNotional: SIGIL_FIRST_LAUNCH_LIMIT,
  firstLaunchLimit: SIGIL_FIRST_LAUNCH_LIMIT,
  launchState: 'suspended',
  stages: [
    { id: 'strategy', label: 'Strategy', detail: 'Qualified', state: 'complete' },
    { id: 'risk', label: 'Risk', detail: 'Passed', state: 'complete' },
    { id: 'capital', label: 'Paper sizing', detail: 'Dynamic bounded policy', state: 'complete' },
    { id: 'eligibility', label: 'Eligibility', detail: 'Eligible', state: 'complete' },
    { id: 'certification', label: 'Certification', detail: 'Paper only', state: 'complete' },
    { id: 'launch', label: 'Launch control', detail: 'Suspended', state: 'pending' },
    { id: 'admission', label: 'Order admission', detail: 'Awaiting', state: 'pending' },
    { id: 'handoff', label: 'Execution handoff', detail: 'Not started', state: 'blocked' },
    { id: 'submission', label: 'Broker submission', detail: 'Unavailable', state: 'blocked' }
  ],
  proposals: [
    {
      id: 'PRP-20260725-0042',
      symbol: 'MSFT',
      side: 'BUY',
      quantity: 0.05,
      estimatedNotional: '$22.64',
      strategy: 'Quality momentum v2',
      status: 'pending',
      evidenceReferences: ['EVD-9F3A7B1C', 'RISK-42A'],
      riskResults: ['Position limit passed', 'Liquidity check passed', 'Concentration 2.1%']
    },
    {
      id: 'PRP-20260725-0041',
      symbol: 'NVDA',
      side: 'SELL',
      quantity: 0.1,
      estimatedNotional: '$17.30',
      strategy: 'Volatility reduction v1',
      status: 'pending',
      evidenceReferences: ['EVD-7C2D9A6E', 'RISK-419'],
      riskResults: ['Sellable quantity passed', 'Wash-sale review clear']
    },
    {
      id: 'PRP-20260725-0040',
      symbol: 'AAPL',
      side: 'BUY',
      quantity: 0.08,
      estimatedNotional: '$17.12',
      strategy: 'Quality momentum v2',
      status: 'rejected',
      evidenceReferences: ['EVD-31F09CC2'],
      riskResults: ['Rejected: stale market evidence']
    }
  ],
  receipts: [
    {
      id: 'RCT-20260725-018',
      orderId: 'ORD-20260725-018',
      proposalId: 'PRP-20260725-0042',
      symbol: 'MSFT',
      side: 'BUY',
      quantity: '0.12',
      price: '417.50',
      notional: '50.10',
      brokerStatus: 'Simulated acknowledgement',
      state: 'simulated',
      duplicatePrevention: 'Envelope consumed once',
      reconciliationRequired: false,
      reconciliationReference: 'REC-20260725-018',
      timestamp: '2026-07-25T14:18:03Z'
    },
    {
      id: 'RCT-20260725-017',
      orderId: 'ORD-20260725-017',
      proposalId: 'PRP-20260725-0040',
      symbol: 'AAPL',
      side: 'BUY',
      quantity: '0.08',
      price: '214.00',
      notional: '17.12',
      brokerStatus: 'Rejected before transport',
      state: 'rejected',
      duplicatePrevention: 'Client order ID retained',
      reconciliationRequired: false,
      reconciliationReference: 'REC-20260725-017',
      timestamp: '2026-07-25T13:41:22Z'
    },
    {
      id: 'RCT-20260725-016',
      orderId: 'ORD-20260725-016',
      proposalId: 'PRP-20260725-0039',
      symbol: 'NVDA',
      side: 'SELL',
      quantity: '0.10',
      price: '174.80',
      notional: '17.48',
      brokerStatus: 'Outcome uncertain — no retry',
      state: 'outcome-uncertain',
      duplicatePrevention: 'Retry blocked pending reconciliation',
      reconciliationRequired: true,
      reconciliationReference: 'REC-20260725-016',
      timestamp: '2026-07-25T12:56:09Z'
    }
  ],
  auditEvents: [
    {
      id: 'AUD-20260725-108',
      timestamp: '2026-07-25T14:18:03Z',
      orderId: 'ORD-20260725-018',
      proposalId: 'PRP-20260725-0042',
      status: 'simulated',
      evidenceReference: 'EVD-9F3A7B1C',
      summary: 'Immutable simulated receipt recorded',
      details: {
        event_type: 'execution_receipt_recorded',
        paper_only: true,
        receipt_digest: 'sha256:8d92…bf31',
        broker_submission_attempted: false
      }
    },
    {
      id: 'AUD-20260725-107',
      timestamp: '2026-07-25T13:41:22Z',
      orderId: 'ORD-20260725-017',
      proposalId: 'PRP-20260725-0040',
      status: 'rejected',
      evidenceReference: 'EVD-31F09CC2',
      summary: 'Submission rejected before transport',
      details: {
        event_type: 'broker_submission_rejected',
        blocker: 'market_evidence_stale',
        broker_submission_attempted: false
      }
    },
    {
      id: 'AUD-20260725-106',
      timestamp: '2026-07-25T12:56:09Z',
      orderId: 'ORD-20260725-016',
      proposalId: 'PRP-20260725-0039',
      status: 'outcome-uncertain',
      evidenceReference: 'EVD-1158A0D2',
      summary: 'Retry blocked; reconciliation required',
      details: {
        event_type: 'submission_outcome_uncertain',
        duplicate_prevention: 'active',
        automatic_retry_allowed: false
      }
    }
  ]
}

function actionEvent(action: SimulatedOperatorAction): AuditEvent {
  const proposalId = 'proposalId' in action ? action.proposalId : '—'
  const verb = action.type.replaceAll('-', ' ')

  return {
    id: `AUD-SIM-${action.type}`,
    timestamp: BASE_TIME,
    orderId: '—',
    proposalId,
    status: 'simulated',
    evidenceReference: 'SIMULATED-UI',
    summary: `Operator ${verb} recorded in local simulation`,
    details: {
      event_type: action.type,
      simulation_only: true,
      broker_submission_attempted: false
    }
  }
}

function applyAction(snapshot: SigilSnapshot, action: SimulatedOperatorAction): SigilSnapshot {
  const proposals = snapshot.proposals.map(proposal => {
    if (!('proposalId' in action) || proposal.id !== action.proposalId) {
      return proposal
    }

    return {
      ...proposal,
      status: action.type === 'approve-proposal' ? ('approved' as const) : ('rejected' as const)
    }
  })

  return {
    ...snapshot,
    proposals,
    pendingApprovals: proposals.filter(proposal => proposal.status === 'pending').length,
    launchState:
      action.type === 'arm-launch' ? 'armed' : action.type === 'suspend-launch' ? 'suspended' : snapshot.launchState,
    killSwitch: action.type === 'engage-kill-switch' ? 'engaged' : snapshot.killSwitch,
    auditEvents: [actionEvent(action), ...snapshot.auditEvents]
  }
}

export class MockSigilOperatorAdapter implements SigilOperatorAdapter {
  private snapshot: SigilSnapshot

  constructor(dataState: SigilDataState = INITIAL_SIGIL_SNAPSHOT.dataState) {
    this.snapshot = {
      ...INITIAL_SIGIL_SNAPSHOT,
      dataState,
      proposals: dataState === 'empty' ? [] : INITIAL_SIGIL_SNAPSHOT.proposals,
      receipts: dataState === 'empty' ? [] : INITIAL_SIGIL_SNAPSHOT.receipts,
      auditEvents: dataState === 'empty' ? [] : INITIAL_SIGIL_SNAPSHOT.auditEvents
    }
  }

  async readSnapshot(): Promise<SigilSnapshot> {
    if (this.snapshot.dataState === 'error') {
      throw new Error('The local Sigil snapshot could not be verified.')
    }

    return structuredClone(this.snapshot)
  }

  async applySimulatedAction(action: SimulatedOperatorAction): Promise<SigilSnapshot> {
    this.snapshot = applyAction(this.snapshot, action)

    return structuredClone(this.snapshot)
  }

  async controlPaperCycle(action: 'start' | 'pause' | 'stop'): Promise<SigilSnapshot> {
    const state = action === 'start' ? 'running' : action === 'pause' ? 'paused' : 'stopped'
    this.snapshot = {
      ...this.snapshot,
      automationState: state,
      automationNextCycleAt: state === 'running' ? '2099-07-25T14:32:23Z' : null,
      runtimeVisibility: this.snapshot.runtimeVisibility
        ? {
            ...this.snapshot.runtimeVisibility,
            operationalState: state,
            pauseCause: state === 'paused' ? 'manual' : null,
            blockingReasons:
              state === 'running'
                ? this.snapshot.runtimeVisibility.blockingReasons.filter(
                    reason => reason.code !== 'automation_paused' && reason.code !== 'automation_stopped'
                  )
                : [
                    {
                      code: state === 'paused' ? 'automation_paused' : 'automation_stopped',
                      severity: 'warning',
                      summary: state === 'paused' ? 'Automation is paused by the owner' : 'Automation is stopped',
                      requiresManualResume: true
                    },
                    ...this.snapshot.runtimeVisibility.blockingReasons.filter(
                      reason => reason.code !== 'automation_paused' && reason.code !== 'automation_stopped'
                    )
                  ]
          }
        : undefined
    }

    return structuredClone(this.snapshot)
  }

  async controlPaperAuthorization(action: 'grant' | 'revoke'): Promise<SigilSnapshot> {
    this.snapshot = {
      ...this.snapshot,
      paperAuthorization:
        action === 'grant'
          ? INITIAL_SIGIL_SNAPSHOT.paperAuthorization
          : {
              status: 'revoked',
              authorizationId: 'PAPER-AUTH-DEMO',
              authorizationMonth: '2026-07',
              automaticMonthlyPolicy: true,
              authorizedAt: '2026-07-25T14:00:00Z',
              expiresAt: '2026-08-24T14:00:00Z',
              revokedAt: BASE_TIME,
              scope: ['automatic-paper-approval', 'simulated-paper-buy', 'simulated-paper-sell']
            },
      automationState: action === 'revoke' ? 'paused' : this.snapshot.automationState
    }

    return structuredClone(this.snapshot)
  }

  async resetPaperRuntime(): Promise<SigilSnapshot> {
    this.snapshot = {
      ...this.snapshot,
      cash: '$10,000.00',
      buyingPower: '$10,000.00',
      portfolioValue: '$0.00',
      totalAccountValue: '$10,000.00',
      realizedPnl: '$0.00',
      unrealizedPnl: '$0.00',
      positions: [],
      proposals: [],
      receipts: [],
      pendingApprovals: 0,
      automationState: 'stopped',
      automationCycleCount: 0,
      paperAuthorization: INITIAL_SIGIL_SNAPSHOT.paperAuthorization
    }

    return structuredClone(this.snapshot)
  }
}

export const sigilOperatorAdapter: SigilOperatorAdapter = new MockSigilOperatorAdapter()
