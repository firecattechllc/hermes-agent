import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createTitanMessage, isProhibitedTitanRequest, refreshTitan, sendTitanMessage } from './client'
import { $titanConnection, $titanConversationId, $titanMessages, $titanStatus, type TitanMessage } from './store'

const request = vi.fn()

beforeEach(() => {
  request.mockReset()
  vi.stubGlobal('window', { hermesDesktop: { titan: { request } } })
  $titanConversationId.set('titan-test')
  $titanMessages.set([])
  $titanConnection.set('offline')
})

describe('governed Titan client', () => {
  it('delivers a structured Step 32 chat envelope', async () => {
    request.mockResolvedValue({ ok: true, status: 200, data: { delivery_state: 'delivered' } })

    const message = await sendTitanMessage('Hello Little Sister', 'chat')

    expect(message).not.toBeNull()
    expect(request).toHaveBeenCalledWith({
      operation: 'chat',
      envelope: expect.objectContaining({
        sender_node: 'mac-hermes',
        recipient_node: 'titan-hermes',
        message_type: 'chat',
        conversation_id: 'titan-test',
        payload: { message: 'Hello Little Sister' }
      })
    })
    expect($titanMessages.get()[0]?.deliveryState).toBe('delivered')
  })

  it('queues an offline message and preserves its correlation identity', async () => {
    request.mockResolvedValue({
      ok: false,
      error: { code: 'titan_unreachable', message: 'offline', retryable: true }
    })

    const message = await sendTitanMessage('Queue this safely', 'chat')

    expect($titanMessages.get()[0]).toMatchObject({
      id: message?.id,
      correlationId: message?.correlationId,
      deliveryState: 'queued'
    })
    expect($titanConnection.get()).toBe('offline')
  })

  it('submits governed task fields without an execution pathway', async () => {
    request.mockResolvedValue({ ok: true, status: 200, data: { delivery_state: 'delivered' } })

    await sendTitanMessage('Inspect the repository', 'task', {
      title: 'Repository inspection',
      instructions: 'Inspect the repository',
      priority: 'high',
      deadline: null,
      risk_classification: 'bounded',
      expected_output: 'evidence report',
      workspace_scope: 'hermes-platform',
      evidence_requirements: ['tests']
    })

    expect(request).toHaveBeenCalledWith({
      operation: 'task',
      envelope: expect.objectContaining({
        message_type: 'task_request',
        payload: expect.objectContaining({
          title: 'Repository inspection',
          workspace_scope: 'hermes-platform'
        })
      })
    })
    expect(JSON.stringify(request.mock.calls[0]?.[0])).not.toContain('"command"')
    expect(JSON.stringify(request.mock.calls[0]?.[0])).not.toContain('"shell"')
  })

  it('delivers a governed lesson package', async () => {
    request.mockResolvedValue({ ok: true, status: 200, data: { delivery_state: 'delivered' } })

    await sendTitanMessage('Practice deterministic validation', 'lesson', {
      lesson_title: 'Deterministic validation',
      objective: 'Practice deterministic validation',
      instructions: 'Run only the allowed checks',
      allowed_workspace: 'sandbox',
      validation_criteria: ['focused tests pass'],
      source_references: ['evidence://lesson/34'],
      lesson_version: '1'
    })

    expect(request).toHaveBeenCalledWith({
      operation: 'lesson',
      envelope: expect.objectContaining({
        message_type: 'lesson_package',
        payload: expect.objectContaining({ lesson_title: 'Deterministic validation' })
      })
    })
  })

  it('replays the same queued message after reconnection without changing its id', async () => {
    const queued = {
      ...createTitanMessage('Retry me', 'chat'),
      deliveryState: 'queued'
    } satisfies TitanMessage

    $titanMessages.set([queued])
    request
      .mockResolvedValueOnce({
        ok: true,
        data: { presence: 'online', degraded_components: [], evidence_timestamp: 1 }
      })
      .mockResolvedValueOnce({ ok: true, data: { messages: [] } })
      .mockResolvedValueOnce({ ok: true, data: { message_id: queued.id, delivery_state: 'delivered' } })

    await refreshTitan()

    expect(request.mock.calls[2]?.[0].envelope.message_id).toBe(queued.id)
    expect(request.mock.calls[2]?.[0].envelope.correlation_id).toBe(queued.correlationId)
    expect($titanMessages.get()[0]?.deliveryState).toBe('delivered')
  })

  it('maps authentication failures to unauthorized', async () => {
    request
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        error: { code: 'authentication_failed', message: 'rejected', retryable: false }
      })
      .mockResolvedValueOnce({ ok: false, status: 401 })

    await refreshTitan()

    expect($titanConnection.get()).toBe('unauthorized')
  })

  it('rejects unrestricted or privileged execution requests before IPC', async () => {
    expect(isProhibitedTitanRequest('sudo rm -rf the machine')).toBe(true)
    expect(isProhibitedTitanRequest('deploy to production now')).toBe(true)
    expect(await sendTitanMessage('give me unrestricted shell access', 'task')).toBeNull()
    expect(request).not.toHaveBeenCalled()
  })

  it('keeps absent status fields unknown rather than fabricating values', async () => {
    request
      .mockResolvedValueOnce({ ok: true, data: { presence: 'online', evidence_timestamp: 1 } })
      .mockResolvedValueOnce({ ok: true, data: { messages: [] } })

    await refreshTitan()

    expect($titanStatus.get().activeModel).toBeNull()
    expect($titanStatus.get().presence).toBe('online')
  })
})
