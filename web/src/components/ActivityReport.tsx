import { useMemo, useState } from 'react'
import type { Detail, Lap, Settings, Similar, Streams } from '../pages/ActivityDetailPage'
import { formatDate } from '../utils/formatters'
import { field, surface } from './ui'

type N = number | null | undefined

// --- formatting (every missing value is the literal "n/a" so a model never guesses) ---
const n = (x: N, d = 0, u = '') => (x == null || !Number.isFinite(x) ? 'n/a' : `${x.toFixed(d)}${u}`)
const hms = (s: N) => {
  if (s == null || !Number.isFinite(s)) return 'n/a'
  const t = Math.round(s)
  const h = Math.floor(t / 3600)
  return `${h ? `${h}:` : ''}${String(Math.floor((t % 3600) / 60)).padStart(h ? 2 : 1, '0')}:${String(t % 60).padStart(2, '0')}`
}
const pace = (spk: N) => (spk != null && Number.isFinite(spk) && spk > 0 ? `${hms(spk)}/km` : 'n/a')
const km = (m: N) => (m == null ? 'n/a' : (m / 1000).toFixed(2))
const lapPace = (l: Lap) => (l.moving_s && l.distance_m ? (l.moving_s * 1000) / l.distance_m : l.avg_speed_ms ? 1000 / l.avg_speed_ms : null)

// --- stats ---
const q = (s: number[], p: number) => s[Math.floor((s.length - 1) * p)]
const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)
function summary(xs: number[], d = 0) {
  if (!xs.length) return 'n/a'
  const s = [...xs].sort((a, b) => a - b)
  return `min ${n(s[0], d)}, p5 ${n(q(s, 0.05), d)}, p25 ${n(q(s, 0.25), d)}, median ${n(q(s, 0.5), d)}, p75 ${n(q(s, 0.75), d)}, p95 ${n(q(s, 0.95), d)}, max ${n(s.at(-1), d)}`
}

