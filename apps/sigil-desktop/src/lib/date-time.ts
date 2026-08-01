const EASTERN_TIME_ZONE = 'America/New_York'

const easternDateTimeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: EASTERN_TIME_ZONE,
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit',
  timeZoneName: 'short',
})

const easternTimeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: EASTERN_TIME_ZONE,
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit',
  timeZoneName: 'short',
})

export function formatEasternDateTime(
  value: string | number | Date | null | undefined,
): string {
  if (value === null || value === undefined || value === '') {
    return 'Unavailable'
  }

  const date = value instanceof Date ? value : new Date(value)

  if (Number.isNaN(date.getTime())) {
    return 'Invalid timestamp'
  }

  return easternDateTimeFormatter.format(date)
}

export function formatEasternTime(
  value: string | number | Date | null | undefined,
): string {
  if (value === null || value === undefined || value === '') {
    return 'Unavailable'
  }

  const date = value instanceof Date ? value : new Date(value)

  if (Number.isNaN(date.getTime())) {
    return 'Invalid timestamp'
  }

  return easternTimeFormatter.format(date)
}

export const SIGIL_DISPLAY_TIME_ZONE = EASTERN_TIME_ZONE
