import { useCallback, useEffect, useRef, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { cn } from '@/lib/utils'

import type {
  PrimeCertificationStatus,
  PrimeFleetNode,
  PrimeFleetStatus,
  PrimeSigilRouteResult
} from './types'

const REFRESH_INTERVAL_MS = 20_000

// Only "advisory_financial_sentiment" is currently routable end-to-end (it
// targets Titan, a fleet node distinct from Sigil's own Mac host). The other
// four SUPPORTED_SIGIL_OPERATIONS route to "mac" -- the same node Sigil runs
// on -- which hermes_cli.prime.sigil_contract's self-address invariant
// correctly rejects. They're still offered here (Prime's real rejection
// reason is itself governed-routing visibility, not a UI-hidden failure),
// but sentiment is the one proven live.
const ROUTABLE_OPERATIONS = [
  'advisory_financial_sentiment',
  'advisory_valuation',
  'advisory_risk_assessment',
  'advisory_portfolio_construction',
  'advisory_research_summary'
] as const

type RoutableOperation = (typeof ROUTABLE_OPERATIONS)[number]

function connectionTone(state: PrimeFleetNode['connection_state']): 'default' | 'destructive' | 'muted' | 'warn' {
  switch (state) {
    case 'connected':
      return 'default'

    case 'degraded':

    case 'stale':
      return 'warn'

    case 'disconnected':

    case 'revoked':
      return 'destructive'

    default:
      return 'muted'
  }
}

function certificationTone(status: PrimeCertificationStatus['status']): 'default' | 'destructive' | 'muted' | 'warn' {
  switch (status) {
    case 'certified':
      return 'default'

    case 'blocked':

    case 'not_certified':
      return 'warn'

    case 'expired':
      return 'destructive'

    default:
      return 'muted'
  }
}

function relativeTime(epochSeconds: number | null): string {
  if (epochSeconds === null) {return 'never'}
  const deltaSeconds = Math.max(0, Math.floor(Date.now() / 1000) - epochSeconds)

  if (deltaSeconds < 5) {return 'just now'}

  if (deltaSeconds < 60) {return `${deltaSeconds}s ago`}

  if (deltaSeconds < 3600) {return `${Math.floor(deltaSeconds / 60)}m ago`}

  return `${Math.floor(deltaSeconds / 3600)}h ago`
}

export function PrimeFleetPanel(): React.JSX.Element {
  const [status, setStatus] = useState<PrimeFleetStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [operation, setOperation] = useState<RoutableOperation>('advisory_financial_sentiment')
  const [routing, setRouting] = useState(false)
  const [routeResult, setRouteResult] = useState<PrimeSigilRouteResult | null>(null)
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    const desktop = window.sigilDesktop

    if (!desktop?.getPrimeFleetStatus) {
      setLoadError('Prime fleet visibility is unavailable in this build.')
      setLoading(false)

      return
    }

    try {
      const response = await desktop.getPrimeFleetStatus()

      if (!mountedRef.current) {return}

      if (response.ok) {
        setStatus(response.result)
        setLoadError(null)
      } else {
        setLoadError(response.message || 'Prime fleet status request failed.')
      }
    } catch (error) {
      if (!mountedRef.current) {return}
      setLoadError(error instanceof Error ? error.message : 'Prime fleet status request failed.')
    } finally {
      if (mountedRef.current) {setLoading(false)}
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    void load()
    const interval = window.setInterval(() => void load(), REFRESH_INTERVAL_MS)

    return () => {
      mountedRef.current = false
      window.clearInterval(interval)
    }
  }, [load])

  const runRoute = useCallback(async () => {
    const desktop = window.sigilDesktop

    if (!desktop?.primeSigilRoute) {return}
    setRouting(true)
    setRouteResult(null)

    try {
      const response = await desktop.primeSigilRoute({
        operation,
        input_payload: { probe: 'mission-control-manual-test' }
      })

      if (response.ok) {
        setRouteResult(response.result)
      } else {
        setRouteResult({ ok: false, error: 'bridge_error', message: response.message })
      }
    } catch (error) {
      setRouteResult({
        ok: false,
        error: 'bridge_exception',
        message: error instanceof Error ? error.message : 'Route request failed.'
      })
    } finally {
      setRouting(false)
    }
  }, [operation])

  return (
    <section aria-labelledby="prime-fleet-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium" id="prime-fleet-heading">
          Hermes Fleet
        </h3>
        <div className="flex flex-wrap items-center gap-1.5" role="status">
          <Badge variant="outline">Paper Only</Badge>
          <Badge variant="outline">Broker Submission Disabled</Badge>
          <Badge variant="outline">Execution Authority Disabled</Badge>
          <Badge variant="outline">Prime Governed</Badge>
        </div>
      </div>

      <div aria-live="polite">
        {loading && !status ? (
          <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
            <Loader className="size-4" type="rose-orbit" />
            <span>Loading fleet status…</span>
          </div>
        ) : loadError ? (
          <ErrorBanner>{loadError}</ErrorBanner>
        ) : !status?.configured ? (
          <EmptyState
            description="Set HERMES_PRIME_BASE_URL and HERMES_PRIME_AUTH_TOKEN to connect Sigil to a governed Prime control plane."
            title="Prime not configured"
          />
        ) : !status.reachable ? (
          <ErrorBanner>Prime is configured at {status.base_url} but did not respond.</ErrorBanner>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">Certification:</span>
              <Badge variant={certificationTone(status.certification.status)}>
                {status.certification.status.replace('_', ' ')}
              </Badge>
              {status.certification.evidence_ref && (
                <span className="truncate font-mono text-[0.65rem] text-muted-foreground">
                  {status.certification.evidence_ref}
                </span>
              )}
            </div>

            {status.nodes.length === 0 ? (
              <EmptyState description="No fleet nodes are registered with Prime yet." title="No nodes registered" />
            ) : (
              <ul className="flex flex-col gap-2" data-testid="prime-fleet-node-list">
                {status.nodes.map(node => (
                  <li
                    className="flex flex-col gap-1 rounded-md border border-(--ui-stroke-secondary) px-3 py-2"
                    key={node.natural_key}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{node.natural_key}</span>
                      <Badge variant={connectionTone(node.connection_state)}>{node.connection_state}</Badge>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.7rem] text-muted-foreground">
                      <span>role: {node.role}</span>
                      <span>last seen: {relativeTime(node.last_seen_at)}</span>
                      <span>
                        models:{' '}
                        {node.model_inventory.length > 0 ? node.model_inventory.join(', ') : 'none reported'}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            <div className="flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) px-3 py-2">
              <label className="text-xs font-medium" htmlFor="prime-route-operation">
                Governed routing test
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  className="rounded-md border border-(--ui-stroke-secondary) bg-transparent px-2 py-1 text-xs"
                  disabled={routing}
                  id="prime-route-operation"
                  onChange={event => setOperation(event.target.value as RoutableOperation)}
                  value={operation}
                >
                  {ROUTABLE_OPERATIONS.map(op => (
                    <option key={op} value={op}>
                      {op}
                    </option>
                  ))}
                </select>
                <Button disabled={routing} onClick={() => void runRoute()} size="sm" type="button">
                  {routing ? 'Routing…' : 'Send harmless test request'}
                </Button>
              </div>
              {routeResult && (
                <div
                  aria-live="polite"
                  className={cn(
                    'rounded-md px-2 py-1.5 text-[0.7rem]',
                    routeResult.ok ? 'bg-primary/10 text-primary' : 'bg-destructive/10 text-destructive'
                  )}
                >
                  {routeResult.ok ? (
                    <>
                      Accepted — routed to <strong>{routeResult.advisory_output?.routed_to}</strong> via alias{' '}
                      <strong>{routeResult.advisory_output?.model_alias}</strong>
                    </>
                  ) : (
                    <>Rejected — {routeResult.rejection_code || routeResult.error || 'unknown reason'}</>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
