const p2 = (n: number) => String(n).padStart(2, '0')

export function formatPace(secondsPerKm: number | null): string {
  if (secondsPerKm == null || !Number.isFinite(secondsPerKm) || secondsPerKm <= 0) return '—'
  const s = Math.round(secondsPerKm)
  return `${Math.floor(s / 60)}:${p2(s % 60)} /km`
}

export function formatDistance(meters: number): string {
  return `${(meters / 1000).toFixed(2)} km`
}

export function formatDuration(seconds: number): string {
  const t = Math.round(seconds)
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const s = t % 60
  if (h > 0) return `${h}h ${p2(m)}m ${p2(s)}s`
  if (m > 0) return `${m}m ${p2(s)}s`
  return `${s}s`
}

export function formatDate(isoUtc: string, timezone: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: timezone,
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(new Date(isoUtc))
      .map((x) => [x.type, x.value]),
  )
  return `${parts.day} ${parts.month} ${parts.year}, ${parts.hour}:${parts.minute}`
}
