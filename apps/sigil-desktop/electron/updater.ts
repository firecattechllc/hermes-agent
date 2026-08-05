import fs from 'node:fs'
import path from 'node:path'

import type { BrowserWindow } from 'electron'

export type UpdaterStatus =
  | 'idle'
  | 'checking'
  | 'update-available'
  | 'up-to-date'
  | 'downloading'
  | 'downloaded'
  | 'installing'
  | 'deferred'
  | 'failed'
  | 'disabled'

export type UpdaterSnapshot = Readonly<{
  status: UpdaterStatus
  currentVersion: string
  availableVersion: string | null
  releaseNotes: string | null
  progress: Readonly<{
    percent: number
    transferred: number
    total: number
    bytesPerSecond: number
  }> | null
  internalTest: boolean
  message: string
  error: Readonly<{ code: string; message: string }> | null
}>

type UpdateInfo = Readonly<{
  version: string
  releaseNotes?: string | Array<Readonly<{ note: string }>> | null
}>

export type UpdaterClient = {
  logger?: unknown
  autoDownload: boolean
  autoInstallOnAppQuit: boolean
  allowPrerelease: boolean
  allowDowngrade: boolean
  channel: string | null
  on(event: string, listener: (...args: any[]) => void): unknown
  checkForUpdates(): Promise<unknown>
  downloadUpdate(): Promise<unknown>
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void
}

export type UpdaterPolicy = Readonly<{
  packaged: boolean
  developmentEnabled: boolean
  internalTest: boolean
  currentVersion: string
}>

export interface UpdaterController {
  getSnapshot(): UpdaterSnapshot
  check(): Promise<UpdaterSnapshot>
  approveDownload(): Promise<UpdaterSnapshot>
  defer(): UpdaterSnapshot
  restartAndInstall(): Promise<UpdaterSnapshot>
}

type Dependencies = Readonly<{
  client: UpdaterClient
  policy: UpdaterPolicy
  auditPath: string
  getWindows: () => BrowserWindow[]
  installReady: () => Promise<Readonly<{ ready: boolean; reason?: string }>>
  now?: () => string
}>

const SECRET_PATTERN =
  /((?:token|secret|password|authorization|credential|api[_-]?key)=)[^&\s]+/gi

export function sanitizeUpdaterText(value: unknown): string {
  return String(value ?? 'Unknown update error')
    .replace(SECRET_PATTERN, '$1[REDACTED]')
    .replace(/([?&](?:token|key|secret|signature)=)[^&\s]+/gi, '$1[REDACTED]')
    .slice(0, 1_000)
}

export function compareVersions(left: string, right: string): number {
  const parse = (value: string) => {
    const match = value.match(/^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/)

    if (!match) {
      throw new Error('Invalid update version.')
    }

    return {
      parts: [Number(match[1]), Number(match[2]), Number(match[3])],
      prerelease: match[4] ?? null
    }
  }

  const a = parse(left)
  const b = parse(right)

  for (let index = 0; index < 3; index += 1) {
    if (a.parts[index] !== b.parts[index]) {
      return (a.parts[index] ?? 0) > (b.parts[index] ?? 0) ? 1 : -1
    }
  }

  if (a.prerelease === b.prerelease) {return 0}

  if (a.prerelease === null) {return 1}

  if (b.prerelease === null) {return -1}

  return a.prerelease.localeCompare(b.prerelease, undefined, { numeric: true })
}

function releaseNotes(info: UpdateInfo): string | null {
  if (typeof info.releaseNotes === 'string') {
    return info.releaseNotes.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 4_000)
  }

  if (Array.isArray(info.releaseNotes)) {
    return info.releaseNotes.map(item => item.note).join('\n').replace(/<[^>]*>/g, ' ').slice(0, 4_000)
  }

  return null
}

