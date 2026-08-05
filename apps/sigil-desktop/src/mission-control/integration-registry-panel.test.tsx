import { render, screen } from '@testing-library/react'

import { type AIStatus, IntegrationRegistryPanel } from './index'

const emptyStatus: NonNullable<AIStatus['integration_registry']> = {
  enabled: false,
  state: 'disabled',
  store_health: 'empty',
  reason: null,
  schema_version: 1,
  registry_revision: `sha256:${'a'.repeat(64)}`,
  entry_count: 0,
  counts_by_lifecycle: {
    discovered: 0,
    under_review: 0,
    rejected: 0,
    sandbox_approved: 0,
    pilot: 0,
    certified: 0,
    deprecated: 0,
    quarantined: 0
  },
  counts_by_category: {},
  pinned_count: 0,
  unpinned_count: 0,
  valid_count: 0,
  invalid_count: 0,
  certified_count: 0,
  quarantined_count: 0,
  deprecated_count: 0,
  latest_lifecycle_evidence_identity: null,
  paper_only: true,
  broker_submission: false,
  activation_authorized: false,
  installation_authorized: false,
  approval_authority: false
}

describe('Integration Registry panel', () => {
  it('renders the actual disabled empty state without controls', () => {
    render(<IntegrationRegistryPanel status={emptyStatus} />)

    expect(screen.getByText('Integration Registry')).toBeTruthy()
    expect(screen.getByText('0 entries · 0 valid · 0 invalid')).toBeTruthy()
    expect(screen.getByText('0 pinned · 0 unpinned')).toBeTruthy()
    expect(screen.getByText('No tracked integration entries.')).toBeTruthy()
    expect(screen.getByText(/installation denied · activation denied/)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders lifecycle, pinning, certification, and quarantine summaries', () => {
    render(
      <IntegrationRegistryPanel
        status={{
          ...emptyStatus,
          enabled: true,
          state: 'healthy',
          entry_count: 3,
          valid_count: 3,
          pinned_count: 3,
          certified_count: 1,
          quarantined_count: 1,
          counts_by_lifecycle: {
            ...emptyStatus.counts_by_lifecycle,
            discovered: 1,
            certified: 1,
            quarantined: 1
          }
        }}
      />
    )

    expect(screen.getByText('3 entries · 3 valid · 0 invalid')).toBeTruthy()
    expect(screen.getByText('3 pinned · 0 unpinned')).toBeTruthy()
    expect(screen.getByText('1 certified · 1 quarantined · 0 deprecated')).toBeTruthy()
    expect(screen.getByText('discovered 1 · certified 1 · quarantined 1')).toBeTruthy()
  })

  it('renders a sanitized invalid reason and remains fail closed', () => {
    render(
      <IntegrationRegistryPanel
        status={{
          ...emptyStatus,
          state: 'invalid',
          store_health: 'invalid',
          invalid_count: 1,
          reason: 'registry storage failed integrity validation'
        }}
      />
    )

    expect(screen.getByText('registry storage failed integrity validation')).toBeTruthy()
    expect(screen.getByText('No activation authority')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
