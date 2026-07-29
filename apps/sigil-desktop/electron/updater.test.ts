import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import {
  compareVersions,
  GovernedUpdater,
  sanitizeUpdaterText,
  UnavailableUpdater,
  type UpdaterClient
} from './updater'

class FakeUpdater extends EventEmitter implements UpdaterClient {
  autoDownload = true
  autoInstallOnAppQuit = true
  allowPrerelease = false
  allowDowngrade = true
  channel: string | null = null
  checkCount = 0
  downloadCount = 0
  installCount = 0
  checkPromise: Promise<unknown> = Promise.resolve()
  downloadPromise: Promise<unknown> = Promise.resolve()
  checkForUpdates() { this.checkCount += 1;

 return this.checkPromise }
  downloadUpdate() { this.downloadCount += 1;

 return this.downloadPromise }
  quitAndInstall() { this.installCount += 1 }
}

function harness(overrides: { packaged?: boolean; developmentEnabled?: boolean; internalTest?: boolean; ready?: () => Promise<{ ready: boolean; reason?: string }> } = {}) {
  const client = new FakeUpdater()
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sigil-updater-test.'))

  const controller = new GovernedUpdater({
    client,
    policy: {
      packaged: overrides.packaged ?? true,
      developmentEnabled: overrides.developmentEnabled ?? false,
      internalTest: overrides.internalTest ?? false,
      currentVersion: '1.8.0-test.1'
    },
    auditPath: path.join(root, 'audit.jsonl'),
    getWindows: () => [],
    installReady: overrides.ready ?? (async () => ({ ready: true })),
    now: () => '2026-07-28T00:00:00.000Z'
  })

  return { client, controller, root }
}

describe('GovernedUpdater', () => {
  it('degrades updater initialization failures without rejecting UI operations', async () => {
    const controller = new UnavailableUpdater(
      '1.8.0',
      new Error('startup token=secret')
    )

    expect(controller.getSnapshot()).toMatchObject({
      status: 'failed',
      error: {
        code: 'initialization_failed',
        message: 'startup token=[REDACTED]'
      }
    })
    await expect(controller.check()).resolves.toEqual(controller.getSnapshot())
  })

  it('is disabled in ordinary development and enables the explicit internal channel', () => {
    expect(harness({ packaged: false }).controller.getSnapshot().status).toBe('disabled')
    const internal = harness({ packaged: false, developmentEnabled: true, internalTest: true })
    expect(internal.controller.getSnapshot().status).toBe('idle')
    expect(internal.client.allowPrerelease).toBe(true)
    expect(internal.client.channel).toBe('internal')
  })

  it('suppresses concurrent checks and downloads', async () => {
    const { client, controller } = harness()
    let releaseCheck!: () => void
    client.checkPromise = new Promise(resolve => { releaseCheck = () => resolve(undefined) })
    const first = controller.check()
    const second = controller.check()
    expect(client.checkCount).toBe(1)
    releaseCheck()
    await Promise.all([first, second])
    client.emit('update-available', { version: '1.8.0-test.2', releaseNotes: '<b>Safe</b> notes' })
    let releaseDownload!: () => void
    client.downloadPromise = new Promise(resolve => { releaseDownload = () => resolve(undefined) })
    const downloadOne = controller.approveDownload()
    const downloadTwo = controller.approveDownload()
    expect(client.downloadCount).toBe(1)
    releaseDownload()
    await Promise.all([downloadOne, downloadTwo])
  })

  it('requires approval, supports progress/defer, and installs only through a double gate', async () => {
    let gateCalls = 0
    const { client, controller } = harness({ ready: async () => ({ ready: ++gateCalls <= 2 }) })
    client.emit('update-available', { version: '1.8.0-test.2', releaseNotes: '<script>x</script>Release' })
    expect(controller.getSnapshot().releaseNotes).not.toContain('<script>')
    await controller.approveDownload()
    client.emit('download-progress', { percent: 55, transferred: 5, total: 10, bytesPerSecond: 2 })
    client.emit('update-downloaded')
    expect(controller.defer().status).toBe('deferred')
    await controller.restartAndInstall()
    expect(client.installCount).toBe(1)
    expect(gateCalls).toBe(2)
  })

  it('blocks install when protected work is active and records audit evidence', async () => {
    const { client, controller, root } = harness({ ready: async () => ({ ready: false, reason: 'Paper cycle running.' }) })
    client.emit('update-available', { version: '1.8.1' })
    await controller.approveDownload()
    client.emit('update-downloaded')
    expect((await controller.restartAndInstall()).status).toBe('downloaded')
    expect(client.installCount).toBe(0)
    expect(fs.readFileSync(path.join(root, 'audit.jsonl'), 'utf8')).toContain('updater.install.blocked')
  })

  it('rejects malformed, equal, and downgrade versions and normalizes failures', () => {
    for (const version of ['bad', '1.8.0-test.1', '1.7.9']) {
      const { client, controller } = harness()
      client.emit('update-available', { version })
      expect(controller.getSnapshot().status).toBe('failed')
    }

    expect(compareVersions('1.8.0-test.2', '1.8.0-test.1')).toBeGreaterThan(0)
    expect(sanitizeUpdaterText('https://x.test?a=1&token=secret')).toContain('[REDACTED]')
  })

  it('propagates network and checksum errors without secrets', async () => {
    const { client, controller } = harness()
    client.checkPromise = Promise.reject(new Error('network token=secret'))
    expect((await controller.check()).error).toEqual({
      code: 'check_failed',
      message: 'network token=[REDACTED]'
    })
    client.emit('error', new Error('checksum mismatch?signature=private'))
    expect(controller.getSnapshot().error?.message).toContain('[REDACTED]')
  })
})
