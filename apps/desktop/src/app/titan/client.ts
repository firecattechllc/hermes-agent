import {
  $titanConnection,
  $titanConversationId,
  $titanMessages,
  $titanStatus,
  appendTitanMessage,
  type TitanComposerMode,
  type TitanMessage,
  updateTitanMessage
} from './store'

const PROHIBITED_REQUEST =
  /\b(sudo|root access|deploy(?:ment)? to production|publish externally|spend(?:ing)?|secret access|api key|private key|unrestricted shell|rm\s+-rf|financial execution|send external (?:message|email))\b/i

const identifier = (prefix: string) => `${prefix}-${crypto.randomUUID().replaceAll('-', '')}`

function envelopeFor(message: TitanMessage): Record<string, unknown> {
  const messageType = message.mode === 'chat' ? 'chat' : message.mode === 'task' ? 'task_request' : 'lesson_package'

  const payload =
    message.payload ??
    (message.mode === 'chat'
      ? { message: message.content }
      : message.mode === 'task'
        ? {
            title: message.content.split('\n', 1)[0].slice(0, 160),
            instructions: message.content,
            priority: 'normal',
            risk_classification: 'bounded',
            expected_output: 'governed report',
            workspace_scope: 'operator-selected workspace',
            evidence_requirements: ['tests', 'artifact references']
          }
        : {
            lesson_title: message.content.split('\n', 1)[0].slice(0, 160),
            objective: message.content,
            instructions: message.content,
            allowed_workspace: 'operator-selected workspace',
            validation_criteria: ['structured acknowledgement', 'completion evidence'],
            lesson_version: 1
          })

  return {
    message_id: message.id,
    correlation_id: message.correlationId,
    conversation_id: message.conversationId,
    sender_node: 'mac-hermes',
    recipient_node: 'titan-hermes',
    message_type: messageType,
    priority: 'normal',
    created_at: message.createdAt,
    payload,
    confidence: null,
    evidence_references: [],
    artifact_references: [],
    approval_required: false,
    approval_state: null,
    delivery_state: 'queued',
    retry: {
      attempt_count: 0,
      maximum_attempts: 3,
      next_attempt_at: null,
      last_attempt_at: null,
      last_error_code: null
    },
    schema_version: 1
  }
}

function operationFor(mode: TitanComposerMode): 'chat' | 'task' | 'lesson' {
  return mode
}

export function createTitanMessage(
  content: string,
  mode: TitanComposerMode,
  payload?: Record<string, unknown>
): TitanMessage {
  return {
    id: identifier('link'),
    correlationId: identifier('corr'),
    conversationId: $titanConversationId.get(),
    author: 'mac',
    content: content.trim(),
    createdAt: Math.floor(Date.now() / 1000),
    deliveryState: 'sending',
    mode,
    evidenceReferences: [],
    payload
  }
}

export function isProhibitedTitanRequest(content: string): boolean {
  return PROHIBITED_REQUEST.test(content)
}

export async function deliverTitanMessage(message: TitanMessage): Promise<void> {
  const result = await window.hermesDesktop.titan.request({
    operation: operationFor(message.mode),
    envelope: envelopeFor(message)
  })

  if (result.ok) {
    updateTitanMessage(message.id, { deliveryState: 'delivered', errorCode: undefined })
    $titanConnection.set('online')

    return
  }

  const code = result.error?.code ?? 'unknown_error'
  const unauthorized = result.status === 401 || result.status === 403 || code === 'authentication_failed'
  updateTitanMessage(message.id, {
    deliveryState: result.error?.retryable ? 'queued' : unauthorized ? 'rejected' : 'failed',
    errorCode: code
  })
  $titanConnection.set(unauthorized ? 'unauthorized' : result.error?.retryable ? 'offline' : 'degraded')
}

export async function sendTitanMessage(
  content: string,
  mode: TitanComposerMode,
  payload?: Record<string, unknown>
): Promise<TitanMessage | null> {
  if (!content.trim() || isProhibitedTitanRequest(`${content}\n${JSON.stringify(payload ?? {})}`)) {
    return null
  }

  const message = createTitanMessage(content, mode, payload)
  appendTitanMessage(message)
  await deliverTitanMessage(message)

  return message
}

