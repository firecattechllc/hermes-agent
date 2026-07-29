import { spawn } from 'node:child_process'
import { existsSync } from "node:fs"
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { app, BrowserWindow, ipcMain, nativeTheme } from 'electron'

import {
  GovernedUpdater,
  UnavailableUpdater,
  type UpdaterController
} from './updater'

export const SIGIL_APP_NAME = 'Sigil'
export const SIGIL_BUNDLE_ID = 'com.firecattechnology.sigil'
export const SIGIL_USER_DATA_DIRECTORY = 'Sigil'
export const SIGIL_BACKEND_STATUS_CHANNEL = 'sigil:get-backend-status'
export const SIGIL_EXPLAIN_PROPOSAL_CHANNEL = 'sigil:explain-proposal'
export const SIGIL_RUNTIME_SNAPSHOT_CHANNEL = 'sigil:get-runtime-snapshot'
export const SIGIL_PAPER_CYCLE_CONTROL_CHANNEL = 'sigil:control-paper-cycle'
export const SIGIL_PAPER_AUTHORIZATION_CONTROL_CHANNEL = 'sigil:control-paper-authorization'
export const SIGIL_PAPER_RUNTIME_RESET_CHANNEL = 'sigil:reset-paper-runtime'
export const SIGIL_PROVIDER_SNAPSHOT_CHANNEL = 'sigil:get-provider-snapshot'
export const SIGIL_MARKET_UNIVERSE_STATUS_CHANNEL = 'sigil:get-market-universe-status'
export const SIGIL_MARKET_UNIVERSE_SEARCH_CHANNEL = 'sigil:search-market-universe'
export const SIGIL_ALPACA_MARKET_DATA_STATUS_CHANNEL = 'sigil:get-alpaca-market-data-status'
export const SIGIL_ALPACA_MARKET_DATA_CONTROL_CHANNEL = 'sigil:control-alpaca-market-data'
export const SIGIL_ASSET_CATALOG_STATUS_CHANNEL = 'sigil:get-asset-catalog-status'
export const SIGIL_ASSET_CATALOG_REFRESH_CHANNEL = 'sigil:refresh-asset-catalog'
export const SIGIL_RESEARCH_UNIVERSE_STATUS_CHANNEL = 'sigil:get-research-universe-status'
export const SIGIL_UPDATER_SNAPSHOT_CHANNEL = 'sigil:get-updater-snapshot'
export const SIGIL_UPDATE_CHECK_CHANNEL = 'sigil:check-for-updates'
export const SIGIL_UPDATE_DOWNLOAD_CHANNEL = 'sigil:approve-update-download'
export const SIGIL_UPDATE_DEFER_CHANNEL = 'sigil:defer-update'
export const SIGIL_UPDATE_INSTALL_CHANNEL = 'sigil:restart-and-install-update'
export const SIGIL_RELEASE_CERTIFICATION_CHANNEL = 'sigil:release-certification'

const certificationProposals = new Map([
  ['CERT-APPROVE', 'pending'],
  ['CERT-REJECT', 'pending'],
  ['CERT-CANCEL', 'pending']
])

function certificationResponse(payload: Readonly<Record<string, unknown>>) {
  const token = process.argv
    .find(argument => argument.startsWith('--sigil-release-certification='))
    ?.split('=', 2)[1]

  const enabled =
    Boolean(token) &&
    token === process.env.SIGIL_RELEASE_CERTIFICATION_TOKEN

  if (!enabled) {
    return { bounded: true, error: 'certification_unavailable' }
  }

  if (payload.operation === 'proposal-state') {
    return { proposals: Object.fromEntries(certificationProposals) }
  }

  if (payload.operation === 'proposal-action') {
    const proposalId = String(payload.proposalId ?? '')
    const action = String(payload.action ?? '')

    if (certificationProposals.has(proposalId) && ['approve', 'reject'].includes(action)) {
      certificationProposals.set(proposalId, action === 'approve' ? 'approved' : 'rejected')
    }

    return {
      proposals: Object.fromEntries(certificationProposals),
      safety: {
        trade: 0,
        order: 0,
        transfer: 0,
        approval: 0,
        broker_submission: 0,
        wallet_mutation: 0,
        persistent_financial_mutation: 0,
        external_network: 0
      }
    }
  }

  if (payload.operation === 'updater-check') {
    const metadata = payload.metadata

    if (typeof metadata !== 'string') {
      return { bounded: true, downloaded: false, installed: false }
    }

    const match = metadata.match(/^version:\s*([^\s]+)$/m)

    if (!match || !/^\d+\.\d+\.\d+$/.test(match[1] ?? '')) {
      return { bounded: true, downloaded: false, installed: false }
    }

    const updateVersion = match[1]
    const current = app.getVersion().split('.').map(Number)
    const candidate = updateVersion.split('.').map(Number)

    const updateAvailable = candidate.some(
      (value, index) =>
        value > (current[index] ?? 0) &&
        candidate.slice(0, index).every(
          (part, prior) => part === (current[prior] ?? 0)
        )
    )

    return {
      bounded: true,
      updateAvailable,
      updateVersion,
      downloaded: false,
      installed: false
    }
  }

  return { bounded: true }
}

