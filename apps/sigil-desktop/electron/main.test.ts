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
    expect(preloadSource).toContain('getProviderSnapshot')
    expect(preloadSource).not.toContain('SIGIL_ALPACA_API_SECRET_KEY')
    expect(preloadSource).not.toContain('SIGIL_PUBLIC_API_SECRET')
    expect(preloadSource).not.toContain('submitOrder')
  })
})