export class GovernedUpdater implements UpdaterController {
  private snapshot: UpdaterSnapshot
  private checkPromise: Promise<UpdaterSnapshot> | null = null
  private downloadPromise: Promise<UpdaterSnapshot> | null = null
  private lastProgressBucket = -1
  private readonly now: () => string

  constructor(private readonly dependencies: Dependencies) {
    this.now = dependencies.now ?? (() => new Date().toISOString())
    const enabled = dependencies.policy.packaged || dependencies.policy.developmentEnabled
    this.snapshot = {
      status: enabled ? 'idle' : 'disabled',
      currentVersion: dependencies.policy.currentVersion,
      availableVersion: null,
      releaseNotes: null,
      progress: null,
      internalTest: dependencies.policy.internalTest,
      message: enabled
        ? 'Updates are ready to check.'
        : 'Updates are disabled in development mode.',
      error: null
    }
    dependencies.client.autoDownload = false
    dependencies.client.autoInstallOnAppQuit = false
    dependencies.client.allowPrerelease = dependencies.policy.internalTest
    dependencies.client.allowDowngrade = false
    dependencies.client.channel = dependencies.policy.internalTest ? 'internal' : null
    this.bindEvents()
  }

  getSnapshot(): UpdaterSnapshot {
    return this.snapshot
  }

  async check(): Promise<UpdaterSnapshot> {
    if (this.snapshot.status === 'disabled') {return this.snapshot}

    if (this.checkPromise) {return this.checkPromise}
    this.transition('checking', 'Checking for updates.', 'updater.check.requested')
    this.checkPromise = this.dependencies.client
      .checkForUpdates()
      .then(() => this.snapshot)
      .catch(error => this.fail('check_failed', error))
      .finally(() => {
        this.checkPromise = null
      })

    return this.checkPromise
  }

  async approveDownload(): Promise<UpdaterSnapshot> {
    if (this.downloadPromise) {return this.downloadPromise}

    if (this.snapshot.status !== 'update-available') {
      return this.fail('invalid_transition', 'No approved update is available to download.')
    }

    this.audit('updater.download.approved')
    this.transition('downloading', 'Downloading approved update.', 'updater.download.started')
    this.downloadPromise = this.dependencies.client
      .downloadUpdate()
      .then(() => this.snapshot)
      .catch(error => this.fail('download_failed', error))
      .finally(() => {
        this.downloadPromise = null
      })

    return this.downloadPromise
  }

  defer(): UpdaterSnapshot {
    if (!['update-available', 'downloaded'].includes(this.snapshot.status)) {
      return this.fail('invalid_transition', 'There is no update to defer.')
    }

    this.transition('deferred', 'Update deferred until the operator returns.', 'updater.install.deferred')

    return this.snapshot
  }

  async restartAndInstall(): Promise<UpdaterSnapshot> {
    if (!['downloaded', 'deferred'].includes(this.snapshot.status)) {
      return this.fail('invalid_transition', 'No downloaded update is ready to install.')
    }

    this.audit('updater.install.approved')
    const gate = await this.dependencies.installReady()

    if (!gate.ready) {
      this.transition(
        'downloaded',
        sanitizeUpdaterText(gate.reason ?? 'A protected operation is active.'),
        'updater.install.blocked'
      )

      return this.snapshot
    }

    // Re-check immediately before handing control to Electron.
    const finalGate = await this.dependencies.installReady()

    if (!finalGate.ready) {
      this.transition(
        'downloaded',
        sanitizeUpdaterText(finalGate.reason ?? 'The installation gate changed.'),
        'updater.install.blocked'
      )

      return this.snapshot
    }

    this.transition('installing', 'Restarting to install the approved update.', 'updater.install.started')
    this.dependencies.client.quitAndInstall(false, true)

    return this.snapshot
  }

