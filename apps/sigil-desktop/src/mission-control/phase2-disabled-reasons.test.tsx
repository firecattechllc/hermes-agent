import { render, screen } from '@testing-library/react'

import { MockSigilOperatorAdapter } from './mock-adapter'

import { SigilOperatorView } from './index'

function disabledReason(control: HTMLButtonElement): string | null {
  const title = control.getAttribute('title')?.trim()

  if (title) {
    return title
  }

  const describedBy = control.getAttribute('aria-describedby')?.trim()

  if (!describedBy) {
    return null
  }

  return describedBy
    .split(/\s+/)
    .map(id => document.getElementById(id)?.textContent?.trim() ?? '')
    .filter(Boolean)
    .join(' ')
}

describe('Sigil Beta Phase 2 disabled-control acceptance', () => {
  it('gives every disabled button a specific operator-visible reason', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)

    await screen.findByTestId('sigil-operator')

    const disabledButtons = Array.from(
      document.querySelectorAll<HTMLButtonElement>('button:disabled')
    )

    expect(disabledButtons.length).toBeGreaterThan(0)

    const missingReasons = disabledButtons
      .filter(button => !disabledReason(button))
      .map(button => button.getAttribute('aria-label') ?? button.textContent?.trim() ?? '<unnamed>')
      .filter(Boolean)

    expect(missingReasons).toEqual([])
  })

  it('keeps broker submission absent from the rendered control surface', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)

    await screen.findByTestId('sigil-operator')

    expect(screen.queryByRole('button', { name: /submit order/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /broker submission/i })).toBeNull()
  })
})
