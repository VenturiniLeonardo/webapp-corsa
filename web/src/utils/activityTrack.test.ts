import { GeoJSONVT } from '@maplibre/geojson-vt'
import type { FeatureCollection, LineString } from 'geojson'
import { describe, expect, it } from 'vitest'
import { activityTrackSource } from './activityTrack'

// Two-metre GPS segments, as recorded during a run at one sample per second.
const track: FeatureCollection<LineString> = {
  type: 'FeatureCollection',
  features: Array.from({ length: 20 }, (_, i) => ({
    type: 'Feature',
    properties: { pace: 300 + i, hr: 140 + i, power: 200 + i, elev: 250 + i },
    geometry: { type: 'LineString', coordinates: [[7.68 + i * 0.00003, 45.07], [7.68 + (i + 1) * 0.00003, 45.07]] },
  })),
}

function tileAt(index: GeoJSONVT, zoom: number) {
  const scale = 2 ** zoom
  const lat = 45.07 * Math.PI / 180
  return index.getTile(zoom, Math.floor((7.68 / 360 + 0.5) * scale), Math.floor((0.5 - Math.log(Math.tan(Math.PI / 4 + lat / 2)) / (2 * Math.PI)) * scale))!
}

describe('activity track rendering', () => {
  it('reproduces disappearing short segments with the default map simplification', () => {
    // MapLibre scales pixel tolerance to its 8192-unit tiles (512 pixels).
    const index = new GeoJSONVT(track, { extent: 8192, tolerance: 0.375 * 8192 / 512, maxZoom: 18 })
    expect(tileAt(index, 12).features).toHaveLength(0)
    expect(tileAt(index, 16).features).toHaveLength(track.features.length)
  })

  it.each([10, 12, 14, 16])('keeps every coloured GPS segment at zoom %i', (zoom) => {
    const source = activityTrackSource(track)
    const index = new GeoJSONVT(track, { extent: 8192, tolerance: source.tolerance! * 8192 / 512, maxZoom: 18 })
    const tile = tileAt(index, zoom)
    expect(tile.features).toHaveLength(track.features.length)
    expect(tile.features.map((f) => f.tags)).toEqual(track.features.map((f) => f.properties))
  })
})
