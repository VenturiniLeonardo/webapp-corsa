export const PRESETS = ['Week', '4W', '12W', '6M', 'YTD', '1Y', 'All'] as const

export const iso = (d: Date) => d.toLocaleDateString('sv')

export function presetFrom(p: string): string | undefined {
  const d = new Date()
  if (p === 'Week') d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
  else if (p === '4W') d.setDate(d.getDate() - 28)
  else if (p === '12W') d.setDate(d.getDate() - 84)
  else if (p === '6M') d.setMonth(d.getMonth() - 6)
  else if (p === '1Y') d.setFullYear(d.getFullYear() - 1)
  else if (p === 'YTD') d.setMonth(0, 1)
  else return undefined
  return iso(d)
}
