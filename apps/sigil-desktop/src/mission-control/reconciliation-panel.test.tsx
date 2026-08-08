import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ReconciliationPanel } from './reconciliation-panel'
import type { ExecutionReceipt, Proposal, SigilSnapshot } from './types'

function receipt(overrides: Partial<ExecutionReceipt> = {}): ExecutionReceipt {
  return {
    id: 'RCT-1',
    orderId: 'ORD-1',
    proposalId: 'PRP-1',
    symbol: 'MSFT',
    side: 'BUY',
    quantity: '0.10',
    price: '417.50',
    notional: '41.75',
    brokerStatus: 'Simulated acknowledgement',
    state: 'simulated',
    duplicatePrevention: 'Envelope consumed once',
    reconciliationRequired: false,
    reconciliationReference: 'REC-1',
    timestamp: '2026-07-25T14:18:03Z',
    ...overrides
  }
}

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 'PRP-1',
    symbol: 'MSFT',
    side: 'BUY',
    quantity: 0.1,
    estimatedNotional: '$41.75',
    strategy: 'sigil-liquid-trend',
    status: 'approved',
    evidenceReferences: ['EVD-1'],
    riskResults: [],
    ...overrides
  }
}

function buildSnapshot(overrides: Partial<SigilSnapshot> = {}): SigilSnapshot {
  return {
    dataState: 'ready',
    lastUpdated: '2026-08-07T12:00:00Z',
    environment: 'paper',
    simulation: true,
    brokerConnection: 'connected',
    maskedAccountId: 'PAPER LOCAL',
    systemHealth: 'Runtime healthy',
    cash: '$9,969.75',
    portfolioValue: '$24.09',
    activeStrategies: 1,
    pendingApprovals: 0,
    killSwitch: 'armed',
    certificationStatus: 'Paper runtime',
    maximumLaunchNotional: 'Dynamic paper allocation',
    firstLaunchLimit: '5% buying power / 10% position',
    launchState: 'suspended',
    stages: [],
    proposals: [],
    receipts: [],
    auditEvents: [],
    ...overrides
  }
}

function renderPanel(snapshot: SigilSnapshot, onOpenAudit = vi.fn(), onRefresh = vi.fn()) {
  render(<ReconciliationPanel onOpenAudit={onOpenAudit} onRefresh={onRefresh} snapshot={snapshot} />)

  return { onOpenAudit, onRefresh }
}

