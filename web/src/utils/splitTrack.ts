import type { FeatureCollection, LineString } from 'geojson'

/** Clip GPS segments to a split's distance interval, preserving gaps in GPS data. */
export function splitTrack(
  streams: { distance?: (number | null)[]; lat?: (number | null)[]; lng?: (number | null)[] },
  range: [number, number] | null,
): FeatureCollection<LineString> {
  const result: FeatureCollection<LineString> = { type: 'FeatureCollection', features: [] }
  if (!range || !range.every(Number.isFinite) || range[1] <= range[0]) return result
  const { distance = [], lat = [], lng = [] } = streams
  for (let i = 1; i < distance.length; i++) {
    const d0 = distance[i - 1], d1 = distance[i]
    const x0 = lng[i - 1], x1 = lng[i], y0 = lat[i - 1], y1 = lat[i]
    if (![d0, d1, x0, x1, y0, y1].every((v) => v != null && Number.isFinite(v))) continue
    if (d1! <= d0!) continue
    const start = Math.max(d0!, range[0]), end = Math.min(d1!, range[1])
    if (end <= start) continue
    const at = (d: number) => {
      const fraction = (d - d0!) / (d1! - d0!)
      return [x0! + (x1! - x0!) * fraction, y0! + (y1! - y0!) * fraction]
    }
    result.features.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [at(start), at(end)] } })
  }
  return result
}