type BackendStatus = {
  bridge_version: string
  status: string
  mode: string
  environment: string
  simulation: boolean
  execution_authorized: boolean
  broker_submission_available: boolean
  supported_commands: string[]
}

type BackendResponse =
  | {
      ok: true
      result: BackendStatus
    }
  | {
      ok: false
      error: string
      message: string
    }

app.setName(SIGIL_APP_NAME)
nativeTheme.themeSource = 'dark'

const currentDirectory = path.dirname(fileURLToPath(import.meta.url))

function repositoryRoot(): string {
  return path.resolve(currentDirectory, '../../..')
}

function pythonExecutable(): string {
  if (process.env.SIGIL_PYTHON) {
    return process.env.SIGIL_PYTHON
  }

  if (!app.isPackaged) {
    return path.join(repositoryRoot(), 'apps/sigil/.venv/bin/python')
  }

  const packagedCandidates = [
    '/opt/homebrew/opt/python@3.11/bin/python3.11',
    '/usr/local/opt/python@3.11/bin/python3.11'
  ]

  const packagedPython = packagedCandidates.find(candidate =>
    existsSync(candidate)
  )

  if (!packagedPython) {
    throw new Error(
      'Sigil requires Python 3.11. No certified packaged runtime was found.'
    )
  }

  return packagedPython
}

function backendSourceRoot(): string {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'sigil-backend')
    : path.join(repositoryRoot(), 'apps/sigil/src')
}

function backendWorkingDirectory(): string {
  return app.isPackaged ? process.resourcesPath : repositoryRoot()
}

type BridgeRequest = Readonly<{
  command: string
  payload?: Readonly<Record<string, unknown>>
}>

type BridgeResponse<T = unknown> =
  | {
      ok: true
      result: T
    }
  | {
      ok: false
      error: string
      message: string
    }

export function runBridgeRequest<T>(
  request: BridgeRequest
): Promise<BridgeResponse<T>> {
  return new Promise(resolve => {
    const sourceRoot = backendSourceRoot()

    const child = spawn(
      pythonExecutable(),
      ['-m', 'sigil.desktop_bridge.runner'],
      {
        cwd: backendWorkingDirectory(),
        env: {
          ...process.env,
          PYTHONPATH: sourceRoot,
          SIGIL_DESKTOP_STATE_DIR: path.join(app.getPath('userData'), 'paper-runtime')
        },
        stdio: ['pipe', 'pipe', 'pipe']
      }
    )

    let stdout = ''
    let stderr = ''
    let settled = false

    const finish = (response: BridgeResponse<T>): void => {
      if (settled) {
        return
      }

      settled = true
      resolve(response)
    }

    const timeout = setTimeout(() => {
      child.kill()
      finish({
        ok: false,
        error: 'backend_timeout',
        message: 'The local Sigil backend did not respond in time.'
      })
    }, ['provider_snapshot', 'asset_catalog_refresh'].includes(request.command) ? 45_000 : 5_000)

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')

    child.stdout.on('data', (chunk: string) => {
      stdout += chunk
    })

    child.stderr.on('data', (chunk: string) => {
      stderr += chunk
    })

    child.on('error', () => {
      clearTimeout(timeout)
      finish({
        ok: false,
        error: 'backend_unavailable',
        message: 'The local Sigil backend could not be started.'
      })
    })

    child.on('close', () => {
      clearTimeout(timeout)

      try {
        const parsed = JSON.parse(stdout) as BridgeResponse<T>

        if (parsed && typeof parsed === 'object' && 'ok' in parsed) {
          finish(parsed)

          return
        }
      } catch {
        // Fail closed below.
      }

      finish({
        ok: false,
        error: 'invalid_backend_response',
        message:
          stderr.trim() ||
          'The local Sigil backend returned an invalid response.'
      })
    })

    child.stdin.end(`${JSON.stringify(request)}\n`)
  })
}

