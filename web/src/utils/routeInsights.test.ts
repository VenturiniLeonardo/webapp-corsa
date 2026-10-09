import { describe, expect, it } from 'vitest'
import { routeInsights } from './routeInsights'

describe('route insights', () => {
  it('finds continuous climbs and descents and weights grades by distance', () => {
    const out = routeInsights({ d: [0, 100, 300, 400, 500, 650, 800], grade: [null, 0.03, 0.06, 0.02, -0.04, -0.08, 0.07] })
    expect(out.climb).toEqual({ start: 0, end: 300, distance: 300, grade: 0.05 })
    expect(out.descent).toEqual({ start: 400, end: 650, distance: 250, grade: -0.064 })
    expect(out.maxDescent).toBe(-0.08)
  })
  it('splits stretches at missing grades and finishes the final stretch', () => {
    const out = routeInsights({ d: [0, 100, 200, 400], grade: [null, 0.05, null, 0.04] })
    expect(out.climb).toEqual({ start: 200, end: 400, distance: 200, grade: 0.04 })
    expect(out.descent).toBeNull()
    expect(out.maxDescent).toBeNull()
  })
  it('does not classify flat routes or zero-length samples as a climb', () => {
    expect(routeInsights({ d: [0, 0, 100, 200], grade: [null, 0.2, 0.02, -0.02] }).climb).toBeNull()
    expect(routeInsights({ d: [0], grade: [null] })).toEqual({ climb: null, descent: null, maxDescent: null })
  })
})