export async function refreshTitan(): Promise<void> {
  $titanConnection.set('connecting')

  const [statusResult, queueResult] = await Promise.all([
    window.hermesDesktop.titan.request({ operation: 'status' }),
    window.hermesDesktop.titan.request({ operation: 'queue' })
  ])

  if (!statusResult.ok) {
    const unauthorized = statusResult.status === 401 || statusResult.status === 403
    $titanConnection.set(unauthorized ? 'unauthorized' : statusResult.error?.retryable ? 'offline' : 'degraded')

    return
  }

  const status =
    statusResult.data && typeof statusResult.data === 'object' ? (statusResult.data as Record<string, unknown>) : {}

  const degraded = Array.isArray(status.degraded_components)
    ? status.degraded_components.filter((item): item is string => typeof item === 'string')
    : []

  $titanStatus.set({
    activeModel: typeof status.active_local_model === 'string' ? status.active_local_model : null,
    lastContact: Math.floor(Date.now() / 1000),
    presence: typeof status.presence === 'string' ? status.presence : 'unknown',
    degradedComponents: degraded,
    uptimeSeconds: typeof status.uptime_seconds === 'number' ? status.uptime_seconds : null,
    queueDepth:
      status.queue_counts && typeof status.queue_counts === 'object'
        ? Object.values(status.queue_counts as Record<string, unknown>).reduce<number>(
            (total, count) => total + (typeof count === 'number' ? count : 0),
            0
          )
        : 0,
    nurseryState: typeof status.nursery_state === 'string' ? status.nursery_state : 'unknown',
    ollamaHealth: typeof status.ollama_health === 'string' ? status.ollama_health : 'unknown',
    finbertHealth: typeof status.finbert_health === 'string' ? status.finbert_health : 'unknown',
    memoryIndexHealth: typeof status.memory_index_health === 'string' ? status.memory_index_health : 'unknown',
    pendingEscalations: typeof status.pending_escalations === 'number' ? status.pending_escalations : 0,
    evidenceTimestamp: typeof status.evidence_timestamp === 'number' ? status.evidence_timestamp : null
  })
  $titanConnection.set(degraded.length > 0 ? 'degraded' : 'online')

  if (queueResult.ok && queueResult.data && typeof queueResult.data === 'object') {
    const messages = (queueResult.data as { messages?: unknown }).messages

    if (Array.isArray(messages)) {
      for (const raw of messages) {
        if (!raw || typeof raw !== 'object') {
          continue
        }

        const envelope = raw as Record<string, unknown>

        if (
          envelope.sender_node !== 'titan-hermes' ||
          envelope.recipient_node !== 'mac-hermes' ||
          typeof envelope.message_id !== 'string'
        ) {
          continue
        }

        const payload =
          envelope.payload && typeof envelope.payload === 'object' ? (envelope.payload as Record<string, unknown>) : {}

        const content =
          typeof payload.message === 'string'
            ? payload.message
            : typeof payload.summary === 'string'
              ? payload.summary
              : JSON.stringify(payload, null, 2)

        const correlationId =
          typeof envelope.correlation_id === 'string' ? envelope.correlation_id : envelope.message_id

        const correlatedMode = $titanMessages
          .get()
          .find(message => message.author === 'mac' && message.correlationId === correlationId)?.mode

        appendTitanMessage({
          id: envelope.message_id,
          correlationId,
          conversationId:
            typeof envelope.conversation_id === 'string' ? envelope.conversation_id : $titanConversationId.get(),
          author: envelope.message_type === 'escalation' ? 'specialist' : 'titan',
          content,
          createdAt: typeof envelope.created_at === 'number' ? envelope.created_at : Math.floor(Date.now() / 1000),
          deliveryState: 'delivered',
          mode:
            envelope.message_type === 'task_result'
              ? 'task'
              : envelope.message_type === 'lesson_package' || correlatedMode === 'lesson'
                ? 'lesson'
                : (correlatedMode ?? 'chat'),
          evidenceReferences: Array.isArray(envelope.evidence_references)
            ? envelope.evidence_references.filter((item): item is string => typeof item === 'string')
            : []
        })
      }
    }
  }

  for (const queued of $titanMessages
    .get()
    .filter(message => message.author === 'mac' && message.deliveryState === 'queued')) {
    await deliverTitanMessage(queued)
  }
}
