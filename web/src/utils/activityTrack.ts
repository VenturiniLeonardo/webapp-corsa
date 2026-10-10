import type { FeatureCollection } from 'geojson'
import type { GeoJSONSourceSpecification } from 'maplibre-gl'

export function activityTrackSource(data: FeatureCollection): GeoJSONSourceSpecification {
  // Each feature is a short GPS segment. Default simplification drops entire
  // segments at overview zoom levels, leaving only scattered round line caps.
  return { type: 'geojson', data, tolerance: 0 }
}
