import { describe, expect, it } from 'vitest'

import {
  formatEasternDateTime,
  formatEasternTime,
  SIGIL_DISPLAY_TIME_ZONE,
} from './date-time'

describe('Eastern Time formatting', () => {
  it('uses the America/New_York time zone', () => {
    expect(SIGIL_DISPLAY_TIME_ZONE).toBe('America/New_York')
  })

  it('formats summer timestamps as EDT', () => {
    expect(formatEasternDateTime('2026-08-01T03:10:12Z')).toContain('EDT')
  })

  it('formats winter timestamps as EST', () => {
    expect(formatEasternDateTime('2026-01-01T03:10:12Z')).toContain('EST')
  })

  it('formats time-only values in Eastern Time', () => {
    expect(formatEasternTime('2026-08-01T03:10:12Z')).toContain('EDT')
  })

  it('handles missing timestamps safely', () => {
    expect(formatEasternDateTime(null)).toBe('Unavailable')
  })

  it('handles invalid timestamps safely', () => {
    expect(formatEasternDateTime('not-a-date')).toBe('Invalid timestamp')
  })
})
