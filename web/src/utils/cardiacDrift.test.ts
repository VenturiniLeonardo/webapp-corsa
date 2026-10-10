import { describe, expect, it } from 'vitest'
import { cardiacDrift } from './cardiacDrift'

function run(duration = 1500, interval = 1) {
  const time = Array.from({ length: duration / interval + 1 }, (_, i) => i * interval)
  return { time, hr: time.map((): number | null => 140), speed: time.map((): number | null => 1000 / 420) }
}

describe('pace-adjusted cardiac drift', () => {
  it('starts after warm-up and baseline, and remains zero at constant effort', () => {
    const values = cardiacDrift(run())
    expect(values.slice(0, 600).every((v) => v === null)).toBe(true)
    expect(values[600]).toBeCloseTo(0)
    expect(values.at(-1)).toBeCloseTo(0)
  })

  it('does not mistake the user’s faster pace for positive cardiac drift', () => {
    const streams = run()
    streams.hr = streams.time.map((t) => t < 900 ? 140 : 160)
    streams.speed = streams.time.map((t) => 1000 / (t < 900 ? 420 : 300))
    // EF improves by 22.5% despite the higher heart rate.
    expect(cardiacDrift(streams).at(-1)).toBeCloseTo(-22.5)
  })

  it('detects efficiency loss when heart rate rises at the same pace', () => {
    const streams = run()
    streams.hr = streams.time.map((t) => t < 900 ? 140 : 154)
    expect(cardiacDrift(streams).at(-1)).toBeCloseTo((1 - 140 / 154) * 100)
  })

  it('has zero drift when speed and HR increase proportionally', () => {
    const streams = run()
    streams.hr = streams.time.map((t) => t < 900 ? 140 : 154)
    streams.speed = streams.time.map((t) => t < 900 ? 3 : 3.3)
    expect(cardiacDrift(streams).at(-1)).toBeCloseTo(0)
  })

  it('weights by duration and clips intervals at the rolling boundary', () => {
    const time = [0]
    while (time.at(-1)! < 1500) time.push(time.at(-1)! + (time.length % 2 ? 7 : 3))
    const values = cardiacDrift({ time, speed: time.map(() => 3), hr: time.map((t) => t < 900 ? 140 : 154) })
    expect(values[time.indexOf(600)]).toBeCloseTo(0)
    const i = time.indexOf(1000)
    expect(values[i]).toBeCloseTo((1 - 140 / ((200 * 140 + 100 * 154) / 300)) * 100)
    expect(values.at(-1)).toBeCloseTo((1 - 140 / 154) * 100)
  })

  it('excludes stopped and missing intervals and shows a gap', () => {
    const streams = run()
    streams.speed = streams.time.map((t) => t >= 900 && t < 1200 ? 0 : 3)
    const values = cardiacDrift(streams)
    expect(values[1000]).toBeNull()
    expect(values[1201]).toBeNull() // insufficient valid time after the stop
    expect(values[1500]).toBeCloseTo(0)
    streams.hr = streams.time.map((t) => t >= 900 && t < 1200 ? null : 140)
    streams.speed = streams.time.map(() => 3)
    expect(cardiacDrift(streams)[1000]).toBeNull()
  })

  it('does not bridge recording gaps', () => {
    const streams = run()
    const keep = streams.time.map((t) => t <= 900 || t >= 1200)
    const time = streams.time.filter((_, i) => keep[i])
    const values = cardiacDrift({ time, hr: time.map(() => 140), speed: time.map(() => 3) })
    expect(values[time.indexOf(1200)]).toBeNull()
    expect(values.at(-1)).toBeCloseTo(0)
  })

  it('requires a sufficiently complete initial reference', () => {
    expect(cardiacDrift(run(500)).every((v) => v === null)).toBe(true)
    expect(cardiacDrift({ time: [0, 600], hr: [140, 140] })).toEqual([null, null])
    const streams = run()
    streams.hr = streams.time.map((t) => t >= 300 && t < 400 ? null : 140)
    expect(cardiacDrift(streams).every((v) => v === null)).toBe(true)
  })

  it('rejects non-finite measurements and zero heart rate', () => {
    for (const invalid of [NaN, Infinity, 0]) {
      const streams = run()
      streams.hr.fill(invalid)
      expect(cardiacDrift(streams).every((v) => v === null)).toBe(true)
    }
  })
})
