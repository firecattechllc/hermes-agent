import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import { INITIAL_SIGIL_SNAPSHOT, MockSigilOperatorAdapter, SIGIL_FIRST_LAUNCH_LIMIT } from './mock-adapter'

import { SigilOperatorView } from './index'

describe('standalone Sigil Mission Control', () => {
  it('launches directly with Sigil-only product navigation and branding', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)

    expect(await screen.findByTestId('sigil-operator')).toBeTruthy()
    expect(screen.getByText('Mission control')).toBeTruthy()

    for (const label of ['Overview', 'Portfolio', 'Proposals', 'Launch', 'Executions', 'Reconciliation', 'Audit', 'Settings']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }

    for (const hermesArea of ['Chat', 'Projects', 'Messaging', 'Artifacts']) {
      expect(screen.queryByText(hermesArea)).toBeNull()
    }
  })

  it('preserves paper safety, masked identity, dynamic sizing, and reconciliation evidence', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter('disconnected')} />)

    expect(await screen.findByText('•••• 23F4')).toBeTruthy()
    expect(screen.getAllByText(SIGIL_FIRST_LAUNCH_LIMIT).length).toBeGreaterThan(0)
    expect(screen.getAllByText('DISCONNECTED').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Reconciliation' }))
    expect(screen.getByText('Required')).toBeTruthy()
    expect(screen.queryByText(/submit order/i)).toBeNull()
  })

  it('shows monthly paper authorization and never exposes submission capability', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)
    await screen.findByTestId('sigil-operator')

    expect(screen.getByText('Paper month 2026-07 · active')).toBeTruthy()
    expect(screen.getByText(/calendar month started automatically authorized/)).toBeTruthy()
    expect(screen.queryByText(/submit order/i)).toBeNull()
  })

  it('renders disconnected Hermes intelligence and explains the selected proposal locally', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)
    await screen.findByTestId('sigil-operator')

    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))

    expect(screen.getByText('Hermes Intelligence')).toBeTruthy()
    expect(screen.getByText('local-disconnected')).toBeTruthy()
    expect(screen.getByText('Analysis only')).toBeTruthy()
    expect(screen.getByText('Never')).toBeTruthy()
    expect(screen.getByText('Unavailable')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Explain selected proposal' }))

    const analysis = await screen.findByTestId('hermes-analysis')

    expect(analysis.textContent).toContain('Proposal PRP-20260725-0042 is governed and simulated.')
    expect(analysis.textContent).toContain('it cannot be submitted to a broker')
    expect(analysis.textContent).toContain('Execution authorized: no')
    expect(analysis.textContent).toContain('Broker submission available: no')
  })

  it('contains no broker submission method in the adapter contract', () => {
    expect('submitOrder' in new MockSigilOperatorAdapter()).toBe(false)
    expect(INITIAL_SIGIL_SNAPSHOT.environment).toBe('paper')
    expect(INITIAL_SIGIL_SNAPSHOT.simulation).toBe(true)
  })

  it('renders refreshable read-only provider data without credential fields', async () => {
    const original = window.sigilDesktop

    const alpacaStatus = {
      configured: true,
      authenticated: true,
      provider_state: 'degraded',
      generated_at: '2026-07-29T16:00:00Z',
      asset_catalog: {
        refresh_state: 'ready',
        source_count: 14148,
        accepted_count: 12984,
        excluded_count: 1164,
        conflict_count: 0,
        generated_at: '2026-07-29T16:00:00Z',
        age_seconds: 0,
        stale: false,
        last_error: null
      },
      delayed_sip: {
        enabled: false,
        classification: '15-minute delayed SIP',
        expected_delay_minutes: 15,
        latest_scan_started_at: null,
        latest_scan_completed_at: null,
        universe_total: 0,
        scanned_count: 0,
        successful_count: 0,
        missing_count: 0,
        rejected_count: 0,
        stale_count: 0,
        current_batch: 0,
        total_batches: 0,
        next_scan_at: null,
        provider_state: 'idle'
      },
      live_iex: {
        enabled: false,
        classification: 'live partial-market IEX',
        partial_market: true,
        connected: false,
        active_symbol_count: 0,
        maximum_symbol_count: 30,
        subscribed_symbols: [],
        last_message_at: null,
        reconnect_attempts: 0,
        stale: true,
        provider_state: 'idle'
      },
      safety: {
        broker_submission_available: false,
        execution_authorized: false,
        live_trading_enabled: false,
        data_only_mode: true
      }
    }

    window.sigilDesktop = {
      productName: 'Sigil',
      persistenceNamespace: 'test',
      brokerSubmissionAvailable: false,
      getBackendStatus: async () => ({
        ok: false,
        error: 'unused',
        message: 'unused'
      }),
      explainProposal: async () => ({
        ok: false,
        error: 'unused',
        message: 'unused'
      }),
      getProviderSnapshot: async () => ({
        ok: true,
        result: {
          checked_at: '2026-07-26T16:00:00Z',
          broker_submission_available: false,
          credentials_exposed: false,
          alpaca: {
            status: 'connected',
            message: 'Read-only market data is current.',
            universe: {
              scope: '12 explicitly defined U.S.-listed demonstration equities',
              total: 12,
              available: 1,
              unavailable: 11,
              catalog_source: 'Sigil bounded demonstration universe',
              catalog_freshness: 'Static local definition; provider catalog unverified',
              iex_status: 'real-time',
              broader_us_status: '15-minute delayed historical data available; catalog unverified',
              criteria: 'Latest IEX quote availability',
              whole_market_coverage: false,
              catalog_access: 'unavailable_current_credentials',
              coverage_limitation: 'Full U.S. listing enumeration is unavailable with current credentials.',
              refresh_policy: 'One batched read-only request every 30 seconds.'
            },
            symbols: [
              {
                symbol: 'MSFT',
                name: 'Microsoft Corporation',
                sector: 'Information Technology',
                price: '452.80',
                observed_at: '2026-07-26T15:59:00Z',
                daily_change_percent: '1.20',
                screen_status: 'available',
                source: 'Alpaca IEX snapshot'
              }
            ]
          },
          public: {
            status: 'connected',
            message: 'Read-only account access is current.',
            accounts: [
              {
                masked_account_id: '•••• 1234',
                cash: '1250.00',
                portfolio_value: '1500.00',
                positions: [{ symbol: 'AAPL', quantity: '2' }]
              }
            ]
          }
        }
      }),
      getAlpacaMarketDataStatus: async () => ({ ok: true, result: alpacaStatus }),
      controlAlpacaMarketData: async () => ({ ok: true, result: alpacaStatus }),
      getMarketUniverseStatus: async () => ({
        ok: true,
        result: {
          schema_version: 1,
          policy_version: 'sigil-market-universe-v1',
          snapshot_id: 'abc123',
          generated_at: '2026-07-27T00:00:00Z',
          source_record_count: 3,
          active_count: 3,
          master_count: 3,
          broker_tradable_count: 2,
          actively_researched_count: 1,
          proposal_eligible_count: 2,
          fractionable_count: 1,
          conflicted_count: 0,
          excluded_count: 1,
          target_minimum: 0,
          target_maximum: 0,
          target_capacity_validated: true,
          catalog_source: 'Alpaca Paper Trading Assets API',
          catalog_scope: 'Full Alpaca asset catalog discovered',
          capacity_certification: 'actual provider counts; no expected total',
          coverage_limitation: 'Market-data coverage is partial under IEX.',
          cache_state: 'fresh',
          cache_age_seconds: 12,
          integrity: 'verified',
          exchange_counts: { NASDAQ: 3 },
          exclusion_reason_counts: { not_tradable: 1 },
          broker_submission_available: false,
          execution_authorized: false
        }
      }),
      searchMarketUniverse: async payload => ({
        ok: true,
        result: {
          query: String(payload.query ?? ''),
          universe: String(payload.universe ?? 'master'),
          total: 1,
          offset: 0,
          limit: 50,
          has_more: false,
          results: [{
            instrument_id: 'SIGIL-1',
            symbol: 'MSFT',
            name: 'Microsoft Corporation',
            exchange: 'XNAS',
            asset_class: 'equity',
            lifecycle_status: 'active',
            reconciliation_status: 'validated',
            monitoring_tier: 'proposal_eligible',
            aliases: ['MSFT'],
            sector: 'Technology',
            broker_tradable: true,
            actively_researched: true,
            proposal_eligible: true,
            exclusion_reasons: []
          }],
          broker_submission_available: false,
          execution_authorized: false
        }
      }),
      paperExecution: async operation => ({
        ok: true,
        result: {
          environment: 'paper',
          live_execution: false,
          broker: 'alpaca_paper',
          broker_base_url: 'https://paper-api.alpaca.markets',
          broker_submission: operation !== 'deactivate',
          activated: operation !== 'deactivate',
          paused: false,
          kill_switch: operation === 'deactivate',
          revision: 7,
          evidence_identity: 'SIGIL-V2-BATCH-2-50',
          audit_identity: 'SIGIL-V2-AUD-00000007',
          degraded_conditions: [],
          policy: {
            maximum_order_notional: '25.00',
            maximum_new_positions_per_cycle: 1,
            maximum_open_positions: 3,
            maximum_pending_entry_orders: 1,
            maximum_deployed_capital: '75.00',
            maximum_symbol_exposure: '25.00',
            minimum_cash_buffer: '100.00',
            minimum_confidence: '0.75',
            maximum_spread_basis_points: '50',
            minimum_average_dollar_volume: '1000000'
          },
          progress: {
            scheduler_state: 'scanning',
            current_cursor: 50,
            current_batch: 2,
            symbols_in_batch: ['AAPL', 'MSFT'],
            symbols_completed_cycle: 50,
            total_eligible_symbols: 12984,
            coverage_percent: 0.39,
            last_completed_symbol: 'MSFT',
            last_successful_research_at: '2026-07-29T16:00:00Z',
            candidates_produced: 0,
            proposals_produced: 0,
            proposals_rejected: 0,
            leading_rejection_reasons: {
              validated_market_research_unavailable: 2
            },
            next_cycle_at: '2026-07-29T16:00:05Z',
            state: operation === 'status' ? 'awaiting_fresh_data' : 'execution_disabled'
          },
          open_positions: 0,
          open_orders: 0,
          deployed_paper_capital: '0',
          remaining_governed_allocation: '75.00',
          last_order_intent: null,
          last_submitted_order: null,
          last_fill: null,
          last_rejection: null,
          last_reconciliation: null
        }
      })
    }

    try {
      render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)

      expect(await screen.findByText('Read-only provider health')).toBeTruthy()
      expect(screen.getByText('Alpaca catalog provider status')).toBeTruthy()
      expect(screen.getByText(/Alpaca IEX · real-time/)).toBeTruthy()
      expect(screen.getByText(/Broader U.S. data · 15-minute delayed/)).toBeTruthy()
      expect(screen.getByText(/Coverage 1\/12 symbols/)).toBeTruthy()
      expect(screen.getByText(/full Alpaca asset catalog and the governed proposal universe are separate/i)).toBeTruthy()
      expect(screen.getByText(/Full U.S. listing enumeration is unavailable/)).toBeTruthy()
      expect(screen.getAllByText('MSFT').length).toBeGreaterThan(0)
      expect(screen.getByText('•••• 1234')).toBeTruthy()
      expect(screen.getByText(/secrets exposed: no/i)).toBeTruthy()
      expect(await screen.findByText('Authenticated')).toBeTruthy()
      expect(screen.getByText('Market data degraded')).toBeTruthy()
      const catalogFreshness = screen.getByText('Catalog freshness').parentElement
      expect(catalogFreshness?.textContent).toContain('0s old')
      expect(catalogFreshness?.textContent).not.toContain('Cached / stale')
      const iexCapacity = screen.getByText('Live IEX capacity').parentElement
      expect(iexCapacity?.textContent).toContain('stale/unavailable')
      expect(screen.getByText('15-minute delayed SIP')).toBeTruthy()
      expect(
        screen.getByRole('button', { name: 'Start delayed-SIP scan' }).hasAttribute('disabled')
      ).toBe(true)
      expect(
        screen.getByRole('button', { name: 'Connect live IEX' }).hasAttribute('disabled')
      ).toBe(true)
      fireEvent.click(screen.getByRole('button', { name: 'Refresh Alpaca assets' }))
      expect(
        await screen.findByText(
          'Read-only Alpaca asset refresh completed: 12984 accepted, 1164 excluded.'
        )
      ).toBeTruthy()
      expect(screen.getByText('Governed market universe')).toBeTruthy()
      expect(screen.getByText('Full Alpaca asset catalog discovered')).toBeTruthy()
      expect(screen.getByText(/Source: Alpaca Paper Trading Assets API/)).toBeTruthy()
      expect(screen.getByText(/Market-data coverage is partial under IEX/)).toBeTruthy()
      const execution = await screen.findByTestId('autonomous-paper-execution')
      expect(execution.textContent).toContain('50 / 12984')
      expect(execution.textContent).toContain('awaiting fresh data')
      expect(execution.textContent).toContain('validated_market_research_unavailable: 2')
      expect(execution.textContent).toContain('LIVE EXECUTION DISABLED')
      expect(screen.getByRole('button', { name: 'Disable' })).toBeTruthy()
      expect(screen.queryByRole('button', { name: /Activate governed paper execution/i })).toBeNull()
      expect(screen.queryByRole('button', { name: /Pause paper execution/i })).toBeNull()
      expect(screen.queryByRole('button', { name: /Resume paper execution/i })).toBeNull()
      expect(screen.queryByRole('button', { name: /Emergency paper stop/i })).toBeNull()
      expect(screen.queryByText(/Governed autonomous paper activation/i)).toBeNull()
      expect(screen.queryByText(/loading autonomous paper execution/i)).toBeNull()
      expect(screen.queryByText(/alpaca-secret|public-secret/i)).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: /Refresh providers/i }))
      await waitFor(() => expect(screen.getByText('Read-only market data is current.')).toBeTruthy())
    } finally {
      window.sigilDesktop = original
    }
  })

  it('confirmation-gates simulated proposal approval and records local audit evidence', async () => {
    const simulator = new MockSigilOperatorAdapter('disconnected')
    const adapter = {
      readSnapshot: () => simulator.readSnapshot(),
      applySimulatedAction: (action: Parameters<typeof simulator.applySimulatedAction>[0]) =>
        simulator.applySimulatedAction(action)
    }

    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')

    fireEvent.click(screen.getByRole('button', { name: 'Proposals' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'Enable simulated operator actions' })
    )

    const approve = screen.getAllByRole('button', { name: 'Approve' })[0]
    fireEvent.click(approve)

    expect(screen.getByText('Confirm simulated approval')).toBeTruthy()
    expect(
      screen.getByText(/does not authorize or submit an order/i)
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))

    await waitFor(() => {
      expect(screen.getAllByText('approved').length).toBeGreaterThan(0)
    })

    expect(screen.queryByText(/submit order/i)).toBeNull()
  })

  it('confirmation-gates simulated proposal rejection without broker submission', async () => {
    const simulator = new MockSigilOperatorAdapter('disconnected')
    const adapter = {
      readSnapshot: () => simulator.readSnapshot(),
      applySimulatedAction: (action: Parameters<typeof simulator.applySimulatedAction>[0]) =>
        simulator.applySimulatedAction(action)
    }

    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')

    fireEvent.click(screen.getByRole('button', { name: 'Proposals' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'Enable simulated operator actions' })
    )

    const reject = screen.getAllByRole('button', { name: 'Reject' })[0]
    fireEvent.click(reject)

    expect(screen.getByText('Confirm simulated rejection')).toBeTruthy()
    expect(
      screen.getByText(/reject PRP-20260725-0042 in the local simulator/i)
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm rejection' }))

    await waitFor(() => {
      expect(screen.getAllByText('rejected').length).toBeGreaterThan(0)
    })

    expect(screen.queryByText(/submit order/i)).toBeNull()
  })

  it('confirmation-gates local paper automation controls', async () => {
    const adapter = new MockSigilOperatorAdapter()

    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')
    fireEvent.click(
      screen.getByRole('button', { name: 'Resume' }),
    )

    expect(
      screen.queryByText('Confirm paper automation start'),
    ).toBeNull()

    await waitFor(() => {
      expect(screen.getByText(/paper automation running/i)).toBeTruthy()
    })

    fireEvent.click(
      screen.getByRole('button', { name: 'Pause' }),
    )

    await waitFor(() => {
      expect(screen.getByText(/paper automation paused/i)).toBeTruthy()
    })

    fireEvent.click(
      screen.getByRole('button', { name: 'Resume' }),
    )
    await waitFor(() => {
      expect(screen.getByText(/paper automation running/i)).toBeTruthy()
    })

    fireEvent.click(
      screen.getByRole('button', { name: 'Stop' }),
    )

    await waitFor(() => {
      expect(screen.getByText(/paper automation stopped/i)).toBeTruthy()
    })
  })

  it('renders governed runtime status, cycle timing, counts, and blocking reasons', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)

    const card = await screen.findByTestId('runtime-visibility')
    expect(card.textContent).toContain('Governed runtime status')
    expect(card.textContent).toContain('PAUSED')
    expect(card.textContent).toContain('HEALTHY')
    expect(card.textContent).toContain('Completed cycles')
    expect(card.textContent).toContain('3')
    expect(card.textContent).toContain('2026-07-25T14:32:13Z')
    expect(card.textContent).toContain('No cycle scheduled')
    expect(card.textContent).toContain('Automation is paused by the owner')
    expect(card.textContent).toContain('Local paper execution: available')
    expect(card.textContent).toContain('Real broker submission: unavailable')
    expect(card.textContent).not.toContain('Local paper execution: currently blocked')
  })

  it('distinguishes a safety-triggered pause from a manual pause', async () => {
    const adapter = new MockSigilOperatorAdapter()
    const snapshot = await adapter.readSnapshot()
    adapter.readSnapshot = async () => ({
      ...snapshot,
      systemHealth: 'Runtime degraded',
      runtimeVisibility: {
        ...snapshot.runtimeVisibility!,
        operationalState: 'paused',
        health: 'degraded',
        rawHealth: 'degraded',
        pauseCause: 'safety',
        nextAction: 'Resolve the safety condition, then explicitly resume automation',
        blockingReasons: [
          {
            code: 'automation_safety_paused',
            severity: 'warning',
            summary: 'Runtime health is degraded',
            requiresManualResume: true
          }
        ]
      }
    })

    render(<SigilOperatorView adapter={adapter} />)

    const card = await screen.findByTestId('runtime-visibility')
    expect(card.textContent).toContain('Runtime health is degraded')
    expect(card.textContent).toContain('automation_safety_paused')
    expect(card.textContent).toContain('explicitly resume')
    expect(card.textContent).not.toContain('paused by the owner')
  })

  it('renders the recent audit timeline newest first with complete references', async () => {
    const adapter = new MockSigilOperatorAdapter()
    const snapshot = await adapter.readSnapshot()
    adapter.readSnapshot = async () => ({
      ...snapshot,
      auditEvents: [snapshot.auditEvents[2]!, snapshot.auditEvents[0]!, snapshot.auditEvents[1]!]
    })

    render(<SigilOperatorView adapter={adapter} />)

    const card = await screen.findByTestId('runtime-visibility')
    const summaries = card.querySelectorAll('ol li')
    expect(summaries[0]?.textContent).toContain('Immutable simulated receipt recorded')
    expect(summaries[0]?.textContent).toContain('PRP-20260725-0042')
    expect(summaries[0]?.textContent).toContain('ORD-20260725-018')
    expect(summaries[0]?.textContent).toContain('EVD-9F3A7B1C')
    expect(screen.getByRole('button', { name: 'View all' })).toBeTruthy()
  })

  it('refreshes visible portfolio values when backend state changes', async () => {
    const adapter = new MockSigilOperatorAdapter()
    const initialRead = adapter.readSnapshot.bind(adapter)
    let reads = 0

    adapter.readSnapshot = async () => {
      const snapshot = await initialRead()
      reads += 1

      return reads === 1
        ? snapshot
        : {
            ...snapshot,
            cash: '$9,500.00',
            totalAccountValue: '$10,900.00',
            lastUpdated: '2026-07-26T20:00:05Z'
          }
    }

    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')
    fireEvent.click(screen.getByRole('button', { name: 'Portfolio' }))
    expect(screen.getAllByText('$10,000.00').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Refresh runtime' }))

    expect(await screen.findByText('$9,500.00')).toBeTruthy()
    expect(screen.getByText('$10,900.00')).toBeTruthy()
    expect(screen.getAllByText(/2026-07-26T20:00:05Z/).length).toBeGreaterThan(0)
  })

  it('shows paper holdings, buy and sell activity, and evidence links', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)
    await screen.findByTestId('sigil-operator')

    fireEvent.click(screen.getByRole('button', { name: 'Portfolio' }))

    expect(screen.getByText('Current simulated holdings')).toBeTruthy()
    expect(screen.getAllByText('MSFT').length).toBeGreaterThan(0)
    expect(screen.getByText('$834.16')).toBeTruthy()
    expect(screen.getByText('REC-20260725-018')).toBeTruthy()
    expect(screen.getAllByText('BUY').length).toBeGreaterThan(0)
    expect(screen.getAllByText('SELL').length).toBeGreaterThan(0)
    expect(screen.getAllByText('PAPER').length).toBeGreaterThan(0)
    expect(screen.getAllByText('SIMULATED').length).toBeGreaterThan(0)
  })

  it('confirmation-gates monthly paper authorization revocation and pauses automation', async () => {
    const adapter = new MockSigilOperatorAdapter()

    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')

    expect(screen.getByText(/Paper month 2026-07 · active/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }))

    expect(screen.getByText('Confirm authorization revocation')).toBeTruthy()
    expect(
      screen.getByText(/running automation will pause/i)
    ).toBeTruthy()
    expect(
      screen.getByText(/simulated fills will be denied/i)
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Revoke authorization' }))

    await waitFor(() => {
      expect(screen.getByText(/Paper month 2026-07 · revoked/)).toBeTruthy()
    })

    expect(screen.getByText(/automation paused/i)).toBeTruthy()
    expect(screen.queryByText(/submit order/i)).toBeNull()
  })

  it('confirmation-gates the local-only paper reset', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)
    await screen.findByTestId('sigil-operator')
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reset local paper portfolio' }))

    expect(screen.getByText('Confirm local paper portfolio reset')).toBeTruthy()
    expect(screen.getByText(/settings, local provider credentials, source files/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Reset empty paper ledger' }))

    expect(await screen.findByText('No paper holdings')).toBeTruthy()
  })
})
