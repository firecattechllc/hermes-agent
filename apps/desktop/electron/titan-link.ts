import { isIP } from 'node:net'

export type TitanLinkOperation = 'status' | 'queue' | 'chat' | 'task' | 'lesson' | 'latestReport'

export interface TitanLinkRequest {
  operation: TitanLinkOperation
  envelope?: Record<string, unknown>
}

export interface TitanLinkResult {
  ok: boolean
  status?: number
  data?: unknown
  error?: { code: string; message: string; retryable: boolean }
}

const ROUTES: Record<TitanLinkOperation, { method: 'GET' | 'POST'; path: string }> = {
  status: { method: 'GET', path: '/status' },
  queue: { method: 'GET', path: '/queue' },
  chat: { method: 'POST', path: '/chat' },
  task: { method: 'POST', path: '/task' },
  lesson: { method: 'POST', path: '/lesson' },
  latestReport: { method: 'GET', path: '/reports/latest' }
}

function privateTitanUrl(raw: string): URL {
  const url = new URL(raw)
  const host = url.hostname.toLowerCase()
  const ipKind = isIP(host)

  const privateIpv4 =
    ipKind === 4 &&
    (host.startsWith('10.') ||
      host.startsWith('192.168.') ||
      /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
      /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(host))

  const loopback = host === 'localhost' || host === '127.0.0.1' || host === '::1'
  const tailscaleDns = host.endsWith('.ts.net')

  if (!loopback && !privateIpv4 && !tailscaleDns) {
    throw new Error('Titan endpoint must be loopback, private-network, or Tailscale DNS')
  }

  if (!loopback && url.protocol !== 'https:') {
    throw new Error('Private Titan endpoints must use HTTPS')
  }

  if (loopback && url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('Titan endpoint must use HTTP or HTTPS')
  }

  url.pathname = url.pathname.replace(/\/+$/, '')
  url.search = ''
  url.hash = ''

  return url
}

function linkError(code: string, message: string, retryable: boolean, status?: number): TitanLinkResult {
  return { ok: false, status, error: { code, message, retryable } }
}

function validStructuredResponse(operation: TitanLinkOperation, data: unknown): boolean {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return false
  }

  const record = data as Record<string, unknown>

  if (operation === 'status') {
    return (
      typeof record.node_id === 'string' &&
      typeof record.node_role === 'string' &&
      typeof record.presence === 'string' &&
      typeof record.evidence_timestamp === 'number'
    )
  }

  if (operation === 'queue') {
    return Array.isArray(record.messages)
  }

  return typeof record.message_id === 'string' && typeof record.correlation_id === 'string'
}

export async function requestTitanLink(
  request: TitanLinkRequest,
  env: NodeJS.ProcessEnv = process.env,
  fetchImpl: typeof fetch = fetch
): Promise<TitanLinkResult> {
  const rawUrl = env.HERMES_LINK_TITAN_URL?.trim()
  const token = env.HERMES_LINK_TOKEN?.trim()

  if (!rawUrl) {
    return linkError('not_configured', 'Titan Hermes is not configured', false)
  }

  if (!token) {
    return linkError('authentication_unavailable', 'Titan authentication is not configured', false)
  }

  let baseUrl: URL

  try {
    baseUrl = privateTitanUrl(rawUrl)
  } catch {
    return linkError('invalid_private_endpoint', 'Titan endpoint is not an approved private address', false)
  }

  const route = ROUTES[request.operation]

  if (route.method === 'POST' && (!request.envelope || typeof request.envelope !== 'object')) {
    return linkError('invalid_envelope', 'A structured Step 32 envelope is required', false)
  }

  try {
    const requestUrl = new URL(baseUrl)
    requestUrl.pathname = `${baseUrl.pathname}${route.path}`.replace(/\/{2,}/g, '/')

    const response = await fetchImpl(requestUrl, {
      method: route.method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(route.method === 'POST' ? { 'Content-Type': 'application/json' } : {})
      },
      body: route.method === 'POST' ? JSON.stringify(request.envelope) : undefined,
      signal: AbortSignal.timeout(10_000)
    })

    const data = (await response.json()) as unknown

    if (!response.ok) {
      const detail =
        data && typeof data === 'object'
          ? ((data as { error?: unknown; detail?: unknown }).error ?? (data as { detail?: unknown }).detail ?? data)
          : null

      const structured = detail && typeof detail === 'object' ? (detail as Record<string, unknown>) : {}
      const code = typeof structured.code === 'string' ? structured.code : `http_${response.status}`
      const unauthorized = response.status === 401 || response.status === 403

      return linkError(
        code,
        unauthorized ? 'Titan authentication was rejected' : 'Titan rejected the governed request',
        response.status >= 500,
        response.status
      )
    }

    if (!validStructuredResponse(request.operation, data)) {
      return linkError('invalid_response', 'Titan returned an invalid structured response', false, response.status)
    }

    return { ok: true, status: response.status, data }
  } catch (error) {
    const timeout = error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError')

    return linkError(
      timeout ? 'titan_timeout' : 'titan_unreachable',
      timeout ? 'Titan did not respond before the request timeout' : 'Titan Hermes is offline or unreachable',
      true
    )
  }
}
