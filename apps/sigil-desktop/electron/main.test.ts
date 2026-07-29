import fs from 'node:fs'
import path from 'node:path'

describe('Sigil Electron startup', () => {
  it('owns a standalone title and persistence namespace', () => {
    const source = fs.readFileSync(path.resolve('electron/main.ts'), 'utf8')
    expect(source).toContain("SIGIL_APP_NAME = 'Sigil'")
    expect(source).toContain("SIGIL_BUNDLE_ID = 'com.firecattechnology.sigil'")
    expect(source).toContain("SIGIL_USER_DATA_DIRECTORY = 'Sigil'")
    expect(source).toContain("title: SIGIL_APP_NAME")
    expect(source).not.toContain('HERMES_DESKTOP')
  })

  it('keeps provider access in a bounded backend-only IPC channel', () => {
    const mainSource = fs.readFileSync(path.resolve('electron/main.ts'), 'utf8')
    const preloadSource = fs.readFileSync(path.resolve('electron/preload.ts'), 'utf8')

    expect(mainSource).toContain("SIGIL_PROVIDER_SNAPSHOT_CHANNEL = 'sigil:get-provider-snapshot'")
    expect(mainSource).toContain("runBridgeRequest({ command: 'provider_snapshot' })")
    expect(mainSource).toContain("SIGIL_PAPER_AUTHORIZATION_CONTROL_CHANNEL")
    expect(mainSource).toContain("command: 'control_paper_authorization'")
    expect(mainSource).toContain("SIGIL_PAPER_RUNTIME_RESET_CHANNEL")
    expect(mainSource).toContain("command: 'reset_paper_runtime'")
    expect(mainSource).toContain("confirmation: 'RESET LOCAL PAPER PORTFOLIO'")
    expect(preloadSource).toContain('getProviderSnapshot')
    expect(mainSource).toContain("command: 'market_universe_status'")
    expect(mainSource).toContain("command: 'market_universe_search'")
    expect(preloadSource).toContain('getMarketUniverseStatus')
    expect(preloadSource).toContain('searchMarketUniverse')
    expect(mainSource).toContain("command: 'alpaca_market_data_status'")
    expect(mainSource).toContain("command: 'control_alpaca_market_data'")
    expect(preloadSource).toContain('getAlpacaMarketDataStatus')
    expect(preloadSource).toContain('controlAlpacaMarketData')
    expect(preloadSource).toContain('controlPaperAuthorization')
    expect(preloadSource).toContain('resetPaperRuntime')
    expect(preloadSource).not.toContain('SIGIL_ALPACA_API_SECRET_KEY')
    expect(preloadSource).not.toContain('SIGIL_PUBLIC_API_SECRET')
    expect(preloadSource).not.toContain('submitOrder')
    expect(mainSource).toContain("'runtime_snapshot',")
    expect(mainSource).toContain("'control_paper_cycle'")
    expect(mainSource).toContain('? 45_000')
  })

  it('exposes only data controls for Alpaca free market data', () => {
    const source = fs.readFileSync(path.resolve('src/mission-control/index.tsx'), 'utf8')

    expect(source).toContain("RELEASE_STAGE = 'V2.1'")
    expect(source).toContain('15-minute delayed SIP')
    expect(source).toContain('live partial-market IEX')
    expect(source).toContain('Live IEX capacity')
    expect(source).toContain('Data-only mode')
    expect(source).toContain('Live trading disabled')
    expect(source).toContain('Refresh Alpaca assets')
    expect(source).toContain("['Discovered', status.source_record_count]")
    expect(source).toContain("['Proposal eligible', status.proposal_eligible_count]")
    expect(source).toContain('Refresh catalog')
    expect(source).not.toContain('Submit Alpaca order')
  })
})