/** Plain-text report of one activity, written to be pasted into an LLM. */
export function buildReport(d: Detail, st: Streams | undefined, hrCfg: Settings | undefined, sim: Similar[]): string {
  const a = d.activity
  const tz = a.timezone ?? 'UTC'
  const avgPace = a.moving_s && a.distance_m ? (a.moving_s * 1000) / a.distance_m : null
  const time = st?.time
  const t0 = time?.[0] ?? 0
  // positive samples of a stream channel within [from, to) seconds from activity start
  const seg = (ch: 'hr' | 'cadence' | 'speed', from = 0, to = Infinity) => {
    const ys = st?.[ch]
    const out: number[] = []
    if (!time || !ys) return out
    for (let i = 0; i < time.length; i++) {
      const t = time[i]
      const y = ys[i]
      if (t != null && y != null && y > 0 && t - t0 >= from && t - t0 < to) out.push(y)
    }
    return out
  }
  const hr = seg('hr')
  const cad = seg('cadence')
  const alt = (st?.altitude ?? []).filter((x): x is number => x != null)
  const hrMax = hrCfg?.hr_max ?? null
  const L: string[] = []
  const sec = (t: string) => L.push('', `## ${t}`)

  L.push(`# RUNNING ACTIVITY REPORT`)
  L.push(
    'Units: distance km/m, time h:mm:ss, pace min:sec per km, HR bpm, cadence steps/min (spm). "n/a" = not recorded.',
    'Tags: (measured) = sensor, (calc) = derived by math, (est.) = estimated by provider/app, (model) = model output.',
  )

  sec('OVERVIEW')
  L.push(
    `Name: ${a.name ?? 'n/a'}`,
    `Start (local, ${tz}): ${formatDate(a.start_time_utc, tz)}`,
    `Sport: ${a.sport_type}; workout type: ${a.workout_type ?? 'n/a'}; perceived difficulty: ${a.difficulty != null ? `${a.difficulty}/10` : 'n/a'}`,
    `Tags: ${d.tags.join(', ') || 'none'}`,
    `Notes: ${a.notes?.trim() || 'none'}`,
  )

  sec('TOTALS')
  L.push(
    `Distance: ${km(a.distance_m)} km (measured)`,
    `Moving time: ${hms(a.moving_s)}; elapsed time: ${hms(a.elapsed_s)}; stopped time: ${a.elapsed_s != null && a.moving_s != null ? hms(a.elapsed_s - a.moving_s) : 'n/a'}`,
    `Average pace (moving): ${pace(avgPace)} (calc); average speed: ${avgPace ? n(3.6 / (avgPace / 1000), 2, ' km/h') : 'n/a'}`,
    `Elevation gain / loss: ${n(a.elev_gain_m, 0, ' m')} / ${n(a.elev_loss_m, 0, ' m')}; altitude min / max: ${alt.length ? `${n(Math.min(...alt))} / ${n(Math.max(...alt))} m` : 'n/a'}`,
    `Calories: ${n(a.calories_kcal, 0, ' kcal')} (est.); average power: ${n(a.avg_power_w, 0, ' W')} (est.)`,
  )

  sec('HEART RATE')
  L.push(
    `Average: ${n(a.avg_hr, 0, ' bpm')} (measured); max: ${n(a.max_hr, 0, ' bpm')} (measured); athlete max HR setting: ${n(hrMax, 0, ' bpm')}`,
    `Average as % of max HR setting: ${a.avg_hr != null && hrMax ? n((a.avg_hr / hrMax) * 100, 0, '%') : 'n/a'}; peak as %: ${a.max_hr != null && hrMax ? n((a.max_hr / hrMax) * 100, 0, '%') : 'n/a'}`,
    `Range held (per-second stream distribution, bpm): ${summary(hr)}`,
    `Typical band (p5-p95): ${hr.length ? `${n(q([...hr].sort((x, y) => x - y), 0.05))}-${n(q([...hr].sort((x, y) => x - y), 0.95))} bpm` : 'n/a'}`,
  )
  const dur = time?.length ? (time.at(-1) ?? t0) - t0 : 0
  if (dur > 600 && hr.length) {
    const h1 = avg(seg('hr', 0, dur / 2))
    const h2 = avg(seg('hr', dur / 2))
    const s1 = avg(seg('speed', 0, dur / 2))
    const s2 = avg(seg('speed', dur / 2))
    L.push(
      `First half avg HR ${n(h1, 1)} bpm vs second half ${n(h2, 1)} bpm (drift ${h1 && h2 ? n(((h2 - h1) / h1) * 100, 1, '%') : 'n/a'}); first half pace ${pace(s1 ? 1000 / s1 : null)} vs second half ${pace(s2 ? 1000 / s2 : null)}`,
    )
  }
  const zs = d.metrics?.time_in_zones_s
  const edges = hrCfg?.hr_zones
  if (zs?.length && zs.reduce((x, y) => x + y, 0) > 0) {
    const tot = zs.reduce((x, y) => x + y, 0)
    const bounds = (i: number) => (edges?.length === 4 ? [i ? `${edges[i - 1]}` : '0', i < 4 ? `${edges[i]}` : `${hrMax ?? 'max'}`].join('-') : 'n/a')
    L.push('Time in HR zones (zone: bpm range, time, share):')
    zs.forEach((s, i) => L.push(`  Z${i + 1}: ${bounds(i)} bpm, ${hms(s)}, ${Math.round((s / tot) * 100)}%`))
  }

  sec('CADENCE')
  L.push(`Average: ${n(a.avg_cadence_spm, 0, ' spm')}; per-second distribution: ${summary(cad)}`)

  sec('EFFICIENCY & LOAD')
  const m = d.metrics
  L.push(
    `Efficiency factor: ${n(m?.efficiency_factor, 2)} (calc, speed per bpm; higher = fitter)`,
    `Aerobic decoupling (Pa:HR): ${n(m?.decoupling_pct, 1, '%')} (calc); pace variability CV: ${n(m?.pace_cv, 3)} (calc); steady run: ${m?.is_steady == null ? 'n/a' : m.is_steady ? 'yes' : 'no'}`,
    `TRIMP: ${n(m?.trimp, 0)} (model); GPS flagged suspect: ${m?.gps_suspect == null ? 'n/a' : m.gps_suspect ? 'yes' : 'no'}`,
  )

  // per-km table: window HR from the stream, falling back to the split's own averages
  const table = (title: string, laps: Lap[]) => {
    if (!laps.length) return
    sec(title)
    L.push('idx | dist_km | time | pace | avg_hr | hr_min-max | hr_p5-p95 | max_hr | cadence | elev_gain_m | pace_vs_run_avg_s')
    let cum = 0
    const paces = laps.map(lapPace).filter((p): p is number => p != null)
    laps.forEach((l, i) => {
      const from = l.start_offset_s ?? cum
      const to = from + (l.elapsed_s ?? l.moving_s ?? 0)
      cum = to
      const h = [...seg('hr', from, to)].sort((x, y) => x - y)
      const c = seg('cadence', from, to)
      const p = lapPace(l)
      L.push(
        [
          i + 1,
          km(l.distance_m),
          hms(l.moving_s ?? l.elapsed_s),
          pace(p),
          n(l.avg_hr ?? avg(h), 0),
          h.length ? `${h[0]}-${h.at(-1)}` : 'n/a',
          h.length ? `${q(h, 0.05)}-${q(h, 0.95)}` : 'n/a',
          n(l.max_hr ?? h.at(-1), 0),
          n(l.avg_cadence_spm ?? avg(c), 0),
          n(l.elev_gain_m, 0),
          p != null && avgPace != null ? `${p - avgPace >= 0 ? '+' : ''}${Math.round(p - avgPace)}` : 'n/a',
        ].join(' | '),
      )
    })
    if (paces.length > 1) {
      const fi = paces.indexOf(Math.min(...paces))
      const si = paces.indexOf(Math.max(...paces))
      const half = Math.floor(paces.length / 2)
      const h1 = avg(paces.slice(0, half))
      const h2 = avg(paces.slice(paces.length - half))
      L.push(`Fastest: #${fi + 1} ${pace(paces[fi])}; slowest: #${si + 1} ${pace(paces[si])}; first-half avg ${pace(h1)} vs second-half avg ${pace(h2)} (${h1 && h2 ? (h2 < h1 ? 'negative split' : 'positive split') : 'n/a'})`)
    }
  }
  table('KM SPLITS (last split may be shorter than 1 km)', d.splits)
  if (d.laps.length && d.laps.length !== d.splits.length) table('DEVICE LAPS', d.laps)

  if (d.best_efforts.length) {
    sec('BEST EFFORTS WITHIN THIS RUN')
    for (const b of d.best_efforts) L.push(`${km(b.distance_m)} km: ${hms(b.elapsed_s)} (${pace((b.elapsed_s * 1000) / b.distance_m)})${b.is_pr ? ' PERSONAL RECORD at the time' : ''}`)
  }

  if (sim.length) {
    sec('COMPARISON WITH SIMILAR RECENT RUNS (delta = this run minus that run)')
    L.push('date | dist_km | pace | pace_delta_s | avg_hr | hr_delta | EF | EF_delta')
    for (const s of sim)
      L.push([formatDate(s.start_time_utc, s.timezone ?? 'UTC').slice(0, 11), km(s.distance_m), pace(s.pace_s_per_km), n(s.pace_delta_s_per_km, 0), n(s.avg_hr, 0), n(s.hr_delta_bpm, 0), n(s.efficiency_factor, 2), n(s.ef_delta, 2)].join(' | '))
  }
  return L.join('\n')
}

