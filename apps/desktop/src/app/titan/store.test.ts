import { beforeEach, describe, expect, it } from 'vitest'

import { $hiddenTreePanes } from '@/components/pane-shell/tree/store'

import {
  $titanDrawerOpen,
  $titanDrawerWidth,
  $titanMode,
  $titanUnread,
  appendTitanMessage,
  setTitanDrawerOpen,
  shouldSendTitanKey
} from './store'

beforeEach(() => {
  window.localStorage.clear()
  $titanDrawerOpen.set(false)
  $titanDrawerWidth.set(380)
  $titanUnread.set(0)
})

describe('Titan drawer presentation state', () => {
  it('opens and closes without touching main session state', () => {
    setTitanDrawerOpen(true)
    expect($titanDrawerOpen.get()).toBe(true)
    expect($hiddenTreePanes.get().has('titan-chat')).toBe(false)

    setTitanDrawerOpen(false)
    expect($titanDrawerOpen.get()).toBe(false)
    expect($hiddenTreePanes.get().has('titan-chat')).toBe(true)
  })

  it('persists a resized drawer width', () => {
    $titanDrawerWidth.set(512)

    expect(window.localStorage.getItem('hermes.desktop.titan.drawerWidth.v1')).toBe('512')
  })

  it('persists Chat, Task, and Lesson mode selection', () => {
    for (const mode of ['task', 'lesson', 'chat'] as const) {
      $titanMode.set(mode)
      expect(window.localStorage.getItem('hermes.desktop.titan.mode.v1')).toBe(mode)
    }
  })

  it('uses Return to send and Shift+Return for a newline', () => {
    expect(shouldSendTitanKey('Enter', false)).toBe(true)
    expect(shouldSendTitanKey('Enter', true)).toBe(false)
  })

  it('tracks unread Titan messages only while collapsed', () => {
    appendTitanMessage({
      id: 'link-incoming',
      correlationId: 'corr-incoming',
      conversationId: 'titan-test',
      author: 'titan',
      content: 'Acknowledged',
      createdAt: 1,
      deliveryState: 'delivered',
      mode: 'chat',
      evidenceReferences: []
    })

    expect($titanUnread.get()).toBe(1)
    setTitanDrawerOpen(true)
    expect($titanUnread.get()).toBe(0)
  })
})
