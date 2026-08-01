import { contextBridge, ipcRenderer } from 'electron'

declare const __SIGIL_BUILD_ID__: string
declare const __SIGIL_BUILD_COMMIT__: string
declare const __SIGIL_BUILD_TIME__: string
declare const __SIGIL_VERSION__: string

const SIGIL_BACKEND_STATUS_CHANNEL = 'sigil:get-backend-status'
const SIGIL_GOVERNED_NEWS_STREAM_STATUS_CHANNEL = 'sigil:get-governed-news-stream-status'
const SIGIL_EXPLAIN_PROPOSAL_CHANNEL = 'sigil:explain-proposal'
const SIGIL_RUNTIME_SNAPSHOT_CHANNEL = 'sigil:get-runtime-snapshot'
const SIGIL_PAPER_CYCLE_CONTROL_CHANNEL = 'sigil:control-paper-cycle'
const SIGIL_PAPER_AUTHORIZATION_CONTROL_CHANNEL = 'sigil:control-paper-authorization'
const SIGIL_PAPER_RUNTIME_RESET_CHANNEL = 'sigil:reset-paper-runtime'
const SIGIL_PROVIDER_SNAPSHOT_CHANNEL = 'sigil:get-provider-snapshot'
const SIGIL_AI_STATUS_CHANNEL = 'sigil:get-ai-status'
const SIGIL_MARKET_UNIVERSE_STATUS_CHANNEL = 'sigil:get-market-universe-status'
const SIGIL_MARKET_UNIVERSE_SEARCH_CHANNEL = 'sigil:search-market-universe'
const SIGIL_MARKET_UNIVERSE_QUOTES_CHANNEL = 'sigil:get-market-universe-quotes'
const SIGIL_ALPACA_MARKET_DATA_STATUS_CHANNEL = 'sigil:get-alpaca-market-data-status'
const SIGIL_ALPACA_MARKET_DATA_CONTROL_CHANNEL = 'sigil:control-alpaca-market-data'
const SIGIL_ASSET_CATALOG_STATUS_CHANNEL = 'sigil:get-asset-catalog-status'
const SIGIL_ASSET_CATALOG_REFRESH_CHANNEL = 'sigil:refresh-asset-catalog'
const SIGIL_RESEARCH_UNIVERSE_STATUS_CHANNEL = 'sigil:get-research-universe-status'
const SIGIL_PAPER_EXECUTION_CHANNEL = 'sigil:paper-execution'
const SIGIL_PRODUCTION_RESEARCH_CHANNEL = 'sigil:production-research'
const SIGIL_UPDATE_CHECK_CHANNEL = 'sigil:check-for-updates'
const SIGIL_UPDATER_SNAPSHOT_CHANNEL = 'sigil:get-updater-snapshot'
const SIGIL_UPDATE_DOWNLOAD_CHANNEL = 'sigil:approve-update-download'
const SIGIL_UPDATE_DEFER_CHANNEL = 'sigil:defer-update'
const SIGIL_UPDATE_INSTALL_CHANNEL = 'sigil:restart-and-install-update'
const SIGIL_UPDATER_STATE_EVENT = 'sigil:updater-state'
const SIGIL_RELEASE_CERTIFICATION_CHANNEL = 'sigil:release-certification'