export default function ActivityReport({ detail, streams, settings, similar, ready }: { detail: Detail; streams?: Streams; settings?: Settings; similar: Similar[]; ready: boolean }) {
  const text = useMemo(() => (ready ? buildReport(detail, streams, settings, similar) : ''), [ready, detail, streams, settings, similar])
  const [copied, setCopied] = useState(false)
  const copy = () => navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    Object.assign(document.createElement('a'), { href: url, download: `activity-${detail.activity.id}-report.txt` }).click()
    URL.revokeObjectURL(url)
  }
  return (
    <section className={`${surface} space-y-2`}>
      <div className="flex items-center gap-2">
        <h2 className="mr-auto text-[22px] font-semibold tracking-[.02em] text-[#eef1f4] uppercase">Report (TXT)</h2>
        <button className={`${field} min-h-10 md:min-h-0`} onClick={copy} disabled={!ready}>{copied ? 'Copied' : 'Copy'}</button>
        <button className={`${field} min-h-10 md:min-h-0`} onClick={download} disabled={!ready}>Download .txt</button>
      </div>
      <pre className="max-h-96 overflow-auto rounded-lg border border-[#262b33] bg-[#111418] p-2 text-xs font-mono tabular-nums whitespace-pre">{ready ? text : 'Loading…'}</pre>
    </section>
  )
}
