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
    expect(screen.getByText('AAPL · 1d · 8 points · current')).toBeTruthy()
    expect(screen.getByText('healthy · 3 sources · 4 embeddings')).toBeTruthy()
    expect(screen.getByText('healthy · 1 forecasts · 1 evaluations')).toBeTruthy()
  })

  it('renders the latest structured failure classification', () => {
    render(<AIFoundationPanel status={{ ...healthyStatus, last_failure_classification: 'provider_unavailable' }} />)

    expect(screen.getByText('provider_unavailable')).toBeTruthy()
    expect(document.body.textContent).not.toContain('api_key')
  })
})
