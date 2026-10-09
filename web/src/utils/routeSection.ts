export type RouteProfile = { d: number[]; ele: number[]; lng: number[]; lat: number[] }

/** Clip the sampled profile, interpolating both ends of the selected interval. */
export function routeSection(p: RouteProfile, range: [number, number]) {
  if (p.d.length < 2 || !range.every(Number.isFinite)) return null
  const start = Math.max(p.d[0], Math.min(...range))
  const end = Math.min(p.d[p.d.length - 1], Math.max(...range))
  if (end <= start) return null
  const at = (d: number) => {
    let i = 1
    while (i < p.d.length - 1 && p.d[i] < d) i++
    const span = p.d[i] - p.d[i - 1]
    const f = span > 0 ? (d - p.d[i - 1]) / span : 0
    const interp = (xs: number[]) => xs[i - 1] + (xs[i] - xs[i - 1]) * f
    return { d, ele: interp(p.ele), coord: [interp(p.lng), interp(p.lat)] }
  }
  const points = [at(start)]
  p.d.forEach((d, i) => {
    if (d > start && d < end) points.push({ d, ele: p.ele[i], coord: [p.lng[i], p.lat[i]] })
  })
  points.push(at(end))
  let gain = 0, loss = 0
  for (let i = 1; i < points.length; i++) {
    const delta = points[i].ele - points[i - 1].ele
    gain += Math.max(0, delta)
    loss += Math.max(0, -delta)
  }
  const elevations = points.map((pt) => pt.ele)
  return {
    start, end, distance: end - start, gain, loss,
    min: Math.min(...elevations), max: Math.max(...elevations),
    grade: (points[points.length - 1].ele - points[0].ele) / (end - start),
    coords: points.map((pt) => pt.coord),
  }
}
