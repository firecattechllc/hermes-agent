import { createServer, type Server } from 'node:http'

import { expect, test } from './test'
import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

let fixture: MockBackendFixture | null = null
let titanServer: Server | null = null
let titanUrl = ''
const token = 'e2e-titan-token'
let titanReplies: Record<string, unknown>[] = []

test.beforeAll(async () => {
  titanServer = createServer((request, response) => {
    response.setHeader('Content-Type', 'application/json')

    if (request.headers.authorization !== `Bearer ${token}`) {
      response.statusCode = 401
      response.end(JSON.stringify({ detail: { code: 'authentication_failed' } }))
      return
    }

    if (request.url === '/status') {
      response.end(
        JSON.stringify({
          node_id: 'titan-hermes',
          node_role: 'little_sister',
          presence: 'online',
          service_version: 'e2e',
          uptime_seconds: 34,
          queue_counts: {
            queued: 0,
            delivered: 0,
            acknowledged: 0,
            failed: 0,
            rejected: 0,
            retryable: 0,
            dead_lettered: 0
          },
          nursery_state: 'healthy',
          ollama_health: 'healthy',
          finbert_health: 'unknown',
          memory_index_health: 'healthy',
          last_synchronization_at: null,
          pending_escalations: 0,
          degraded_components: [],
          evidence_timestamp: Math.floor(Date.now() / 1000)
        })
      )
      return
    }

    if (request.url === '/queue') {
      response.end(JSON.stringify({ messages: titanReplies }))
      return
    }

    if (request.method === 'POST' && request.url === '/chat') {
      let body = ''
      request.setEncoding('utf8')
      request.on('data', chunk => {
        body += chunk
      })
      request.on('end', () => {
        const envelope = JSON.parse(body) as Record<string, unknown>
        titanReplies = [
          {
            ...envelope,
            message_id: 'link-reply-e2e',
            sender_node: 'titan-hermes',
            recipient_node: 'mac-hermes',
            payload: { message: 'Hello Mac from Titan' },
            delivery_state: 'queued'
          }
        ]
        response.end(JSON.stringify({ ...envelope, delivery_state: 'delivered' }))
      })
      return
    }

    response.statusCode = 404
    response.end(JSON.stringify({ detail: { code: 'not_found' } }))
  })

  await new Promise<void>(resolve => titanServer!.listen(0, '127.0.0.1', resolve))
  const address = titanServer.address()

  if (!address || typeof address === 'string') {
    throw new Error('Titan E2E server did not bind')
  }

  titanUrl = `http://127.0.0.1:${address.port}`
  fixture = await setupMockBackend({
    HERMES_DESKTOP_BOOT_FAKE: '1',
    HERMES_DESKTOP_BOOT_FAKE_STEP_MS: '10',
    HERMES_LINK_TITAN_URL: titanUrl,
    HERMES_LINK_TOKEN: token
  })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  await new Promise<void>(resolve => titanServer?.close(() => resolve()))
  fixture = null
  titanServer = null
})

test('opens, chats, switches modes, and closes without replacing the main session', async () => {
  const page = fixture!.page
  const mainComposer = page.locator('[contenteditable="true"]').first()
  await expect(mainComposer).toBeVisible()

  await page.getByRole('button', { name: 'Toggle Titan Hermes' }).click()
  await expect(page.getByRole('complementary', { name: 'Titan Hermes — Little Sister' })).toBeVisible()
  await expect(page.getByText('Online', { exact: true })).toBeVisible()

  const titanComposer = page.getByRole('textbox', { name: 'Message Titan Hermes…' })
  await titanComposer.fill('Hello Little Sister')
  await titanComposer.press('Enter')
  await expect(page.getByText('Hello Little Sister', { exact: true })).toBeVisible()
  await expect(page.getByText('Delivered', { exact: true })).toBeVisible()
  await expect(page.getByText('Hello Mac from Titan', { exact: true })).toHaveCount(1, {
    timeout: 20_000
  })

  await page.getByRole('button', { name: 'Task', exact: true }).click()
  await expect(page.getByText('Risk classification', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Lesson', exact: true }).click()
  await expect(page.getByText('Validation criteria', { exact: true })).toBeVisible()

  await expect(mainComposer).toBeVisible()
  await page.getByRole('button', { name: 'Collapse' }).click()
  await expect(page.getByRole('complementary', { name: 'Titan Hermes — Little Sister' })).toBeHidden()
  await expect(mainComposer).toBeVisible()
})
