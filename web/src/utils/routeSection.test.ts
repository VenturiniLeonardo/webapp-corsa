import { describe, expect, it } from 'vitest'
import { routeSection } from './routeSection'

const profile = { d: [0, 100, 200, 300], ele: [10, 30, 20, 40], lng: [0, 1, 2, 3], lat: [4, 5, 6, 7] }
describe('selected route section', () => {
  it('clips both boundaries and measures only the selected ascent and descent', () => {
    const s = routeSection(profile, [50, 250])!
    expect(s.coords).toEqual([[0.5, 4.5], [1, 5], [2, 6], [2.5, 6.5]])
    expect(s.distance).toBe(200)
    expect(s.gain).toBe(20)
    expect(s.loss).toBe(10)
    expect(s.grade).toBe(0.05)
    expect([s.min, s.max]).toEqual([20, 30])
  })
  it('accepts reverse drags and clamps to the route limits', () => {
    expect(routeSection(profile, [400, -10])).toEqual(routeSection(profile, [0, 300]))
  })
  it('handles selections contained in a single segment', () => {
    expect(routeSection(profile, [20, 40])?.gain).toBeCloseTo(4)
    expect(routeSection(profile, [20, 40])?.coords).toHaveLength(2)
  })
  it('rejects empty, out-of-range or invalid selections and degenerate profiles', () => {
    expect(routeSection(profile, [50, 50])).toBeNull()
    expect(routeSection(profile, [400, 500])).toBeNull()
    expect(routeSection(profile, [NaN, 100])).toBeNull()
    expect(routeSection({ d: [0], ele: [10], lng: [0], lat: [4] }, [0, 100])).toBeNull()
  })
})
