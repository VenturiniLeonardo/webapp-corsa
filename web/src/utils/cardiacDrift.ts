type Samples = (number | null)[]
type DriftStreams = { time?: Samples; hr?: Samples; speed?: Samples }

export const DRIFT_WINDOW_S = 300
const MAX_GAP_S = 10
const MIN_COVERAGE = 0.8

/** Rolling Pa:HR efficiency loss, relative to minutes 5–10 (after warm-up).
 * Integrate speed and HR over the same valid time intervals; never average pace
 * or weight samples equally, since recording intervals may vary.
 * This is an efficiency indicator, not a physiological model of HR versus pace.
 */
export function cardiacDrift({ time = [], hr = [], speed = [] }: DriftStreams): Samples {
  const result: Samples = time.map(() => null)
  const start = time.find((t) => t != null && Number.isFinite(t))
  if (start == null) return result
  const baselineStart = start + DRIFT_WINDOW_S
  const baselineEnd = baselineStart + DRIFT_WINDOW_S
  type Segment = { start: number; end: number; speed: number; hr: number }
  const segments: Segment[] = []
  let baselineSeconds = 0
  let baselineDistance = 0
  let baselineHeartbeats = 0
  for (let i = 1; i < time.length; i++) {
    const t0 = time[i - 1], t1 = time[i], v = speed[i - 1], h = hr[i - 1]
    if (t0 == null || t1 == null || v == null || h == null ||
      ![t0, t1, v, h].every(Number.isFinite) || t1 <= t0 || t1 - t0 > MAX_GAP_S || v < 0.5 || h <= 0) continue
    // Do not accept overlapping/out-of-order intervals from a broken stream.
    if (segments.length && t0 < segments[segments.length - 1].end) continue
    segments.push({ start: t0, end: t1, speed: v, hr: h })
    const dt = Math.max(0, Math.min(t1, baselineEnd) - Math.max(t0, baselineStart))
    baselineSeconds += dt
    baselineDistance += v * dt
    baselineHeartbeats += h * dt
  }
  if (baselineSeconds < DRIFT_WINDOW_S * MIN_COVERAGE || baselineHeartbeats <= 0) return result
  const baselineEfficiency = baselineDistance / baselineHeartbeats
  let left = 0, right = 0
  let seconds = 0, distance = 0, heartbeats = 0
  let previousTime = start
  for (let i = 0; i < time.length; i++) {
    const t = time[i]
    if (t == null || !Number.isFinite(t) || t < previousTime) continue
    previousTime = t
    while (right < segments.length && segments[right].end <= t) {
      const seg = segments[right++]
      const dt = seg.end - seg.start
      seconds += dt; distance += seg.speed * dt; heartbeats += seg.hr * dt
    }
    const windowStart = t - DRIFT_WINDOW_S
    while (left < right && segments[left].end <= windowStart) {
      const seg = segments[left++]
      const dt = seg.end - seg.start
      seconds -= dt; distance -= seg.speed * dt; heartbeats -= seg.hr * dt
    }
    // Clip the first interval at the exact window boundary.
    const first = segments[left]
    const clip = left < right ? Math.max(0, windowStart - first.start) : 0
    const validSeconds = seconds - clip
    const validDistance = distance - (clip ? first.speed * clip : 0)
    const validHeartbeats = heartbeats - (clip ? first.hr * clip : 0)
    // Leave a visible gap at pauses or missing samples, even if the preceding
    // window still has enough coverage. Resume once valid data returns.
    const current = right > left ? segments[right - 1] : undefined
    if (t < baselineEnd || current?.end !== t || validSeconds < DRIFT_WINDOW_S * MIN_COVERAGE || validHeartbeats <= 0) continue
    result[i] = (1 - (validDistance / validHeartbeats) / baselineEfficiency) * 100
  }
  return result
}
