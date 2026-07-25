import { atom, computed } from 'nanostores'

import { PANE_TOGGLE_REVEAL_EVENT } from '@/components/pane-shell'
import { setTreePaneHidden } from '@/components/pane-shell/tree/store'
import { Codecs, persistentAtom } from '@/lib/persisted'

export type TitanConnectionState = 'online' | 'offline' | 'connecting' | 'degraded' | 'unauthorized'
export type TitanComposerMode = 'chat' | 'task' | 'lesson'
export type TitanDeliveryState = 'sending' | 'queued' | 'delivered' | 'failed' | 'rejected'
export type TitanAuthor = 'mac' | 'titan' | 'specialist' | 'event'

export interface TitanMessage {
  id: string
  correlationId: string
  conversationId: string
  author: TitanAuthor
  content: string
  createdAt: number
  deliveryState: TitanDeliveryState
  mode: TitanComposerMode
  evidenceReferences: string[]
  payload?: Record<string, unknown>
  errorCode?: string
}

export interface TitanStatus {
  activeModel: string | null
  lastContact: number | null
  presence: string
  degradedComponents: string[]
  uptimeSeconds: number | null
  queueDepth: number
  nurseryState: string
  ollamaHealth: string
  finbertHealth: string
  memoryIndexHealth: string
  pendingEscalations: number
  evidenceTimestamp: number | null
}

const sanitizeMessages = (value: unknown): TitanMessage[] => {
  if (!Array.isArray(value)) {
    return []
  }

  return value
    .filter((item): item is TitanMessage => Boolean(item && typeof item === 'object'))
    .filter(item => typeof item.id === 'string' && typeof item.content === 'string')
    .slice(-500)
}

export const $titanDrawerOpen = persistentAtom('hermes.desktop.titan.drawerOpen.v1', false, Codecs.bool)
export const $titanDrawerWidth = persistentAtom(
  'hermes.desktop.titan.drawerWidth.v1',
  380,
  Codecs.json<number>(value => (typeof value === 'number' && value >= 300 && value <= 640 ? value : 380))
)
export const $titanConversationId = persistentAtom(
  'hermes.desktop.titan.conversationId.v1',
  `titan-${crypto.randomUUID()}`,
  Codecs.text
)
export const $titanMessages = persistentAtom(
  'hermes.desktop.titan.messages.v1',
  [] as TitanMessage[],
  Codecs.json(sanitizeMessages)
)
export const $titanMode = persistentAtom('hermes.desktop.titan.mode.v1', 'chat' as TitanComposerMode, {
  decode: raw => (raw === 'task' || raw === 'lesson' ? raw : 'chat'),
  encode: value => value
})
export const $titanConnection = atom<TitanConnectionState>('connecting')
export const $titanStatus = atom<TitanStatus>({
  activeModel: null,
  lastContact: null,
  presence: 'unknown',
  degradedComponents: [],
  uptimeSeconds: null,
  queueDepth: 0,
  nurseryState: 'unknown',
  ollamaHealth: 'unknown',
  finbertHealth: 'unknown',
  memoryIndexHealth: 'unknown',
  pendingEscalations: 0,
  evidenceTimestamp: null
})
export const $titanUnread = atom(0)
export const $titanQueuedCount = computed(
  $titanMessages,
  messages => messages.filter(message => message.author === 'mac' && message.deliveryState === 'queued').length
)

export function newTitanConversation() {
  $titanConversationId.set(`titan-${crypto.randomUUID()}`)
  $titanMessages.set([])
  $titanUnread.set(0)
}

export function appendTitanMessage(message: TitanMessage) {
  const existing = $titanMessages.get()
  const index = existing.findIndex(item => item.id === message.id)

  if (index >= 0) {
    const next = existing.slice()
    next[index] = message
    $titanMessages.set(next)
  } else {
    $titanMessages.set([...existing, message].slice(-500))

    if (message.author !== 'mac' && !$titanDrawerOpen.get()) {
      $titanUnread.set($titanUnread.get() + 1)
    }
  }
}

export function updateTitanMessage(id: string, patch: Partial<TitanMessage>) {
  $titanMessages.set($titanMessages.get().map(message => (message.id === id ? { ...message, ...patch } : message)))
}

export function setTitanDrawerOpen(open: boolean) {
  $titanDrawerOpen.set(open)
  setTreePaneHidden('titan-chat', !open)

  if (open) {
    $titanUnread.set(0)
  }

  // Collapsible panes leave the layout grid at narrow window widths. Wait for
  // the hidden-pane bridge to render its overlay contribution, then pin or
  // dismiss that overlay using the pane shell's governed reveal contract.
  if (typeof window !== 'undefined') {
    window.requestAnimationFrame(() => {
      window.dispatchEvent(
        new CustomEvent(PANE_TOGGLE_REVEAL_EVENT, {
          detail: { id: 'titan-chat', mode: open ? 'open' : 'close' }
        })
      )
    })
  }
}

export function shouldSendTitanKey(key: string, shiftKey: boolean): boolean {
  return key === 'Enter' && !shiftKey
}
