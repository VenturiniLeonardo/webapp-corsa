import { describe, expect, it } from 'vitest'
import { splitTrack } from './splitTrack'

describe('splitTrack', () => {
  const streams = { distance: [0, 800, 1200, 1800, 2400], lng: [0, 8, 12, 18, 24], lat: [0, 8, 12, 18, 24] }

  it('interpolates both kilometer boundaries and includes only the selected interval', () => {
    const result = splitTrack(streams, [1000, 2000])
    expect(result.features.map((f) => f.geometry.coordinates)).toEqual([
      [[10, 10], [12, 12]], [[12, 12], [18, 18]], [[18, 18], [20, 20]],
    ])
  })

  it('clips the final partial kilometer to the end of the track', () => {
    expect(splitTrack(streams, [2000, 2500]).features.map((f) => f.geometry.coordinates)).toEqual([[[20, 20], [24, 24]]])
  })

  it('does not bridge missing GPS samples', () => {
    const result = splitTrack({ ...streams, lat: [0, 8, null, 18, 24] }, [0, 2400])
    expect(result.features.map((f) => f.geometry.coordinates)).toEqual([[[0, 0], [8, 8]], [[18, 18], [24, 24]]])
  })

  it('clears the highlight and tolerates missing distance data or invalid intervals', () => {
    expect(splitTrack(streams, null).features).toEqual([])
    expect(splitTrack({ lat: [0, 1], lng: [0, 1] }, [0, 1000]).features).toEqual([])
    expect(splitTrack(streams, [1000, 1000]).features).toEqual([])
    expect(splitTrack(streams, [0, NaN]).features).toEqual([])
  })
})
