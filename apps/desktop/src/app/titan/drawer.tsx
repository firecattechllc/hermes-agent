import { useStore } from '@nanostores/react'
import { type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

import { CompactMarkdown } from '@/components/chat/compact-markdown'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Textarea } from '@/components/ui/textarea'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { setPaneWidthOverride } from '@/store/panes'

import { deliverTitanMessage, isProhibitedTitanRequest, refreshTitan, sendTitanMessage } from './client'
import {
  $titanConnection,
  $titanDrawerWidth,
  $titanMessages,
  $titanMode,
  $titanQueuedCount,
  $titanStatus,
  appendTitanMessage,
  newTitanConversation,
  setTitanDrawerOpen,
  shouldSendTitanKey,
  type TitanConnectionState,
  type TitanMessage
} from './store'

const MIN_WIDTH = 300
const MAX_WIDTH = 640

function formatTime(timestamp: number | null): string {
  if (!timestamp) {
    return ''
  }

  return new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' }).format(timestamp * 1000)
}

function formatDuration(seconds: number | null, fallback: string): string {
  if (seconds === null) {
    return fallback
  }

  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3_600)

  return days > 0 ? `${days}d ${hours}h` : `${hours}h`
}

function stateDot(state: TitanConnectionState): string {
  if (state === 'online') {
    return 'bg-(--ui-success)'
  }

  if (state === 'connecting') {
    return 'bg-(--ui-accent)'
  }

  if (state === 'degraded') {
    return 'bg-(--ui-warning)'
  }

  if (state === 'unauthorized') {
    return 'bg-(--ui-danger)'
  }

  return 'bg-(--ui-text-quaternary)'
}

function TitanChatHeader() {
  const { t } = useI18n()
  const connection = useStore($titanConnection)
  const status = useStore($titanStatus)

  return (
    <header className="flex shrink-0 items-start gap-2 border-b border-(--ui-stroke-tertiary) px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h2 className="truncate text-sm font-semibold">{t.titan.title}</h2>
          <span aria-hidden="true" className={cn('size-2 shrink-0 rounded-full', stateDot(connection))} />
          <span className="text-[0.65rem] text-(--ui-text-tertiary)">{t.titan.states[connection]}</span>
        </div>
        <p className="text-[0.68rem] text-(--ui-text-tertiary)">{t.titan.subtitle}</p>
        <div className="mt-1 flex flex-wrap gap-x-3 text-[0.62rem] text-(--ui-text-quaternary)">
          <span>
            {t.titan.activeModel}: {status.activeModel ?? t.titan.notAvailable}
          </span>
          <span>
            {t.titan.lastContact}: {status.lastContact ? formatTime(status.lastContact) : t.titan.notAvailable}
          </span>
        </div>
      </div>
      <Tip label={t.titan.openMissionControl}>
        <Button
          aria-label={t.titan.openMissionControl}
          onClick={() => {
            const next = $titanDrawerWidth.get() < MAX_WIDTH ? MAX_WIDTH : 380
            $titanDrawerWidth.set(next)
            setPaneWidthOverride('titan-chat', next)
          }}
          size="icon-titlebar"
          variant="ghost"
        >
          <Codicon name="dashboard" />
        </Button>
      </Tip>
      <Tip label={t.titan.newConversation}>
        <Button
          aria-label={t.titan.newConversation}
          onClick={newTitanConversation}
          size="icon-titlebar"
          variant="ghost"
        >
          <Codicon name="add" />
        </Button>
      </Tip>
      <Tip label={t.common.collapse}>
        <Button
          aria-label={t.common.collapse}
          onClick={() => setTitanDrawerOpen(false)}
          size="icon-titlebar"
          variant="ghost"
        >
          <Codicon name="close" />
        </Button>
      </Tip>
    </header>
  )
}