export function readBackendStatus(): Promise<BackendResponse> {
  return runBridgeRequest<BackendStatus>({ command: 'health' })
}

let governedUpdater: UpdaterController | null = null

async function installReadiness(): Promise<Readonly<{ ready: boolean; reason?: string }>> {
  const response = await runBridgeRequest<{
    automation?: { state?: string }
    runtime_health?: string
  }>({ command: 'runtime_snapshot' })

  if (!response.ok) {
    return { ready: false, reason: 'The protected paper runtime could not be verified.' }
  }

  if (response.result.automation?.state === 'running') {
    return { ready: false, reason: 'Pause the governed paper cycle before installing.' }
  }

  if (['locked', 'recovery_required', 'corrupt'].includes(response.result.runtime_health ?? '')) {
    return { ready: false, reason: 'Resolve the protected runtime state before installing.' }
  }

  return { ready: true }
}

async function initializeUpdater(): Promise<GovernedUpdater> {
  const { autoUpdater } = await import('electron-updater')
  const developmentEnabled = process.env.SIGIL_ENABLE_DEV_UPDATES === '1'
  const internalTest = process.env.SIGIL_INTERNAL_UPDATE_CHANNEL === '1'

  const updater = new GovernedUpdater({
    client: autoUpdater,
    policy: {
      packaged: app.isPackaged,
      developmentEnabled,
      internalTest,
      currentVersion: app.getVersion()
    },
    auditPath: path.join(app.getPath('userData'), 'updater', 'audit.jsonl'),
    getWindows: () => BrowserWindow.getAllWindows(),
    installReady: installReadiness
  })

  governedUpdater = updater

  return updater
}