  private bindEvents(): void {
    const client = this.dependencies.client
    client.on('update-available', (info: UpdateInfo) => {
      try {
        if (compareVersions(info.version, this.snapshot.currentVersion) <= 0) {
          this.fail('unsafe_version', 'Equal-version updates and downgrades are not allowed.')

          return
        }
      } catch (error) {
        this.fail('malformed_metadata', error)

        return
      }

      this.snapshot = {
        ...this.snapshot,
        availableVersion: info.version,
        releaseNotes: releaseNotes(info),
        error: null
      }
      this.transition('update-available', `Version ${info.version} is available.`, 'updater.update.available')
    })
    client.on('update-not-available', () => {
      this.transition('up-to-date', 'Sigil is up to date.', 'updater.update.not_available')
    })
    client.on('download-progress', (progress: any) => {
      const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0))
      this.snapshot = {
        ...this.snapshot,
        status: 'downloading',
        progress: {
          percent,
          transferred: Number(progress.transferred) || 0,
          total: Number(progress.total) || 0,
          bytesPerSecond: Number(progress.bytesPerSecond) || 0
        },
        message: `Downloading update: ${percent.toFixed(1)}%.`
      }
      const bucket = Math.floor(percent / 10)

      if (bucket > this.lastProgressBucket) {
        this.lastProgressBucket = bucket
        this.audit('updater.download.progress', { bucket: bucket * 10 })
      }

      this.broadcast()
    })
    client.on('update-downloaded', () => {
      this.transition('downloaded', 'Update downloaded. Restart requires approval.', 'updater.download.completed')
    })
    client.on('error', (error: unknown) => {
      this.fail('updater_error', error)
    })
  }

  private fail(code: string, error: unknown): UpdaterSnapshot {
    const message = sanitizeUpdaterText(error instanceof Error ? error.message : error)
    this.snapshot = {
      ...this.snapshot,
      status: 'failed',
      message,
      error: { code, message }
    }
    this.audit('updater.failed', { code, message })
    this.broadcast()

    return this.snapshot
  }

  private transition(status: UpdaterStatus, message: string, auditEvent: string): void {
    this.snapshot = { ...this.snapshot, status, message, error: null }
    this.audit(auditEvent)
    this.broadcast()
  }

  private audit(event: string, details: Readonly<Record<string, unknown>> = {}): void {
    const record = JSON.stringify({
      schema_version: 1,
      timestamp: this.now(),
      event,
      current_version: this.snapshot.currentVersion,
      target_version: this.snapshot.availableVersion,
      internal_test: this.snapshot.internalTest,
      details
    })

    fs.mkdirSync(path.dirname(this.dependencies.auditPath), { recursive: true })
    const descriptor = fs.openSync(this.dependencies.auditPath, 'a', 0o600)

    try {
      fs.writeSync(descriptor, `${record}\n`)
      fs.fsyncSync(descriptor)
    } finally {
      fs.closeSync(descriptor)
    }
  }

  private broadcast(): void {
    for (const window of this.dependencies.getWindows()) {
      if (!window.isDestroyed()) {
        window.webContents.send('sigil:updater-state', this.snapshot)
      }
    }
  }
}

export class UnavailableUpdater implements UpdaterController {
  private readonly snapshot: UpdaterSnapshot

  constructor(currentVersion: string, error: unknown) {
    const message = sanitizeUpdaterText(
      error instanceof Error ? error.message : error
    )

    this.snapshot = {
      status: 'failed',
      currentVersion,
      availableVersion: null,
      releaseNotes: null,
      progress: null,
      internalTest: false,
      message: `Updates are unavailable: ${message}`,
      error: { code: 'initialization_failed', message }
    }
  }

  getSnapshot(): UpdaterSnapshot {
    return this.snapshot
  }

  async check(): Promise<UpdaterSnapshot> {
    return this.snapshot
  }

  async approveDownload(): Promise<UpdaterSnapshot> {
    return this.snapshot
  }

  defer(): UpdaterSnapshot {
    return this.snapshot
  }

  async restartAndInstall(): Promise<UpdaterSnapshot> {
    return this.snapshot
  }
}
