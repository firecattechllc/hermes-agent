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
  finbert: {
    enabled: true,
    available: true,
    health: 'healthy',
    sentiment_artifact_count: 1,
    latest_sentiment: {
      label: 'positive',
      confidence: 0.82,
      source_identity: 'news-42',
      freshness: null,
      limitations: ['Advisory only']
    }
  },
  embeddinggemma: {
    enabled: true,
    available: true,
    health: 'healthy',
    vector_dimension: 768,
    corpus_count: 1,
    source_count: 3,
    chunk_count: 4,
    embedding_count: 4,
    vector_store_health: 'healthy',
    latest_retrieval: {
      result_count: 2,
      freshness: ['current'],
      limitations: ['Advisory retrieval only']
    }
  },
  kronos: {
    enabled: true,
    available: true,
    health: 'healthy',
    model_id: 'neoquasar.kronos-small',
    tokenizer_id: 'neoquasar.kronos-tokenizer-base',
    supported_intervals: ['1d'],
    maximum_sequence_length: 512,
    maximum_horizon: 64,
    forecast_artifact_count: 1,
    evaluation_artifact_count: 1,
    last_successful_forecast: {
      symbol: 'AAPL',
      interval: '1d',
      forecast_horizon: 8,
      created_at: '2026-08-01T17:05:00Z',
      uncertainty_available: false,
      freshness: 'current',
      limitations: ['Advisory only']
    }
  },
  orchestration: {
    enabled: true,
    health: 'healthy',
    active_count: 0,
    completed_count: 1,
    partial_count: 0,
    failed_count: 0,
    paused_count: 0,
    pending_human_interactions: 0,
    buzz: 'unavailable',
    atlas: 'available',
    openworker: 'unavailable',
    latest: {
      orchestration_id: 'orchestration-research-001',
      plan_id: `orchestration-plan-${'a'.repeat(64)}`,
      state: 'completed',
      capabilities: ['semantic_retrieval.v1', 'financial_sentiment.v1'],
      completed_steps: 4,
      failed_steps: 0,
      artifact_id: `analysis-artifact-${'b'.repeat(64)}`,
      evidence_identities: [`sha256:${'c'.repeat(64)}`],
      failure_classification: null,
      limitations: ['Advisory only'],
      updated_at: '2026-08-01T17:10:00Z'
    }
  },
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
    expect(screen.getByText('completed · 4 completed · 0 failed')).toBeTruthy()
    expect(screen.getByText('healthy · 3 sources · 4 embeddings')).toBeTruthy()
    expect(screen.getByText('healthy · 1 forecasts · 1 evaluations')).toBeTruthy()
    expect(screen.getByText('healthy · completed · 0 pending input')).toBeTruthy()
    expect(screen.getByText('Buzz unavailable · Atlas available · OpenWorker unavailable')).toBeTruthy()
    const evidence = screen.getByText('Orchestration evidence').parentElement?.textContent ?? ''
    expect(evidence).toContain('semantic_retrieval.v1, financial_sentiment.v1')
    expect(evidence).toContain('analysis-artifact-')
  })

  it('renders the latest structured failure classification', () => {
    render(<AIFoundationPanel status={{ ...healthyStatus, last_failure_classification: 'provider_unavailable' }} />)

    expect(screen.getByText('provider_unavailable')).toBeTruthy()
    expect(document.body.textContent).not.toContain('api_key')
  })
})