function TitanMessageRow({ message }: { message: TitanMessage }) {
  const { t } = useI18n()
  const [details, setDetails] = useState(false)

  const label =
    message.author === 'mac' ? 'Mac Hermes · Big Sister' : message.author === 'titan' ? t.titan.title : 'Specialist'

  return (
    <article
      className={cn(
        'group/message px-3 py-2.5',
        message.author === 'event' && 'border-y border-(--ui-stroke-tertiary) bg-(--ui-bg-quaternary)'
      )}
    >
      <div className="mb-1 flex items-center gap-2 text-[0.62rem] text-(--ui-text-quaternary)">
        <span className="font-semibold text-(--ui-text-secondary)">{label}</span>
        <time dateTime={new Date(message.createdAt * 1000).toISOString()}>{formatTime(message.createdAt)}</time>
        <button
          className="ml-auto hover:text-(--ui-text-primary)"
          onClick={() => setDetails(value => !value)}
          type="button"
        >
          {t.titan.states[message.deliveryState]}
        </button>
      </div>
      <CompactMarkdown className="text-(--ui-text-secondary)" text={message.content} />
      {details && (
        <div className="mt-2 space-y-1 text-[0.62rem] text-(--ui-text-quaternary)">
          <div className="break-all">
            {t.titan.correlationId}: {message.correlationId}
          </div>
          {message.errorCode && <div>{message.errorCode}</div>}
          <div className="flex gap-2">
            <Button
              onClick={() => void window.hermesDesktop.writeClipboard(message.content)}
              size="micro"
              variant="text"
            >
              {t.titan.copy}
            </Button>
            {message.deliveryState === 'failed' && (
              <Button onClick={() => void deliverTitanMessage(message)} size="micro" variant="text">
                {t.titan.retry}
              </Button>
            )}
          </div>
        </div>
      )}
    </article>
  )
}

function TitanMessageList() {
  const { t } = useI18n()
  const messages = useStore($titanMessages)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => endRef.current?.scrollIntoView({ block: 'end' }), [messages.length])

  return (
    <div aria-live="polite" className="min-h-0 flex-1 overflow-y-auto" role="log">
      {messages.length === 0 ? (
        <div className="grid h-full place-items-center px-6 text-center text-xs text-(--ui-text-quaternary)">
          {t.titan.empty}
        </div>
      ) : (
        messages.map(message => <TitanMessageRow key={message.id} message={message} />)
      )}
      <div ref={endRef} />
    </div>
  )
}

function TitanMissionControlSummary() {
  const { t } = useI18n()
  const status = useStore($titanStatus)

  const rows = [
    [t.titan.overview.uptime, formatDuration(status.uptimeSeconds, t.titan.notAvailable)],
    [t.titan.overview.queueDepth, String(status.queueDepth)],
    [t.titan.overview.nursery, status.nurseryState],
    [t.titan.overview.ollama, status.ollamaHealth],
    [t.titan.overview.finbert, status.finbertHealth],
    [t.titan.overview.memoryIndex, status.memoryIndexHealth],
    [t.titan.overview.pendingEscalations, String(status.pendingEscalations)],
    [
      t.titan.overview.latestEvidence,
      status.evidenceTimestamp ? formatTime(status.evidenceTimestamp) : t.titan.notAvailable
    ]
  ]

  return (
    <section
      aria-label={t.titan.openMissionControl}
      className="grid shrink-0 grid-cols-2 gap-x-4 gap-y-1 border-b border-(--ui-stroke-tertiary) px-3 py-2"
    >
      {rows.map(([label, value]) => (
        <div className="flex min-w-0 justify-between gap-2 text-[0.62rem]" key={label}>
          <span className="truncate text-(--ui-text-quaternary)">{label}</span>
          <span className="truncate text-(--ui-text-secondary)">{value}</span>
        </div>
      ))}
    </section>
  )
}

