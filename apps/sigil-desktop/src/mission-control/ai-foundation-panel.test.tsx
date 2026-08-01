import { render, screen } from '@testing-library/react'

import { AIFoundationPanel, type AIStatus } from './index'

const healthyStatus: AIStatus = {
  enabled: true,
  service_state: 'enabled',
  configured_model_count: 1,
  available_provider_count: 1,
  local_gemma_health: 'healthy',
  evidence_ledger_health: 'healthy',
  artifact_store_health: 'healthy',
  artifact_count: 1,
  last_successful_analysis_at: '2026-08-01T17:00:00Z',
  latest_analysis_summary: 'Sanitized research summary',
  last_failure_classification: null,
  paper_only: true,
  broker_submission: false
}

describe('AI Foundation status panel', () => {
  it('renders disabled and unavailable state without execution controls', () => {
    render(<AIFoundationPanel status={null} />)

    expect(screen.getByText('AI analysis is advisory only.')).toBeTruthy()
    expect(screen.getByText('No execution authority')).toBeTruthy()
    expect(screen.getAllByText('unavailable').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders healthy provider and sanitized artifact summary', () => {
    render(<AIFoundationPanel status={healthyStatus} />)

    expect(screen.getAllByText('healthy').length).toBeGreaterThan(0)
    expect(screen.getByText('1 models · 1 available')).toBeTruthy()
    expect(screen.getByText('Sanitized research summary')).toBeTruthy()
  })

  it('renders the latest structured failure classification', () => {
    render(<AIFoundationPanel status={{ ...healthyStatus, last_failure_classification: 'provider_unavailable' }} />)

    expect(screen.getByText('provider_unavailable')).toBeTruthy()
    expect(document.body.textContent).not.toContain('api_key')
  })
})
