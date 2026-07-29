import fs from 'node:fs'
import path from 'node:path'

describe('governed updater preload API', () => {
  const source = fs.readFileSync(path.resolve('electron/preload.ts'), 'utf8')

  it('exposes only fixed updater operations and cleans up listeners', () => {
    for (const operation of [
      'getUpdaterSnapshot',
      'checkForUpdates',
      'approveUpdateDownload',
      'deferUpdate',
      'restartAndInstallUpdate',
      'subscribeToUpdaterState'
    ]) {
      expect(source).toContain(operation)
    }

    expect(source).toContain('removeListener(SIGIL_UPDATER_STATE_EVENT, handler)')
    expect(source).not.toContain('ipcRenderer,')
    expect(source).not.toContain('updateUrl')
  })
})
