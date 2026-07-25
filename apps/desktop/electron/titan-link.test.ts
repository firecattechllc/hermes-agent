import assert from 'node:assert/strict'

import { test } from 'vitest'

import { requestTitanLink } from './titan-link'

test('rejects public Titan endpoints before transport', async () => {
  let called = false

  const result = await requestTitanLink(
    { operation: 'status' },
    { HERMES_LINK_TITAN_URL: 'https://example.com', HERMES_LINK_TOKEN: 'not-logged' },
    async () => {
      called = true

      return new Response('{}')
    }
  )

  assert.equal(result.error?.code, 'invalid_private_endpoint')
  assert.equal(called, false)
})

test('maps authentication failure without exposing response detail', async () => {
  const result = await requestTitanLink(
    { operation: 'status' },
    { HERMES_LINK_TITAN_URL: 'https://titan.tailnet.ts.net', HERMES_LINK_TOKEN: 'not-logged' },
    async () =>
      new Response(JSON.stringify({ detail: { code: 'authentication_failed', message: 'sensitive detail' } }), {
        status: 401
      })
  )

  assert.deepEqual(result.error, {
    code: 'authentication_failed',
    message: 'Titan authentication was rejected',
    retryable: false
  })
})

test('only sends a structured envelope to an allowlisted Step 32 route', async () => {
  let requested = ''

  const result = await requestTitanLink(
    { operation: 'chat', envelope: { message_id: 'link-1' } },
    { HERMES_LINK_TITAN_URL: 'http://127.0.0.1:9320', HERMES_LINK_TOKEN: 'not-logged' },
    async input => {
      requested = String(input)

      return new Response(
        JSON.stringify({ message_id: 'link-1', correlation_id: 'corr-1', delivery_state: 'delivered' }),
        { status: 200 }
      )
    }
  )

  assert.equal(result.ok, true)
  assert.equal(requested, 'http://127.0.0.1:9320/chat')
})

test('fails closed on a malformed successful response', async () => {
  const result = await requestTitanLink(
    { operation: 'status' },
    { HERMES_LINK_TITAN_URL: 'http://127.0.0.1:9320', HERMES_LINK_TOKEN: 'not-logged' },
    async () => new Response(JSON.stringify({ presence: 'online' }), { status: 200 })
  )

  assert.equal(result.ok, false)
  assert.equal(result.error?.code, 'invalid_response')
})
