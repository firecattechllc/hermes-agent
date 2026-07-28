export type SigilDataState = 'disconnected' | 'empty' | 'error' | 'loading' | 'ready' | 'stale'
export type SigilTone = 'danger' | 'info' | 'muted' | 'success' | 'warning'

export interface SigilMetric {
  label: string
  value: string
  detail: string
  tone?: SigilTone
}

export interface PipelineStage {
  id: string
  label: string
  detail: string
  state: 'blocked' | 'complete' | 'pending' | 'ready' | 'simulated'
}

export interface Proposal {
  id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  estimatedNotional: string
  strategy: string
  status: 'approved' | 'pending' | 'rejected'
  evidenceReferences: string[]
  riskResults: string[]
}

export interface ExecutionReceipt {
  id: string
  orderId: string
  proposalId: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: string
  price: string
  notional: string
  brokerStatus: string
  state: 'outcome-uncertain' | 'rejected' | 'simulated'
  duplicatePrevention: string
  reconciliationRequired: boolean
  timestamp: string
  reconciliationReference: string
}

export interface PaperPosition {
  symbol: string
  quantity: string
  averageCost: string
  marketValue: string
  unrealizedPnl: string
  realizedPnl: string
  allocation: string
  auditReferences: string[]
}

export interface PaperAuthorization {
  status: 'active' | 'expired' | 'required' | 'revoked'
  authorizationId: string | null
  authorizationMonth?: string
  automaticMonthlyPolicy?: boolean
  authorizedAt: string | null
  expiresAt: string | null
  revokedAt: string | null
  scope: string[]
}

export interface AuditEvent {
  id: string
  timestamp: string
  orderId: string
  proposalId: string
  status: string
  evidenceReference: string
  summary: string
  details: Readonly<Record<string, unknown>>
}

export interface RuntimeBlockingReason {
  code: string
  severity: 'critical' | 'info' | 'warning'
  summary: string
  requiresManualResume: boolean
}

export interface RuntimeVisibility {
  operationalState: 'blocked' | 'paused' | 'running' | 'stopped'
  health: 'blocked' | 'degraded' | 'healthy'
  rawHealth: string
  paperExecutionAvailable: boolean
  brokerSubmissionAvailable: boolean
  executionAuthorized: boolean
  connectionState: string
  automationMode: string
  pauseCause: 'manual' | 'safety' | null
  nextAction: string
  blockingReasons: RuntimeBlockingReason[]
  counts: {
    cycles: number
    proposals: number
    executions: number
    reconciliation: number
    auditEvents: number
  }
}

export interface SigilProviderSnapshot {
  checked_at: string
  broker_submission_available: false
  credentials_exposed: false
  alpaca: {
    status: 'connected' | 'degraded' | 'not_configured'
    message: string
    universe?: {
      scope: string
      total: number
      available: number
      unavailable: number
      catalog_source?: string
      catalog_freshness?: string
      iex_status?: string
      broader_us_status?: string
      criteria: string
      whole_market_coverage: false
      catalog_access?: string
      coverage_limitation?: string
      refresh_policy?: string
    }
    symbols: Array<{
      symbol: string
      name?: string
      sector?: string
      price: string
      observed_at: string
      daily_change_percent?: string
      screen_status?: string
      source: string
    }>
  }
  public: {
    status: 'connected' | 'degraded' | 'not_configured'
    message: string
    accounts: Array<{
      masked_account_id: string
      cash: string
      portfolio_value: string
      positions: Array<{ symbol: string; quantity: string }>
    }>
  }
}

export interface MarketUniverseStatus {
  schema_version: number
  policy_version: string
  snapshot_id: string
  generated_at: string
  source_record_count: number
  master_count: number
  broker_tradable_count: number
  actively_researched_count: number
  proposal_eligible_count: number
  conflicted_count: number
  excluded_count: number
  target_minimum: number
  target_maximum: number
  target_capacity_validated: boolean
  catalog_source: string
  catalog_scope: string
  capacity_certification: string
  coverage_limitation: string
  broker_submission_available: false
  execution_authorized: false
}

export interface MarketUniverseInstrument {
  instrument_id: string
  symbol: string
  name: string
  exchange: string
  asset_class: string
  lifecycle_status: string
  reconciliation_status: string
  monitoring_tier: string
  aliases: string[]
  sector: string | null
  broker_tradable: boolean
  actively_researched: boolean
  proposal_eligible: boolean
  exclusion_reasons: string[]
}

export interface MarketUniverseSearchResult {
  query: string
  universe: string
  total: number
  offset: number
  limit: number
  has_more: boolean
  results: MarketUniverseInstrument[]
  broker_submission_available: false
  execution_authorized: false
}

export interface AlpacaMarketDataStatus {
  configured: boolean
  authenticated: boolean
  provider_state: string
  asset_catalog: {
    refresh_state: string; source_count: number; accepted_count: number
    excluded_count: number; conflict_count: number; generated_at: string | null
    age_seconds: number | null; stale: boolean; last_error: string | null
  }
  delayed_sip: {
    classification: string; expected_delay_minutes: number; scanned_count: number
    universe_total: number; current_batch: number; total_batches: number; provider_state: string
  }
  live_iex: {
    classification: string; partial_market: true; connected: boolean
    active_symbol_count: number; maximum_symbol_count: number; subscribed_symbols: string[]
    last_message_at: string | null; stale: boolean; provider_state: string
  }
  safety: {
    broker_submission_available: false; execution_authorized: false
    live_trading_enabled: false; data_only_mode: true
  }
}

export interface SigilSnapshot {
  dataState: SigilDataState
  lastUpdated: string
  environment: 'paper' | 'live'
  simulation: boolean
  brokerConnection: 'connected' | 'disconnected'
  maskedAccountId: string
  systemHealth: string
  cash: string
  portfolioValue: string
  buyingPower?: string
  totalAccountValue?: string
  realizedPnl?: string
  unrealizedPnl?: string
  positions?: PaperPosition[]
  paperAuthorization?: PaperAuthorization
  activeStrategies: number
  automationState?: 'paused' | 'running' | 'stopped'
  automationCycleCount?: number
  automationLastCycleAt?: string | null
  automationNextCycleAt?: string | null
  runtimeVisibility?: RuntimeVisibility
  pendingApprovals: number
  killSwitch: 'armed' | 'engaged'
  certificationStatus: string
  maximumLaunchNotional: string
  firstLaunchLimit: string
  launchState: 'armed' | 'suspended'
  stages: PipelineStage[]
  proposals: Proposal[]
  receipts: ExecutionReceipt[]
  auditEvents: AuditEvent[]
}

export type SimulatedOperatorAction =
  | { type: 'approve-proposal'; proposalId: string }
  | { type: 'reject-proposal'; proposalId: string }
  | { type: 'arm-launch' }
  | { type: 'suspend-launch' }
  | { type: 'engage-kill-switch' }

/**
 * Deliberately excludes broker submission and capital-limit mutation.
 * Step 34 can observe governed state and simulate bounded operator decisions;
 * it cannot cross the Step 33 submission boundary.
 */
export interface SigilOperatorAdapter {
  readSnapshot(): Promise<SigilSnapshot>
  applySimulatedAction(action: SimulatedOperatorAction): Promise<SigilSnapshot>
  controlPaperCycle?: (action: 'start' | 'pause' | 'stop') => Promise<SigilSnapshot>
  controlPaperAuthorization?: (action: 'grant' | 'revoke') => Promise<SigilSnapshot>
  resetPaperRuntime?: () => Promise<SigilSnapshot>
}