function TitanMessageComposer() {
  const { t } = useI18n()
  const connection = useStore($titanConnection)
  const mode = useStore($titanMode)
  const queued = useStore($titanQueuedCount)
  const [value, setValue] = useState('')
  const [title, setTitle] = useState('')
  const [priority, setPriority] = useState('normal')
  const [deadline, setDeadline] = useState('')
  const [risk, setRisk] = useState('bounded')
  const [expectedOutput, setExpectedOutput] = useState('governed report')
  const [workspace, setWorkspace] = useState('')
  const [evidence, setEvidence] = useState('tests\nartifact references')
  const [validation, setValidation] = useState('')
  const [references, setReferences] = useState('')
  const [version, setVersion] = useState('1')

  const fullDraft = [
    title,
    value,
    priority,
    deadline,
    risk,
    expectedOutput,
    workspace,
    evidence,
    validation,
    references
  ].join('\n')

  const prohibited = isProhibitedTitanRequest(fullDraft)
  const valid = value.trim() && (mode === 'chat' || title.trim())

  const submit = async () => {
    if (!valid || prohibited || connection === 'unauthorized' || connection === 'connecting') {
      return
    }

    const content = mode === 'chat' ? value : `${title.trim()}\n\n${value.trim()}`

    const payload =
      mode === 'task'
        ? {
            title: title.trim(),
            instructions: value.trim(),
            priority: priority.trim(),
            deadline: deadline.trim() || null,
            risk_classification: risk.trim(),
            expected_output: expectedOutput.trim(),
            workspace_scope: workspace.trim(),
            evidence_requirements: evidence
              .split('\n')
              .map(item => item.trim())
              .filter(Boolean)
          }
        : mode === 'lesson'
          ? {
              lesson_title: title.trim(),
              objective: expectedOutput.trim(),
              instructions: value.trim(),
              allowed_workspace: workspace.trim(),
              validation_criteria: validation
                .split('\n')
                .map(item => item.trim())
                .filter(Boolean),
              source_references: references
                .split('\n')
                .map(item => item.trim())
                .filter(Boolean),
              lesson_version: version.trim()
            }
          : undefined

    setValue('')
    setTitle('')
    await sendTitanMessage(content, mode, payload)
  }

  const quick = async (prompt: string, operation?: 'status' | 'queue' | 'latestReport') => {
    if (operation) {
      const result = await window.hermesDesktop.titan.request({ operation })

      if (result.ok) {
        appendTitanMessage({
          id: `event-${crypto.randomUUID().replaceAll('-', '')}`,
          correlationId: `corr-${crypto.randomUUID().replaceAll('-', '')}`,
          conversationId: 'titan-operational',
          author: 'event',
          content: `**${prompt}**\n\n\`\`\`json\n${JSON.stringify(result.data, null, 2)}\n\`\`\``,
          createdAt: Math.floor(Date.now() / 1000),
          deliveryState: 'delivered',
          mode: 'chat',
          evidenceReferences: []
        })
      }

      return
    }

    await sendTitanMessage(prompt, 'chat')
  }

  const options = [
    { id: 'chat' as const, label: t.titan.modes.chat },
    { id: 'task' as const, label: t.titan.modes.task },
    { id: 'lesson' as const, label: t.titan.modes.lesson }
  ]

  return (
    <footer className="shrink-0 border-t border-(--ui-stroke-tertiary) p-2.5">
      <div className="mb-2 flex gap-1 overflow-x-auto pb-0.5">
        {[
          [t.titan.quick.status, 'Titan governed status', 'status'],
          [t.titan.quick.work, 'Summarize your current governed work.'],
          [t.titan.quick.report, 'Latest governed report', 'latestReport'],
          [t.titan.quick.queue, 'Titan governed queue', 'queue'],
          [t.titan.quick.escalations, 'Summarize pending governed escalations.']
        ].map(([label, prompt, operation]) => (
          <Button
            disabled={connection === 'unauthorized' || connection === 'connecting'}
            key={label}
            onClick={() => void quick(prompt, operation as 'status' | 'queue' | 'latestReport' | undefined)}
            size="micro"
            variant="outline"
          >
            {label}
          </Button>
        ))}
      </div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <SegmentedControl onChange={$titanMode.set} options={options} value={mode} />
        {queued > 0 && <span className="text-[0.62rem] text-(--ui-text-tertiary)">{queued} queued</span>}
      </div>
      {mode !== 'chat' && (
        <div className="mb-2 grid max-h-48 grid-cols-2 gap-2 overflow-y-auto pr-1">
          <label className="col-span-2 text-[0.62rem] text-(--ui-text-tertiary)">
            {t.titan.fields.title}
            <Input onChange={event => setTitle(event.target.value)} size="sm" value={title} />
          </label>
          {mode === 'task' ? (
            <>
              <label className="text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.priority}
                <Input onChange={event => setPriority(event.target.value)} size="sm" value={priority} />
              </label>
              <label className="text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.deadline}
                <Input
                  onChange={event => setDeadline(event.target.value)}
                  size="sm"
                  type="datetime-local"
                  value={deadline}
                />
              </label>
              <label className="text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.risk}
                <Input onChange={event => setRisk(event.target.value)} size="sm" value={risk} />
              </label>
              <label className="text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.expectedOutput}
                <Input onChange={event => setExpectedOutput(event.target.value)} size="sm" value={expectedOutput} />
              </label>
              <label className="col-span-2 text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.evidence}
                <Input onChange={event => setEvidence(event.target.value)} size="sm" value={evidence} />
              </label>
            </>
          ) : (
            <>
              <label className="text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.objective}
                <Input onChange={event => setExpectedOutput(event.target.value)} size="sm" value={expectedOutput} />
              </label>
              <label className="text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.version}
                <Input onChange={event => setVersion(event.target.value)} size="sm" value={version} />
              </label>
              <label className="col-span-2 text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.validation}
                <Input onChange={event => setValidation(event.target.value)} size="sm" value={validation} />
              </label>
              <label className="col-span-2 text-[0.62rem] text-(--ui-text-tertiary)">
                {t.titan.fields.references}
                <Input onChange={event => setReferences(event.target.value)} size="sm" value={references} />
              </label>
            </>
          )}
          <label className="col-span-2 text-[0.62rem] text-(--ui-text-tertiary)">
            {t.titan.fields.workspace}
            <Input onChange={event => setWorkspace(event.target.value)} size="sm" value={workspace} />
          </label>
        </div>
      )}
      <Textarea
        aria-label={t.titan.placeholder}
        className="max-h-40 min-h-20 resize-none"
        onChange={event => setValue(event.target.value)}
        onKeyDown={event => {
          if (shouldSendTitanKey(event.key, event.shiftKey)) {
            event.preventDefault()
            void submit()
          }
        }}
        placeholder={mode === 'chat' ? t.titan.placeholder : t.titan.fields.instructions}
        value={value}
      />
      {prohibited && <p className="mt-1 text-[0.65rem] text-(--ui-danger)">{t.titan.prohibited}</p>}
      <div className="mt-2 flex justify-end">
        <Button
          aria-label={t.titan.send}
          disabled={!valid || prohibited || connection === 'unauthorized' || connection === 'connecting'}
          onClick={() => void submit()}
          size="sm"
        >
          {connection === 'offline' ? t.titan.states.queued : t.common.send}
        </Button>
      </div>
    </footer>
  )
}

