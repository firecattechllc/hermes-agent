import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MockSigilOperatorAdapter } from './mock-adapter'

import { SigilOperatorView } from './index'

describe('SigilOperatorView', () => {
  it('is paper, simulated, disconnected, masked, and has no live submission control', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter('ready')} />)

    expect(await screen.findByRole('heading', { name: 'Sigil' })).toBeTruthy()
    expect(screen.getAllByText('Paper only').length).toBeGreaterThan(0)
    expect(screen.getByText('disconnected')).toBeTruthy()
    expect(screen.getByText('•••• 23F4')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Executions' }))
    expect(screen.getByText('Simulated executions')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /submit/i })).toBeNull()
    expect(screen.getByRole('status').textContent).toContain('No broker submission')
  })

  it('keeps manual proposal actions locked when governed paper runtime controls are active', async () => {
    const adapter = new MockSigilOperatorAdapter('ready')
    const apply = vi.spyOn(adapter, 'applySimulatedAction')
    render(<SigilOperatorView adapter={adapter} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Proposals' }))

    const approveButtons = screen.getAllByRole('button', { name: 'Approve' })
    const rejectButtons = screen.getAllByRole('button', { name: 'Reject' })

    expect(approveButtons.every(button => button.hasAttribute('disabled'))).toBe(true)
    expect(rejectButtons.every(button => button.hasAttribute('disabled'))).toBe(true)
    expect(screen.queryByRole('button', { name: 'Enable simulated operator actions' })).toBeNull()
    expect(screen.queryByText('Confirm simulated approval')).toBeNull()
    expect(apply).not.toHaveBeenCalled()
  })

  it.each(['empty', 'disconnected', 'stale'] as const)('renders the %s snapshot state', async dataState => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter(dataState)} />)
    expect(await screen.findByTestId('sigil-operator')).toBeTruthy()
  })

  it('renders a recoverable error state', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter('error')} />)
    expect(await screen.findByText('Sigil snapshot unavailable')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry local snapshot' })).toBeTruthy()
  })
})