export function registerSigilIpc(): void {
  ipcMain.removeHandler(SIGIL_BACKEND_STATUS_CHANNEL)
  ipcMain.removeHandler(SIGIL_EXPLAIN_PROPOSAL_CHANNEL)
  ipcMain.removeHandler(SIGIL_RUNTIME_SNAPSHOT_CHANNEL)
  ipcMain.removeHandler(SIGIL_PAPER_CYCLE_CONTROL_CHANNEL)
  ipcMain.removeHandler(SIGIL_PAPER_AUTHORIZATION_CONTROL_CHANNEL)
  ipcMain.removeHandler(SIGIL_PAPER_RUNTIME_RESET_CHANNEL)
  ipcMain.removeHandler(SIGIL_PROVIDER_SNAPSHOT_CHANNEL)
  ipcMain.removeHandler(SIGIL_MARKET_UNIVERSE_STATUS_CHANNEL)
  ipcMain.removeHandler(SIGIL_MARKET_UNIVERSE_SEARCH_CHANNEL)
  ipcMain.removeHandler(SIGIL_ASSET_CATALOG_STATUS_CHANNEL)
  ipcMain.removeHandler(SIGIL_ASSET_CATALOG_REFRESH_CHANNEL)
  ipcMain.removeHandler(SIGIL_RESEARCH_UNIVERSE_STATUS_CHANNEL)
  ipcMain.removeHandler(SIGIL_UPDATE_CHECK_CHANNEL)
  ipcMain.removeHandler(SIGIL_UPDATER_SNAPSHOT_CHANNEL)
  ipcMain.removeHandler(SIGIL_UPDATE_DOWNLOAD_CHANNEL)
  ipcMain.removeHandler(SIGIL_UPDATE_DEFER_CHANNEL)
  ipcMain.removeHandler(SIGIL_UPDATE_INSTALL_CHANNEL)
  ipcMain.removeHandler(SIGIL_RELEASE_CERTIFICATION_CHANNEL)

  ipcMain.handle(SIGIL_BACKEND_STATUS_CHANNEL, () => readBackendStatus())

  ipcMain.handle(
    SIGIL_EXPLAIN_PROPOSAL_CHANNEL,
    (_event, payload: Readonly<Record<string, unknown>>) =>
      runBridgeRequest({
        command: 'explain_proposal',
        payload
      })
  )

  ipcMain.handle(SIGIL_RUNTIME_SNAPSHOT_CHANNEL, () =>
    runBridgeRequest({ command: 'runtime_snapshot' })
  )
  ipcMain.handle(
    SIGIL_PAPER_CYCLE_CONTROL_CHANNEL,
    (_event, action: 'start' | 'pause' | 'stop') =>
      runBridgeRequest({ command: 'control_paper_cycle', payload: { action } })
  )
  ipcMain.handle(
    SIGIL_PAPER_AUTHORIZATION_CONTROL_CHANNEL,
    (_event, action: 'grant' | 'revoke') =>
      runBridgeRequest({
        command: 'control_paper_authorization',
        payload: { action }
      })
  )
  ipcMain.handle(SIGIL_PAPER_RUNTIME_RESET_CHANNEL, () =>
    runBridgeRequest({
      command: 'reset_paper_runtime',
      payload: { confirmation: 'RESET LOCAL PAPER PORTFOLIO' }
    })
  )
  ipcMain.handle(SIGIL_PROVIDER_SNAPSHOT_CHANNEL, () =>
    runBridgeRequest({ command: 'provider_snapshot' })
  )
  ipcMain.handle(SIGIL_MARKET_UNIVERSE_STATUS_CHANNEL, () =>
    runBridgeRequest({ command: 'market_universe_status' })
  )
  ipcMain.handle(
    SIGIL_MARKET_UNIVERSE_SEARCH_CHANNEL,
    (_event, payload: Readonly<Record<string, unknown>>) =>
      runBridgeRequest({ command: 'market_universe_search', payload })
  )
  ipcMain.handle(SIGIL_ALPACA_MARKET_DATA_STATUS_CHANNEL, () =>
    runBridgeRequest({ command: 'alpaca_market_data_status' })
  )
  ipcMain.handle(
    SIGIL_ALPACA_MARKET_DATA_CONTROL_CHANNEL,
    (_event, action: string) =>
      runBridgeRequest({ command: 'control_alpaca_market_data', payload: { action } })
  )
  ipcMain.handle(SIGIL_ASSET_CATALOG_STATUS_CHANNEL, () =>
    runBridgeRequest({ command: 'asset_catalog_status' })
  )
  ipcMain.handle(SIGIL_ASSET_CATALOG_REFRESH_CHANNEL, () =>
    runBridgeRequest({ command: 'asset_catalog_refresh' })
  )
  ipcMain.handle(SIGIL_RESEARCH_UNIVERSE_STATUS_CHANNEL, () =>
    runBridgeRequest({ command: 'research_universe_status' })
  )
  ipcMain.handle(SIGIL_UPDATER_SNAPSHOT_CHANNEL, () => governedUpdater?.getSnapshot())
  ipcMain.handle(SIGIL_UPDATE_CHECK_CHANNEL, () => governedUpdater?.check())
  ipcMain.handle(SIGIL_UPDATE_DOWNLOAD_CHANNEL, () => governedUpdater?.approveDownload())
  ipcMain.handle(SIGIL_UPDATE_DEFER_CHANNEL, () => governedUpdater?.defer())
  ipcMain.handle(SIGIL_UPDATE_INSTALL_CHANNEL, () => governedUpdater?.restartAndInstall())
  ipcMain.handle(
    SIGIL_RELEASE_CERTIFICATION_CHANNEL,
    (_event, payload: Readonly<Record<string, unknown>>) =>
      certificationResponse(payload)
  )
}

export function createSigilWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 900,
    minHeight: 720,
    title: SIGIL_APP_NAME,
    backgroundColor: '#0d0d0e',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(currentDirectory, 'electron-preload.cjs')
    }
  })

  window.once('ready-to-show', () => window.show())
  const developmentServer = process.env.SIGIL_DEV_SERVER

  if (developmentServer) {
    void window.loadURL(developmentServer)
  } else {
    void window.loadFile(path.join(currentDirectory, 'index.html'))
  }

  return window
}

app.whenReady().then(async () => {
  let updater: UpdaterController

  try {
    updater = await initializeUpdater()
  } catch (error) {
    updater = new UnavailableUpdater(app.getVersion(), error)
    governedUpdater = updater
  }

  registerSigilIpc()
  createSigilWindow()

  if (app.isPackaged && updater instanceof GovernedUpdater) {
    setTimeout(() => {
      void updater.check()
    }, 8_000)
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createSigilWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