export function TitanChatDrawer() {
  const { t } = useI18n()
  const width = useStore($titanDrawerWidth)

  useEffect(() => {
    setPaneWidthOverride('titan-chat', $titanDrawerWidth.get())
    void refreshTitan()
    const timer = window.setInterval(() => void refreshTitan(), 15_000)

    return () => window.clearInterval(timer)
  }, [])

  const beginResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const startX = event.clientX
    const startWidth = $titanDrawerWidth.get()
    const target = event.currentTarget
    target.setPointerCapture(event.pointerId)

    const move = (moveEvent: PointerEvent) => {
      const next = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, startWidth + startX - moveEvent.clientX))
      $titanDrawerWidth.set(next)
      setPaneWidthOverride('titan-chat', next)
    }

    const up = () => {
      target.removeEventListener('pointermove', move)
      target.removeEventListener('pointerup', up)
    }

    target.addEventListener('pointermove', move)
    target.addEventListener('pointerup', up)
  }

  return (
    <aside
      aria-label={`${t.titan.title} — ${t.titan.subtitle}`}
      className="relative flex h-full min-h-0 w-full flex-col overflow-hidden bg-(--ui-sidebar-surface-background)"
    >
      <div
        aria-label="Resize Titan Hermes drawer"
        className="absolute inset-y-0 left-0 z-10 w-1 cursor-col-resize"
        onPointerDown={beginResize}
        role="separator"
      />
      <TitanChatHeader />
      {width >= 600 && <TitanMissionControlSummary />}
      <TitanMessageList />
      <TitanMessageComposer />
    </aside>
  )
}
