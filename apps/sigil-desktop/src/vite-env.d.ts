/// <reference types="vite/client" />

import type {
  AssetCatalogStatus,
  MarketUniverseQuoteResult,
  MarketUniverseSearchResult,
  MarketUniverseStatus,
  SigilProviderSnapshot
} from './mission-control/types'

declare global {
type SigilBackendStatus = {
  bridge_version: string
  status: string
  mode: string
  environment: string
  simulation: boolean
  execution_authorized: boolean
  broker_submission_available: boolean
  supported_commands: string[]
}

type SigilBackendProposalExplanation = {
  kind: 'proposal-explanation'
  summary: string
  explanation: string
  model_route: string
  source: 'local'
  confidence: number
  evidence_references: Array<{
    id: string
    label?: string
    source?: string
  }>
  generated_at: string
  execution_authorized: false
  broker_submission_available: false
}

type SigilBackendResponse<T> =
  | {
      ok: true
      result: T
    }
  | {
      ok: false
      error: string
      message: string
    }

type SigilRuntimeSnapshot = Record<string, unknown>

type SigilProposalExplanationPayload = {
  proposal_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  estimated_notional: number
  strategy: string
  evidence_references: Array<{
    id: string
    label?: string
    source?: string
  }>
}

interface SigilDesktopApi {
  productName: string
  persistenceNamespace: string
  brokerSubmissionAvailable: false
  buildInfo?: {
    version: string
    build: string
    commit: string
    buildTime: string
    channel: 'dev' | 'release'
    applicationMode: 'Live development' | 'Packaged release'
  }
  getUpdaterSnapshot?: () => Promise<Record<string, unknown>>
  checkForUpdates?: () => Promise<Record<string, unknown>>
  approveUpdateDownload?: () => Promise<Record<string, unknown>>
  deferUpdate?: () => Promise<Record<string, unknown>>
  restartAndInstallUpdate?: () => Promise<Record<string, unknown>>
  subscribeToUpdaterState?: (
    listener: (snapshot: Record<string, unknown>) => void
  ) => () => void
  releaseCertification?: (
    payload: Readonly<Record<string, unknown>>
  ) => Promise<Record<string, unknown>>
  getBackendStatus: () => Promise<SigilBackendResponse<SigilBackendStatus>>
  getRuntimeSnapshot?: () => Promise<SigilBackendResponse<SigilRuntimeSnapshot>>
  controlPaperCycle?: (
    action: 'start' | 'pause' | 'stop'
  ) => Promise<SigilBackendResponse<SigilRuntimeSnapshot>>
  controlPaperAuthorization?: (
    action: 'grant' | 'revoke'
  ) => Promise<SigilBackendResponse<SigilRuntimeSnapshot>>
  resetPaperRuntime?: () => Promise<SigilBackendResponse<SigilRuntimeSnapshot>>
  getProviderSnapshot?: () => Promise<
    SigilBackendResponse<SigilProviderSnapshot>
  >
  getAIStatus?: () => Promise<SigilBackendResponse<Record<string, unknown>>>
  getAlpacaMarketDataStatus?: () => Promise<
    SigilBackendResponse<Record<string, unknown>>
  >
  controlAlpacaMarketData?: (
    action: string
  ) => Promise<SigilBackendResponse<Record<string, unknown>>>
  getMarketUniverseStatus?: () => Promise<
    SigilBackendResponse<MarketUniverseStatus>
  >
  searchMarketUniverse?: (
    payload: Readonly<Record<string, unknown>>
  ) => Promise<SigilBackendResponse<MarketUniverseSearchResult>>
  getMarketUniverseQuotes?: (
    payload: Readonly<Record<string, unknown>>
  ) => Promise<SigilBackendResponse<MarketUniverseQuoteResult>>
  getAssetCatalogStatus?: () => Promise<
    SigilBackendResponse<AssetCatalogStatus>
  >
  refreshAssetCatalog?: () => Promise<
    SigilBackendResponse<AssetCatalogStatus>
  >
  paperExecution?: (
    operation: string,
    payload?: Readonly<Record<string, unknown>>
  ) => Promise<SigilBackendResponse<Record<string, unknown>>>
  productionResearch?: (
    operation: string,
    payload?: Readonly<Record<string, unknown>>
  ) => Promise<SigilBackendResponse<Record<string, unknown>>>
  explainProposal: (
    payload: SigilProposalExplanationPayload
  ) => Promise<SigilBackendResponse<SigilBackendProposalExplanation>>
}

interface Window {
  sigilDesktop?: SigilDesktopApi
}
}
