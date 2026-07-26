import { contextBridge, ipcRenderer } from 'electron'

const SIGIL_BACKEND_STATUS_CHANNEL = 'sigil:get-backend-status'
const SIGIL_EXPLAIN_PROPOSAL_CHANNEL = 'sigil:explain-proposal'
const SIGIL_RUNTIME_SNAPSHOT_CHANNEL = 'sigil:get-runtime-snapshot'
const SIGIL_PAPER_CYCLE_CONTROL_CHANNEL = 'sigil:control-paper-cycle'
const SIGIL_PROVIDER_SNAPSHOT_CHANNEL = 'sigil:get-provider-snapshot'

contextBridge.exposeInMainWorld('sigilDesktop', {
  productName: 'Sigil',
  persistenceNamespace: 'com.firecattechnology.sigil',
  brokerSubmissionAvailable: false,
  getBackendStatus: () => ipcRenderer.invoke(SIGIL_BACKEND_STATUS_CHANNEL),
  getRuntimeSnapshot: () => ipcRenderer.invoke(SIGIL_RUNTIME_SNAPSHOT_CHANNEL),
  controlPaperCycle: (action: 'start' | 'pause' | 'stop') =>
    ipcRenderer.invoke(SIGIL_PAPER_CYCLE_CONTROL_CHANNEL, action),
  getProviderSnapshot: () => ipcRenderer.invoke(SIGIL_PROVIDER_SNAPSHOT_CHANNEL),
  explainProposal: (payload: Readonly<Record<string, unknown>>) =>
    ipcRenderer.invoke(SIGIL_EXPLAIN_PROPOSAL_CHANNEL, payload)
})
