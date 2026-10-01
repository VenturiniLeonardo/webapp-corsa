import { describe, expect, it } from 'vitest'
import { formatDate, formatDistance, formatDuration, formatPace } from './formatters'

describe('formatters', () => {
  it('pace', () => {
    expect(formatPace(312)).toBe('5:12 /km')
    expect(formatPace(359.6)).toBe('6:00 /km')
    expect(formatPace(null)).toBe('—')
  })
  it('distance', () => {
    expect(formatDistance(10250)).toBe('10.25 km')
    expect(formatDistance(0)).toBe('0.00 km')
  })
  it('duration', () => {
    expect(formatDuration(3665)).toBe('1h 01m 05s')
    expect(formatDuration(125)).toBe('2m 05s')
    expect(formatDuration(9)).toBe('9s')
  })
  it('date uses the given timezone', () => {
    expect(formatDate('2026-01-15T23:30:00Z', 'Europe/Rome')).toBe('16 Jan 2026, 00:30')
    expect(formatDate('2026-01-15T23:30:00Z', 'UTC')).toBe('15 Jan 2026, 23:30')
  })
})
