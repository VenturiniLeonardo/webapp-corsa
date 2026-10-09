type Profile = { d: number[]; grade: (number | null)[] }
type Stretch = { start: number; end: number; distance: number; grade: number }

/** Continuous stretches beyond ±2%, using the backend's smoothed grades. */
export function routeInsights(p: Profile): { climb: Stretch | null; descent: Stretch | null; maxDescent: number | null } {
  let climb: Stretch | null = null
  let descent: Stretch | null = null
  let current: Stretch | null = null
  let direction = 0
  let weightedGrade = 0
  let minGrade: number | null = null
  const finish = () => {
    if (!current) return
    current.grade = weightedGrade / current.distance
    if (direction === 1 && (!climb || current.distance > climb.distance)) climb = current
    if (direction === -1 && (!descent || current.distance > descent.distance)) descent = current
    current = null
    weightedGrade = 0
  }
  for (let i = 1; i < p.d.length; i++) {
    const g = p.grade[i]
    const distance = p.d[i] - p.d[i - 1]
    if (distance <= 0 || g == null || !Number.isFinite(g)) {
      finish()
      direction = 0
      continue
    }
    minGrade = Math.min(minGrade ?? g, g)
    const next = g > 0.02 ? 1 : g < -0.02 ? -1 : 0
    if (next !== direction || !next) finish()
    direction = next
    if (!next) continue
    if (!current) current = { start: p.d[i - 1], end: p.d[i], distance: 0, grade: 0 }
    current.end = p.d[i]
    current.distance += distance
    weightedGrade += g * distance
  }
  finish()
  return { climb, descent, maxDescent: minGrade != null && minGrade < 0 ? minGrade : null }
}