describe('Reconciliation panel', () => {
  it('shows a loading state while the snapshot is loading', () => {
    renderPanel(buildSnapshot({ dataState: 'loading' }))

    expect(screen.getByRole('status')).toBeTruthy()
    expect(screen.queryByTestId('reconciliation-panel')).toBeFalsy()
  })

  it('shows an empty state when there is nothing to reconcile', () => {
    renderPanel(buildSnapshot({ proposals: [], receipts: [] }))

    expect(screen.getByText('Nothing to reconcile')).toBeTruthy()
  })

  it('classifies a clean receipt as reconciled and counts it in the summary', () => {
    renderPanel(
      buildSnapshot({
        receipts: [receipt({ id: 'RCT-1', reconciliationRequired: false, reconciliationReference: 'REC-1' })]
      })
    )

    expect(screen.getByRole('cell', { name: 'Reconciled' })).toBeTruthy()
    const summary = screen.getByTestId('reconciliation-summary')

    expect(summary.textContent).toContain('1')
  })

  it('classifies a required receipt as a mismatch needing attention', () => {
    renderPanel(
      buildSnapshot({
        receipts: [
          receipt({
            id: 'RCT-2',
            orderId: 'ORD-2',
            reconciliationRequired: true,
            reconciliationReference: 'REC-2',
            duplicatePrevention: 'Retry blocked pending reconciliation'
          })
        ]
      })
    )

    expect(screen.getByRole('cell', { name: 'Required' })).toBeTruthy()
    expect(screen.getByText('Retry blocked pending reconciliation')).toBeTruthy()
  })

  it('flags a receipt with no matching reconciliation record as awaiting, not fabricating a clean status', () => {
    renderPanel(
      buildSnapshot({
        receipts: [
          receipt({
            id: 'RCT-3',
            orderId: 'ORD-3',
            reconciliationRequired: false,
            reconciliationReference: 'PAPER-RUNTIME:ORD-3'
          })
        ]
      })
    )

    expect(screen.getByRole('cell', { name: 'Awaiting record' })).toBeTruthy()
  })

  it('surfaces an approved proposal with no recorded execution as a genuine gap', () => {
    renderPanel(
      buildSnapshot({
        proposals: [proposal({ id: 'PRP-9', status: 'approved' })],
        receipts: []
      })
    )

    expect(screen.getByText('Approved, no recorded execution yet')).toBeTruthy()
    expect(screen.getByText('PRP-9')).toBeTruthy()
    expect(screen.getByText('No reconciliation record yet')).toBeTruthy()
  })

  it('does not treat a pending or rejected proposal as an execution gap', () => {
    renderPanel(
      buildSnapshot({
        proposals: [proposal({ id: 'PRP-9', status: 'pending' })],
        receipts: []
      })
    )

    expect(screen.getByText('Nothing to reconcile')).toBeTruthy()
  })

  it('does not fabricate a gap when the adapter proposalId join misses but evidence matches', () => {
    // Regression guard: desktop-adapter.ts's proposalId heuristic only strips
    // a "PAPER-ORD-" prefix and falls back to '-' for "PAPER-PROD-ORD-*"
    // order ids, so proposalId alone under-matches real backend data. Both
    // sides genuinely share the evidence identity, so that must still close
    // the gap and must not be reported as awaiting.
    renderPanel(
      buildSnapshot({
        proposals: [proposal({ id: 'PRP-9', status: 'approved', evidenceReferences: ['EVD-SHARED'] })],
        receipts: [
          receipt({
            id: 'RCT-9',
            proposalId: '—',
            reconciliationReference: 'EVD-SHARED',
            reconciliationRequired: false
          })
        ]
      })
    )

    expect(screen.queryByText('Approved, no recorded execution yet')).toBeFalsy()
    expect(screen.getByText('EVD-SHARED')).toBeTruthy()
  })

  it('shows a stale-data banner when the connection is not ready', () => {
    renderPanel(
      buildSnapshot({
        dataState: 'stale',
        receipts: [receipt()]
      })
    )

    expect(screen.getByText(/may be stale/)).toBeTruthy()
  })

  it('filters rows by symbol, order id, and status', () => {
    renderPanel(
      buildSnapshot({
        receipts: [
          receipt({ id: 'RCT-1', symbol: 'MSFT', orderId: 'ORD-1' }),
          receipt({ id: 'RCT-2', symbol: 'NVDA', orderId: 'ORD-2' })
        ]
      })
    )

    expect(screen.getByText('MSFT')).toBeTruthy()
    expect(screen.getByText('NVDA')).toBeTruthy()

    fireEvent.change(screen.getByPlaceholderText('Filter by symbol, order, or status'), {
      target: { value: 'nvda' }
    })

    expect(screen.queryByText('MSFT')).toBeFalsy()
    expect(screen.getByText('NVDA')).toBeTruthy()
  })

  it('shows a no-matches state when the filter excludes everything', () => {
    renderPanel(buildSnapshot({ receipts: [receipt({ symbol: 'MSFT' })] }))

    fireEvent.change(screen.getByPlaceholderText('Filter by symbol, order, or status'), {
      target: { value: 'zzzz' }
    })

    expect(screen.getByText('No matching records')).toBeTruthy()
  })

  it('opens the audit trail from an evidence reference', () => {
    const { onOpenAudit } = renderPanel(
      buildSnapshot({ receipts: [receipt({ reconciliationReference: 'REC-42' })] })
    )

    fireEvent.click(screen.getByText('REC-42'))

    expect(onOpenAudit).toHaveBeenCalledTimes(1)
  })

  it('calls onRefresh from the refresh button', () => {
    const { onRefresh } = renderPanel(buildSnapshot({ receipts: [receipt()] }))

    fireEvent.click(screen.getByRole('button', { name: /refresh/i }))

    expect(onRefresh).toHaveBeenCalledTimes(1)
  })
})