contextBridge.exposeInMainWorld('sigilDesktop', {
  productName: 'Sigil',
  persistenceNamespace: 'com.firecattechnology.sigil',
  brokerSubmissionAvailable: false,
  buildInfo: {
    version: __SIGIL_VERSION__,
    build: __SIGIL_BUILD_ID__,
    commit: __SIGIL_BUILD_COMMIT__,
    buildTime: __SIGIL_BUILD_TIME__,
    channel: process.env.SIGIL_DEV_SERVER ? 'dev' : 'release',
    applicationMode: process.env.SIGIL_DEV_SERVER ? 'Live development' : 'Packaged release'
  },
  getUpdaterSnapshot: () => ipcRenderer.invoke(SIGIL_UPDATER_SNAPSHOT_CHANNEL),
  checkForUpdates: () => ipcRenderer.invoke(SIGIL_UPDATE_CHECK_CHANNEL),
  approveUpdateDownload: () => ipcRenderer.invoke(SIGIL_UPDATE_DOWNLOAD_CHANNEL),
  deferUpdate: () => ipcRenderer.invoke(SIGIL_UPDATE_DEFER_CHANNEL),
  restartAndInstallUpdate: () => ipcRenderer.invoke(SIGIL_UPDATE_INSTALL_CHANNEL),
  subscribeToUpdaterState: (listener: (snapshot: unknown) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, snapshot: unknown) => listener(snapshot)
    ipcRenderer.on(SIGIL_UPDATER_STATE_EVENT, handler)

    return () => ipcRenderer.removeListener(SIGIL_UPDATER_STATE_EVENT, handler)
  },
  releaseCertification: (payload: Readonly<Record<string, unknown>>) =>
    ipcRenderer.invoke(SIGIL_RELEASE_CERTIFICATION_CHANNEL, payload),
  getBackendStatus: () => ipcRenderer.invoke(SIGIL_BACKEND_STATUS_CHANNEL),
  getRuntimeSnapshot: () => ipcRenderer.invoke(SIGIL_RUNTIME_SNAPSHOT_CHANNEL),
  controlPaperCycle: (action: 'start' | 'pause' | 'stop') =>
    ipcRenderer.invoke(SIGIL_PAPER_CYCLE_CONTROL_CHANNEL, action),
  controlPaperAuthorization: (action: 'grant' | 'revoke') =>
    ipcRenderer.invoke(SIGIL_PAPER_AUTHORIZATION_CONTROL_CHANNEL, action),
  resetPaperRuntime: () => ipcRenderer.invoke(SIGIL_PAPER_RUNTIME_RESET_CHANNEL),
  getProviderSnapshot: () => ipcRenderer.invoke(SIGIL_PROVIDER_SNAPSHOT_CHANNEL),
  getAIStatus: () => ipcRenderer.invoke(SIGIL_AI_STATUS_CHANNEL),
  getMarketUniverseStatus: () => ipcRenderer.invoke(SIGIL_MARKET_UNIVERSE_STATUS_CHANNEL),
  searchMarketUniverse: (payload: Readonly<Record<string, unknown>>) =>
    ipcRenderer.invoke(SIGIL_MARKET_UNIVERSE_SEARCH_CHANNEL, payload),
  getMarketUniverseQuotes: (payload: Readonly<Record<string, unknown>>) =>
    ipcRenderer.invoke(SIGIL_MARKET_UNIVERSE_QUOTES_CHANNEL, payload),
  getAlpacaMarketDataStatus: () => ipcRenderer.invoke(SIGIL_ALPACA_MARKET_DATA_STATUS_CHANNEL),
  controlAlpacaMarketData: (action: string) => ipcRenderer.invoke(SIGIL_ALPACA_MARKET_DATA_CONTROL_CHANNEL, action),
  getAssetCatalogStatus: () => ipcRenderer.invoke(SIGIL_ASSET_CATALOG_STATUS_CHANNEL),
  refreshAssetCatalog: () => ipcRenderer.invoke(SIGIL_ASSET_CATALOG_REFRESH_CHANNEL),
  getResearchUniverseStatus: () => ipcRenderer.invoke(SIGIL_RESEARCH_UNIVERSE_STATUS_CHANNEL),
  paperExecution: (operation: string, payload?: Readonly<Record<string, unknown>>) =>
    ipcRenderer.invoke(SIGIL_PAPER_EXECUTION_CHANNEL, operation, payload),
  productionResearch: (operation: string, payload?: Readonly<Record<string, unknown>>) =>
    ipcRenderer.invoke(SIGIL_PRODUCTION_RESEARCH_CHANNEL, operation, payload),
  explainProposal: (payload: Readonly<Record<string, unknown>>) =>
    ipcRenderer.invoke(SIGIL_EXPLAIN_PROPOSAL_CHANNEL, payload),

  getGovernedNewsStatus: () => ipcRenderer.invoke('sigil:get-governed-news-status'),
  getGovernedNewsStreamStatus: () => ipcRenderer.invoke(SIGIL_GOVERNED_NEWS_STREAM_STATUS_CHANNEL),
  getGovernedNewsTimeline: (symbol: string) => ipcRenderer.invoke('sigil:get-governed-news-timeline', symbol),
  getGovernedNewsAdvisorySummary: () => ipcRenderer.invoke('sigil:get-governed-news-advisory-summary'),
  collectGovernedAlpacaNews: (symbols: string[]) => ipcRenderer.invoke('sigil:collect-governed-alpaca-news', symbols)
})
