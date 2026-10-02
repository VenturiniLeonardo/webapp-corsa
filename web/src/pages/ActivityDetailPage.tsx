import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import { type ExpressionSpecification, type GeoJSONSource, LngLatBounds, Map as MlMap, Marker, setWorkerUrl } from 'maplibre-gl'
import type { Feature, FeatureCollection } from 'geojson'
import 'maplibre-gl/dist/maplibre-gl.css'
import mlWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { type ReactNode, type RefObject, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import ActivityReport from '../components/ActivityReport'
import AiPanel from '../components/AiPanel'
import { btnGhost, field, FB, FC, FM, MUTED, pill, surface } from '../components/ui'
import { formatDate, formatDistance, formatDuration, formatPace } from '../utils/formatters'

// --- API shapes (app/api/activities.py) -------------------------------------
type Summary = {
  id: number
  name: string | null
  sport_type: string
  workout_type: string | null
  start_time_utc: string
  timezone: string | null
  distance_m: number | null
  moving_s: number | null
  avg_hr: number | null
}
type Activity = Summary & {
  notes: string | null
  difficulty: number | null
  elapsed_s: number | null
  elev_gain_m: number | null
  elev_loss_m: number | null
  max_hr: number | null
  avg_cadence_spm: number | null
  avg_power_w: number | null
  calories_kcal: number | null
  excluded_from_stats: boolean
  weather_temp_c: number | null
  weather_dew_point_c: number | null
}
export type Lap = {
  idx: number
  start_offset_s: number | null
  max_hr: number | null
  avg_cadence_spm: number | null
  distance_m: number | null
  moving_s: number | null
  elapsed_s: number | null
  avg_speed_ms: number | null
  avg_hr: number | null
  elev_gain_m: number | null
  gap_speed_ms?: number | null
}
export type Detail = {
  activity: Activity
  metrics: { efficiency_factor: number | null; time_in_zones_s: number[] | null; decoupling_pct: number | null; pace_cv: number | null; is_steady: boolean | null; trimp: number | null; gps_suspect: boolean | null; gap_speed_ms: number | null; hr_at_ref_pace: number | null; ef_adjusted: number | null } | null
  laps: Lap[]
  splits: Lap[]
  best_efforts: { distance_m: number; elapsed_s: number; is_pr: boolean }[]
  sources: { id: number; source: string; external_id: string; status: string; fetched_at: string | null; mapper_version: number | null; is_primary: boolean }[]
  tags: string[]
  duplicate_candidates: Summary[]
  heat_slowdown_pct: number | null
}
export type Similar = Summary & {
  pace_s_per_km: number | null
  efficiency_factor: number | null
  pace_delta_s_per_km: number | null
  hr_delta_bpm: number | null
  ef_delta: number | null
}
type Ch = 'time' | 'distance' | 'hr' | 'speed' | 'lat' | 'lng' | 'altitude' | 'cadence' | 'power'
export type Streams = Partial<Record<Ch, (number | null)[]>>
export type Settings = { hr_max: number | null; hr_zones: number[] | null; ref_pace_s_per_km?: number | null }
type ColorBy = 'pace' | 'hr' | 'power' | 'elev'
const COLOR_BY: [ColorBy, string, Ch][] = [['pace', 'Pace', 'speed'], ['hr', 'HR', 'hr'], ['power', 'Power', 'power'], ['elev', 'Elevation', 'altitude']]

// maplibre v6 resolves its worker next to its own module; Vite bundles that away, so point it explicitly
setWorkerUrl(mlWorkerUrl)

const WORKOUTS = ['easy', 'long', 'workout', 'race', 'other']
const PACE_CLAMP = 600 // 10:00/km (map colours)
const CHART_PACE_MAX = 720 // 12:00/km: slow tail of the pace chart
// map track ramps, dark → bright per metric (pace blue, HR red, power amber, elev gray)
const RAMPS: Record<ColorBy, string[]> = {
  pace: ['#1e3a8a', '#2563eb', '#3b82f6', '#60a5fa', '#bfdbfe'],
  hr: ['#450a0a', '#991b1b', '#dc2626', '#f87171', '#fecaca'],
  power: ['#451a03', '#92400e', '#d97706', '#fbbf24', '#fef3c7'],
  elev: ['#27272a', '#52525b', '#71717a', '#a1a1aa', '#e4e4e7'],
}
const rampOf = (k: ColorBy) => (k === 'pace' ? RAMPS.pace : [...RAMPS[k]].reverse()) // high/fast = dark, low/slow = light (pace axis is s/km, so lo = fast)
const ZONE_COLORS = ['#3f1d1d', '#7f1d1d', '#b91c1c', '#ef4444', '#fca5a5'] // sequential, not rainbow
const C = { pace: '#4c8dff', hr: '#ef4444', elev: '#6b7280', cad: '#a855f7', pow: '#f59e0b', grid: '#22222a', text: '#a3a3a3' }
const EFFORTS: [number, string][] = [[400, '400m'], [1000, '1k'], [1609.34, '1mi'], [5000, '5k'], [10000, '10k'], [21097.5, '21.1k'], [42195, '42.2k']]
const mono = 'font-mono tabular-nums'
const GROUP = 'activity'

// --- helpers ----------------------------------------------------------------
const clock = (s: number) => {
  const t = Math.round(s)
  const h = Math.floor(t / 3600)
  const mm = String(Math.floor((t % 3600) / 60)).padStart(h ? 2 : 1, '0')
  return `${h ? `${h}:` : ''}${mm}:${String(t % 60).padStart(2, '0')}`
}
const lapPace = (l: Lap) => (l.moving_s && l.distance_m ? (l.moving_s * 1000) / l.distance_m : l.avg_speed_ms ? 1000 / l.avg_speed_ms : null)
const sign = (d: number, s: string) => `${d < 0 ? '−' : '+'}${s}`
function median(xs: (number | null | undefined)[]): number | null {
  const s = xs.filter((x): x is number => x != null && Number.isFinite(x)).sort((a, b) => a - b)
  if (!s.length) return null
  const m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}
function nearest(xs: number[], v: number) {
  let lo = 0
  let hi = xs.length - 1
  while (lo < hi) {
    const m = (lo + hi) >> 1
    if (xs[m] < v) lo = m + 1
    else hi = m
  }
  return lo
}
function fillForward(xs: (number | null)[]): number[] {
  let last = 0
  return xs.map((x) => (x == null ? last : (last = x)))
}
const pct = (xs: number[], p: number) => [...xs].sort((a, b) => a - b)[Math.floor((xs.length - 1) * p)]
const has = (xs?: (number | null)[]) => !!xs?.some((x) => x != null)
const effortLabel = (d: number) => EFFORTS.reduce((a, b) => (Math.abs(b[0] - d) < Math.abs(a[0] - d) ? b : a))[1]

// --- page -------------------------------------------------------------------
export default function ActivityDetailPage() {
  const id = Number(useParams().id)
  const qc = useQueryClient()
  const detail = useQuery({ queryKey: ['activity', id], queryFn: () => api<Detail>(`/api/activities/${id}`) })
  const streams = useQuery({
    queryKey: ['streams', id],
    queryFn: () => api<{ channels: Streams }>(`/api/activities/${id}/streams`).then((r) => r.channels),
    staleTime: Infinity,
  })
  const similar = useQuery({ queryKey: ['similar', id], queryFn: () => api<Similar[]>(`/api/activities/${id}/similar`) })
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api<Settings>('/api/settings') })
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api(`/api/activities/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'streams' })
    },
  })

  const nav = useNavigate()
  const del = useMutation({
    mutationFn: () => api(`/api/activities/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.removeQueries({ predicate: (q) => q.queryKey[1] === id }) // activity, streams, similar of the deleted run
      qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'streams' })
      nav('/activities')
    },
  })

  const [xMode, setXMode] = useState<'distance' | 'time'>('distance')
  const [colorBy, setColorBy] = useState<ColorBy>('pace')
  const [hoverSplit, setHoverSplit] = useState<number | null>(null)
  const cursor = useRef<((i: number | null) => void) | null>(null)

  const st = streams.data
  const series = useMemo(() => {
    if (!st?.time?.length) return null
    const mode: Series['mode'] = xMode === 'distance' && has(st.distance) ? 'distance' : 'time'
    const x = mode === 'distance' ? fillForward(st.distance!).map((d) => d / 1000) : fillForward(st.time)
    const pace = (st.speed ?? []).map((v) => (v == null ? null : v > 0 ? Math.min(1000 / v, CHART_PACE_MAX) : CHART_PACE_MAX))
    return { mode, x, pace, dist: st.distance ? fillForward(st.distance) : null }
  }, [st, xMode])

  const splits = detail.data?.splits
  const splitEnds = useMemo(() => {
    const ends: number[] = []
    for (const s of splits ?? []) ends.push((ends.at(-1) ?? 0) + (s.distance_m ?? 0))
    return ends
  }, [splits])

  // ponytail: axis-pointer events fire once per connected chart; handler is O(log n) + a no-op setState
  const onHover = useCallback(
    (v: number | null) => {
      if (!series || v == null) {
        cursor.current?.(null)
        setHoverSplit(null)
        return
      }
      const i = nearest(series.x, v)
      cursor.current?.(i)
      const d = series.dist?.[i]
      setHoverSplit(d == null || !splitEnds.length ? null : Math.min(nearest(splitEnds, d), splitEnds.length - 1))
    },
    [series, splitEnds],
  )

  if (detail.isPending) return <p className="text-sm text-neutral-500">Loading…</p>
  if (detail.isError) return <p className="text-sm text-red-400">Failed to load activity.</p>
  const d = detail.data
  const a = d.activity
  const tz = a.timezone ?? 'UTC'
  const strava = d.sources.find((s) => s.source === 'strava')
  const ef = d.metrics?.efficiency_factor ?? null
  const pace = a.moving_s && a.distance_m ? (a.moving_s * 1000) / a.distance_m : null
  const sim = similar.data ?? []
  const best1k = d.best_efforts.find((b) => b.distance_m === 1000)?.elapsed_s
  const alt = (st?.altitude ?? []).filter((x): x is number => x != null)
  const m = d.metrics
  const gap = m?.gap_speed_ms ? 1000 / m.gap_speed_ms : null
  const refPace = settings.data?.ref_pace_s_per_km ?? 420

  const delta = (me: number | null, others: (number | null)[], fmt: (x: number) => string) => {
    const m = median(others)
    return me == null || m == null ? undefined : sign(me - m, fmt(Math.abs(me - m)))
  }

  return (
    <div className="space-y-4" style={{ fontFamily: FB }}>
      {/* 1. header */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="mr-auto min-w-0">
          <div className="text-xs tracking-[.08em] uppercase" style={{ fontFamily: FM, color: MUTED }}>{formatDate(a.start_time_utc, tz)}</div>
          <h1 className="mt-1 text-[44px] leading-none font-bold tracking-[.01em] text-[#eef1f4] uppercase" style={{ fontFamily: FC }}>{a.name ?? '—'}</h1>
        </div>
        <select
          aria-label="Workout type"
          className={`${field} min-h-10 md:min-h-0`}
          value={a.workout_type ?? ''}
          onChange={(e) => patch.mutate({ workout_type: e.target.value || null })}
        >
          <option value="">—</option>
          {WORKOUTS.map((w) => (
            <option key={w}>{w}</option>
          ))}
        </select>
        <select
          aria-label="Difficulty"
          className={`${field} min-h-10 md:min-h-0 tabular-nums`}
          value={a.difficulty ?? ''}
          onChange={(e) => patch.mutate({ difficulty: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">difficoltà —</option>
          {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
            <option key={n} value={n}>
              difficoltà {n}/10
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-xs text-neutral-400">
          <input type="checkbox" checked={a.excluded_from_stats} onChange={(e) => patch.mutate({ excluded_from_stats: e.target.checked })} />
          exclude from stats
        </label>
        {d.sources.map((s) => (
          <span key={s.id} className="rounded-full bg-[#20252c] px-2 text-xs text-neutral-300" title={s.source}>
            {s.source === 'strava' ? 'Strava' : s.source.replace('file_', '').toUpperCase()}
          </span>
        ))}
        {strava && (
          <a href={`https://www.strava.com/activities/${strava.external_id}`} target="_blank" rel="noreferrer" className="text-sm text-[#4c8dff] hover:underline">
            View on Strava
          </a>
        )}
        {patch.isError && <span className="text-xs text-red-400">Save failed</span>}
        <div className="ml-auto flex gap-2">
          <button
            className={`${btnGhost} gap-1.5`}
            onClick={() => {
              const n = window.prompt('Nome allenamento', a.name ?? '')?.trim()
              if (n) patch.mutate({ name: n })
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>
            Rinomina
          </button>
          <button
            className={`${btnGhost} gap-1.5 hover:!border-red-400/60 hover:!text-red-400`}
            disabled={del.isPending}
            onClick={() => window.confirm('Eliminare definitivamente questo allenamento?') && del.mutate()}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /></svg>
            Elimina
          </button>
        </div>
      </header>

      {/* 2. stats */}
      <section className={`${surface} grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4`}>
        <Group title="Time / distance">
          <Stat label="Distance" value={a.distance_m != null ? formatDistance(a.distance_m) : '—'} delta={delta(a.distance_m, sim.map((s) => s.distance_m), (x) => `${(x / 1000).toFixed(2)} km`)} />
          <Stat label="Moving" value={a.moving_s != null ? formatDuration(a.moving_s) : '—'} delta={delta(a.moving_s, sim.map((s) => s.moving_s), formatDuration)} />
          <Stat label="Total" value={a.elapsed_s != null ? formatDuration(a.elapsed_s) : '—'} />
          <Stat label="Pace" value={formatPace(pace)} delta={delta(pace, sim.map((s) => s.pace_s_per_km), (x) => `${Math.round(x)} s/km`)} />
          <Stat label="GAP" value={formatPace(gap)} model="Grade-adjusted pace: each stretch weighted by the Minetti energy cost of its slope (flat equivalent)" />
          <Stat label="Best 1 km" value={best1k != null ? formatPace(best1k) : '—'} />
        </Group>
        <Group title="Heart">
          <Stat label="Avg HR" value={a.avg_hr != null ? `${Math.round(a.avg_hr)} bpm` : '—'} delta={delta(a.avg_hr, sim.map((s) => s.avg_hr), (x) => `${Math.round(x)}`)} />
          <Stat label="Max HR" value={a.max_hr != null ? `${Math.round(a.max_hr)} bpm` : '—'} />
          <Stat label="EF" value={ef != null ? ef.toFixed(2) : '—'} delta={delta(ef, sim.map((s) => s.efficiency_factor), (x) => x.toFixed(2))} />
          <Stat label="EF corretto" value={m?.ef_adjusted != null ? m.ef_adjusted.toFixed(2) : '—'} model="EF on grade-adjusted speed, raised by the expected heat slowdown" />
          <Stat label="Decoupling" value={m?.decoupling_pct != null ? `${m.decoupling_pct.toFixed(1)}%` : '—'} />
          <Stat label={`HR @ ${clock(refPace)}`} value={m?.hr_at_ref_pace != null ? `${Math.round(m.hr_at_ref_pace)} bpm` : '—'} />
        </Group>
        <Group title="Terrain">
          <Stat label="D+ / D−" value={`${a.elev_gain_m != null ? Math.round(a.elev_gain_m) : '—'} / ${a.elev_loss_m != null ? Math.round(a.elev_loss_m) : '—'} m`} />
          <Stat label="Alt min / max" value={alt.length ? `${Math.round(Math.min(...alt))} / ${Math.round(Math.max(...alt))} m` : '—'} />
          <Stat label="Temp / dew pt" value={a.weather_temp_c != null && a.weather_dew_point_c != null ? `${Math.round(a.weather_temp_c)} / ${Math.round(a.weather_dew_point_c)} °C` : '—'} est="Open-Meteo hourly at the run midpoint, not measured on the route" />
          <Stat label="Heat cost" value={d.heat_slowdown_pct != null ? `+${d.heat_slowdown_pct.toFixed(1)}%` : '—'} model="Expected pace slowdown from temperature + dew point (Hadley table)" />
        </Group>
        <Group title="Other">
          <Stat label="Cadence" value={a.avg_cadence_spm != null ? `${Math.round(a.avg_cadence_spm)} spm` : '—'} />
          <Stat label="Power" value={a.avg_power_w != null ? `${Math.round(a.avg_power_w)} W` : '—'} est="Estimated by the provider, not measured by a power meter" />
          <Stat label="Calories" value={a.calories_kcal != null ? `${a.calories_kcal} kcal` : '—'} est="Estimated from HR/pace and body data" />
        </Group>
        {sim.length > 0 && <p className="col-span-full text-xs text-neutral-500">Δ vs median of {sim.length} similar runs</p>}
      </section>

      {/* 3. map | splits */}
      <section className="grid gap-4 xl:grid-cols-[3fr_2fr]">
        <div className={`${surface} space-y-2`}>
          <div className="flex gap-1 text-xs">
            {COLOR_BY.filter(([, , ch]) => has(st?.[ch])).map(([k, label]) => (
              <button key={k} aria-pressed={colorBy === k} onClick={() => setColorBy(k)} className={pill(colorBy === k)}>
                {label}
              </button>
            ))}
          </div>
          {has(st?.lat) && has(st?.lng) ? (
            <TrackMap st={st!} colorBy={colorBy} cursor={cursor} />
          ) : (
            <p className="flex h-60 items-center justify-center rounded-lg border border-dashed border-[#262b33] text-sm text-neutral-500">{streams.isPending ? 'Loading…' : 'No GPS track'}</p>
          )}
        </div>
        <div className={surface}>
          <Splits splits={d.splits} hl={hoverSplit} />
        </div>
      </section>

      {/* 4. charts */}
      {series && st && (
        <section className={`${surface} space-y-2`}>
          <div className="flex gap-1 text-xs">
            {(['distance', 'time'] as const).map((k) => (
              <button key={k} aria-pressed={series.mode === k} onClick={() => setXMode(k)} className={pill(series.mode === k)}>
                {k === 'distance' ? 'Distance' : 'Time'}
              </button>
            ))}
          </div>
          <Charts st={st} series={series} settings={settings.data} onHover={onHover} />
        </section>
      )}

      {/* 5. zones */}
      {d.metrics?.time_in_zones_s && <Zones secs={d.metrics.time_in_zones_s} />}

      <div className={surface}>
        <AiPanel path={`/api/ai/activity/${id}`} />
      </div>

      {/* 6. best efforts */}
      {d.best_efforts.length > 0 && (
        <Section title="Best efforts">
          <table className="w-full max-w-md text-sm">
            <tbody className={mono}>
              {d.best_efforts.map((b) => (
                <tr key={b.distance_m} className="border-b border-[#262b33]">
                  <td className="py-1 pr-3 font-sans text-neutral-400">{effortLabel(b.distance_m)}</td>
                  <td className="pr-3 text-right">{clock(b.elapsed_s)}</td>
                  <td className="pr-3 text-right text-neutral-400">{formatPace((b.elapsed_s * 1000) / b.distance_m)}</td>
                  <td className="w-10">{b.is_pr && <span className="rounded-sm border border-accent px-1 text-xs text-[#4c8dff]">PR</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {/* 7. similar */}
      {sim.length > 0 && (
        <Section title="Similar runs">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-[#262b33] text-left text-xs text-neutral-400">
                <tr>
                  {['Date', 'Name', 'Dist', 'Pace', 'Δ', 'HR', 'Δ', 'EF', 'Δ'].map((h, i) => (
                    <th key={i} className={`py-1 pr-3 font-normal ${i > 1 ? 'text-right' : ''}`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className={mono}>
                {sim.map((s) => (
                  <tr key={s.id} className="border-b border-[#262b33]">
                    <td className="py-1 pr-3 whitespace-nowrap">{formatDate(s.start_time_utc, s.timezone ?? 'UTC').slice(0, 11)}</td>
                    <td className="max-w-48 truncate pr-3 font-sans">
                      <Link to={`/activities/${s.id}`} className="hover:text-[#4c8dff]">{s.name ?? '—'}</Link>
                    </td>
                    <td className="pr-3 text-right">{s.distance_m != null ? formatDistance(s.distance_m) : '—'}</td>
                    <td className="pr-3 text-right">{formatPace(s.pace_s_per_km)}</td>
                    <td className="pr-3 text-right text-neutral-500">{s.pace_delta_s_per_km != null ? sign(s.pace_delta_s_per_km, `${Math.round(Math.abs(s.pace_delta_s_per_km))}s`) : '—'}</td>
                    <td className="pr-3 text-right">{s.avg_hr != null ? Math.round(s.avg_hr) : '—'}</td>
                    <td className="pr-3 text-right text-neutral-500">{s.hr_delta_bpm != null ? sign(s.hr_delta_bpm, `${Math.round(Math.abs(s.hr_delta_bpm))}`) : '—'}</td>
                    <td className="pr-3 text-right">{s.efficiency_factor?.toFixed(2) ?? '—'}</td>
                    <td className="text-right text-neutral-500">{s.ef_delta != null ? sign(s.ef_delta, Math.abs(s.ef_delta).toFixed(2)) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-neutral-500">Δ = this run minus that run</p>
        </Section>
      )}

      {/* 8. device laps, only when they differ from the km splits */}
      {d.laps.length > 0 && d.laps.length !== d.splits.length && (
        <Section title="Device laps">
          <Splits splits={d.laps} hl={null} bare />
        </Section>
      )}

      {/* 9. notes & tags */}
      <Editor key={a.id} notes={a.notes ?? ''} tags={d.tags} save={patch.mutate} />

      {/* 10. AI report */}
      <ActivityReport detail={d} streams={st} settings={settings.data} similar={sim} ready={!streams.isPending && !similar.isPending} />

      {/* 11. sources */}
      <details className={`${surface} text-sm`}>
        <summary className="cursor-pointer text-neutral-400">Sources &amp; raw data</summary>
        <div className="mt-2 space-y-3 overflow-x-auto">
          <table className="text-xs">
            <thead className="text-left text-neutral-500">
              <tr>
                {['Source', 'External id', 'Status', 'Fetched', 'Mapper', ''].map((h) => (
                  <th key={h} className="pr-3 font-normal">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className={mono}>
              {d.sources.map((s) => (
                <tr key={s.id}>
                  <td className="pr-3">{s.source}</td>
                  <td className="pr-3">{s.external_id}</td>
                  <td className="pr-3">{s.status}</td>
                  <td className="pr-3">{s.fetched_at ?? '—'}</td>
                  <td className="pr-3">v{s.mapper_version ?? '—'}</td>
                  <td>{s.is_primary && 'primary'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.duplicate_candidates.length > 0 && (
            <p className="text-xs text-neutral-400">
              Duplicate candidates:{' '}
              {d.duplicate_candidates.map((c) => (
                <Link key={c.id} to={`/activities/${c.id}`} className="mr-2 text-[#4c8dff]">#{c.id}</Link>
              ))}
            </p>
          )}
          <pre className={`max-h-96 overflow-auto rounded-lg border border-[#262b33] bg-[#111418] p-2 text-xs ${mono}`}>{JSON.stringify(d, null, 2)}</pre>
        </div>
      </details>
    </div>
  )
}

// --- pieces -----------------------------------------------------------------
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={`${surface} space-y-2`}>
      <h2 className="text-[22px] font-semibold tracking-[.02em] text-[#eef1f4] uppercase" style={{ fontFamily: FC }}>{title}</h2>
      {children}
    </section>
  )
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h2 className="mb-1 text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM, color: MUTED }}>{title}</h2>
      <dl className="space-y-0.5">{children}</dl>
    </div>
  )
}

function Stat({ label, value, delta, est, model }: { label: string; value: string; delta?: string; est?: string; model?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-sm">
      <dt className="text-neutral-400">
        {label}
        {est && (
          <abbr title={est} className="ml-1 text-[10px] text-neutral-500 no-underline">
            est.
          </abbr>
        )}
        {model && (
          <abbr title={model} className="ml-1 text-[10px] text-neutral-500 no-underline">
            model
          </abbr>
        )}
      </dt>
      <dd className={`text-right ${mono}`}>
        {value}
        {delta && <span className="ml-1.5 text-xs text-neutral-500">{delta}</span>}
      </dd>
    </div>
  )
}

function Splits({ splits, hl, bare }: { splits: Lap[]; hl: number | null; bare?: boolean }) {
  const paces = splits.map(lapPace)
  const showGap = splits.some((s) => s.gap_speed_ms)
  const avg = median(paces)
  const maxDev = Math.max(1, ...paces.map((p) => (p != null && avg != null ? Math.abs(p - avg) : 0)))
  const hlRef = useRef<HTMLTableRowElement>(null)
  useEffect(() => {
    hlRef.current?.scrollIntoView({ block: 'nearest' })
  }, [hl])
  if (!splits.length) return bare ? null : <p className="text-sm text-neutral-500">No splits.</p>
  return (
    <div className={bare ? '' : 'max-h-[440px] overflow-y-auto'}>
      <table className="w-full text-sm">
        <thead className="sticky top-0 border-b border-[#262b33] bg-[#191d23] text-left text-xs text-neutral-400">
          <tr>
            <th className="py-1 pr-2 font-normal">{bare ? 'Lap' : 'Km'}</th>
            <th className="pr-2 text-right font-normal">Pace</th>
            {showGap && <th className="pr-2 text-right font-normal" title="Grade-adjusted pace (model)">GAP</th>}
            <th className="w-1/3 font-normal" />
            <th className="pr-2 text-right font-normal">HR</th>
            <th className="text-right font-normal">D+</th>
          </tr>
        </thead>
        <tbody className={mono}>
          {splits.map((s, i) => {
            const p = paces[i]
            const dev = p != null && avg != null ? p - avg : 0
            const w = `${(Math.abs(dev) / maxDev) * 50}%`
            return (
              <tr key={s.idx} ref={i === hl ? hlRef : undefined} className={`border-b border-[#262b33] ${i === hl ? 'bg-[#20252c] text-neutral-100' : ''}`}>
                <td className="py-1 pr-2 text-neutral-400">
                  {i + 1}
                  {s.distance_m != null && s.distance_m < 950 && <span className="text-xs text-neutral-500"> ({(s.distance_m / 1000).toFixed(2)})</span>}
                </td>
                <td className="pr-2 text-right">{formatPace(p).replace(' /km', '')}</td>
                {showGap && <td className="pr-2 text-right text-neutral-400">{s.gap_speed_ms ? formatPace(1000 / s.gap_speed_ms).replace(' /km', '') : '—'}</td>}
                <td>
                  {/* bar from the centre (median); right = faster */}
                  <div className="relative h-2" title={p != null && avg != null ? sign(dev, `${Math.round(Math.abs(dev))} s/km vs median`) : undefined}>
                    <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                    <div className="absolute inset-y-0 bg-pace" style={dev <= 0 ? { left: '50%', width: w } : { right: '50%', width: w, opacity: 0.5 }} />
                  </div>
                </td>
                <td className="pr-2 text-right">{s.avg_hr != null ? Math.round(s.avg_hr) : '—'}</td>
                <td className="text-right">{s.elev_gain_m != null ? Math.round(s.elev_gain_m) : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Zones({ secs }: { secs: number[] }) {
  const total = secs.reduce((a, b) => a + b, 0)
  if (!total) return null
  return (
    <Section title="Time in HR zones">
      <div className="flex h-4 w-full overflow-hidden rounded-sm">
        {secs.map((s, i) => (
          <div key={i} style={{ width: `${(s / total) * 100}%`, background: ZONE_COLORS[i] }} title={`Z${i + 1}`} />
        ))}
      </div>
      <div className={`grid grid-cols-5 gap-2 text-xs ${mono}`}>
        {secs.map((s, i) => (
          <div key={i}>
            <span className="text-neutral-400">Z{i + 1}</span> {clock(s)} <span className="text-neutral-500">{Math.round((s / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </Section>
  )
}

function Editor({ notes: n0, tags: t0, save }: { notes: string; tags: string[]; save: (b: Record<string, unknown>) => void }) {
  const [notes, setNotes] = useState(n0)
  const [tags, setTags] = useState(t0.join(', '))
  const parse = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)
  const saveTags = () => {
    if (parse(tags).join(',') !== t0.join(',')) save({ tags: parse(tags) })
  }
  return (
    <Section title="Notes & tags">
      <textarea
        aria-label="Notes"
        rows={3}
        className={`${field} w-full`}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        onBlur={() => notes !== n0 && save({ notes: notes || null })}
      />
      <input
        aria-label="Tags"
        placeholder="tags, comma separated"
        className={`${field} w-full`}
        value={tags}
        onChange={(e) => setTags(e.target.value)}
        onBlur={saveTags}
        onKeyDown={(e) => e.key === 'Enter' && saveTags()}
      />
    </Section>
  )
}

// --- map --------------------------------------------------------------------
function TrackMap({ st, colorBy, cursor }: { st: Streams; colorBy: ColorBy; cursor: RefObject<((i: number | null) => void) | null> }) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<MlMap | null>(null)

  const data = useMemo(() => {
    const lat = st.lat!
    const lng = st.lng!
    // centred moving average: raw 1 Hz samples make the track flicker between hues
    const smooth = (xs: (number | null)[] = [], w = 7) =>
      xs.map((_, i) => {
        const win = xs.slice(Math.max(0, i - w), i + w + 1).filter((x): x is number => x != null)
        return win.length ? win.reduce((a, b) => a + b, 0) / win.length : null
      })
    const vals: Record<ColorBy, (number | null)[]> = {
      pace: smooth(st.speed).map((v) => (v ? Math.min(1000 / v, PACE_CLAMP) : null)),
      hr: smooth(st.hr),
      power: smooth(st.power),
      elev: smooth(st.altitude),
    }
    const pt = (i: number) => (lat[i] != null && lng[i] != null ? [lng[i]!, lat[i]!] : null)
    const feats: Feature[] = []
    for (let i = 1; i < lat.length; i++) {
      const p0 = pt(i - 1)
      const p1 = pt(i)
      if (p0 && p1) feats.push({ type: 'Feature', properties: { pace: vals.pace[i], hr: vals.hr[i], power: vals.power[i], elev: vals.elev[i] }, geometry: { type: 'LineString', coordinates: [p0, p1] } })
    }
    const pts = lat.map((_, i) => pt(i)).filter((p): p is number[] => !!p)
    // first GPS point at or past each whole km
    const km: Feature[] = []
    const dist = st.distance ?? []
    for (let i = 0, next = 1000; i < dist.length; i++) {
      const p = pt(i)
      if (dist[i] == null || dist[i]! < next || !p) continue
      km.push({ type: 'Feature', properties: { km: String(next / 1000) }, geometry: { type: 'Point', coordinates: p } })
      next = (Math.floor(dist[i]! / 1000) + 1) * 1000
    }
    const range = (xs: (number | null)[]) => {
      const v = xs.filter((x): x is number => x != null)
      return v.length ? [pct(v, 0.05), Math.max(pct(v, 0.95), pct(v, 0.05) + 1)] : [0, 1]
    }
    return {
      track: { type: 'FeatureCollection', features: feats } as FeatureCollection,
      ends: {
        type: 'FeatureCollection',
        features: [
          { type: 'Feature', properties: { k: 'start' }, geometry: { type: 'Point', coordinates: pts[0] } },
          { type: 'Feature', properties: { k: 'end' }, geometry: { type: 'Point', coordinates: pts[pts.length - 1] } },
        ],
      } as FeatureCollection,
      km: { type: 'FeatureCollection', features: km } as FeatureCollection,
      pts,
      ranges: { pace: range(vals.pace), hr: range(vals.hr), power: range(vals.power), elev: range(vals.elev) },
    }
  }, [st])

  const color = useCallback(
    (k: ColorBy): ExpressionSpecification => {
      const [lo, hi] = data.ranges[k]
      const ramp = rampOf(k)
      const stops = ramp.flatMap((c, i) => [lo + ((hi - lo) * i) / (ramp.length - 1), c])
      return ['interpolate', ['linear'], ['coalesce', ['get', k], lo], ...stops] as ExpressionSpecification
    },
    [data],
  )

  useEffect(() => {
    const m = new MlMap({ container: el.current!, style: 'https://tiles.openfreemap.org/styles/dark', attributionControl: { compact: true } })
    map.current = m
    const marker = new Marker({ element: Object.assign(document.createElement('div'), { className: 'size-3 rounded-full border-2 border-neutral-100 bg-accent' }) })
    const b = new LngLatBounds()
    for (const p of data.pts) b.extend(p as [number, number])
    m.fitBounds(b, { padding: 24, duration: 0 })
    m.on('load', () => {
      m.addSource('track', { type: 'geojson', data: data.track })
      m.addLayer({ id: 'track', type: 'line', source: 'track', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-width': 3, 'line-color': color(colorBy) } })
      m.addSource('km', { type: 'geojson', data: data.km })
      m.addLayer({ id: 'km', type: 'circle', source: 'km', paint: { 'circle-radius': 8, 'circle-color': '#0a0a0c', 'circle-stroke-color': '#e5e5e5', 'circle-stroke-width': 1.5 } })
      m.addLayer({
        id: 'km-label',
        type: 'symbol',
        source: 'km',
        layout: { 'text-field': ['get', 'km'], 'text-font': ['Noto Sans Regular'], 'text-size': 10, 'text-allow-overlap': true },
        paint: { 'text-color': '#e5e5e5' },
      })
      m.addSource('ends', { type: 'geojson', data: data.ends })
      m.addLayer({
        id: 'ends',
        type: 'circle',
        source: 'ends',
        paint: {
          'circle-radius': 5,
          'circle-color': ['match', ['get', 'k'], 'start', '#e5e5e5', '#0a0a0c'],
          'circle-stroke-color': '#e5e5e5',
          'circle-stroke-width': 2,
        },
      })
    })
    cursor.current = (i) => {
      const lat = i == null ? null : st.lat?.[i]
      const lng = i == null ? null : st.lng?.[i]
      if (lat == null || lng == null) marker.remove()
      else marker.setLngLat([lng, lat]).addTo(m)
    }
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(el.current!)
    return () => {
      cursor.current = null
      ro.disconnect()
      m.remove()
      map.current = null
    }
    // colorBy is applied by the effect below; re-creating the map on toggle is wasteful
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, st, cursor])

  useEffect(() => {
    const m = map.current
    if (m?.getLayer('track')) m.setPaintProperty('track', 'line-color', color(colorBy))
    else m?.once('load', () => m.setPaintProperty('track', 'line-color', color(colorBy)))
  }, [colorBy, color])

  // keep the source in sync if streams refetch without remount
  useEffect(() => {
    ;(map.current?.getSource('track') as GeoJSONSource | undefined)?.setData(data.track)
    ;(map.current?.getSource('km') as GeoJSONSource | undefined)?.setData(data.km)
  }, [data])

  const [lo, hi] = data.ranges[colorBy]
  const fmt = { pace: formatPace, hr: (v: number) => `${Math.round(v)} bpm`, power: (v: number) => `${Math.round(v)} W`, elev: (v: number) => `${Math.round(v)} m` }[colorBy]
  const ramp = rampOf(colorBy)
  return (
    <div className="relative">
      <div ref={el} className="h-60 w-full overflow-hidden rounded-lg xl:h-[420px]" />
      <div className="pointer-events-none absolute top-2 left-2 rounded bg-neutral-950/80 px-2 py-1 text-[10px] text-neutral-300 tabular-nums">
        <div className="h-1.5 w-32 rounded" style={{ background: `linear-gradient(to right, ${ramp.join(',')})` }} />
        <div className="mt-0.5 flex justify-between">
          <span>{fmt(lo)}</span>
          <span>{fmt(hi)}</span>
        </div>
      </div>
    </div>
  )
}

// --- charts -----------------------------------------------------------------
type Series = { mode: 'distance' | 'time'; x: number[]; pace: (number | null)[] }

function Charts({ st, series, settings, onHover }: { st: Streams; series: Series; settings?: Settings; onHover: (v: number | null) => void }) {
  const options = useMemo(() => {
    const { x, mode } = series
    const xFmt = (v: number) => (mode === 'distance' ? `${v.toFixed(2)} km` : clock(v))
    const axisX = (v: number) => (mode === 'distance' ? `${+v.toFixed(1)}` : clock(v))
    const mk = (name: string, y: (number | null)[], color: string, fmt: (v: number) => string, extra: { area?: boolean; inverse?: boolean; max?: (v: { max: number }) => number; bands?: [number, number, string][] } = {}): EChartsOption => ({
      animation: false,
      backgroundColor: 'transparent',
      textStyle: { color: C.text, fontFamily: 'ui-monospace, monospace' },
      title: { text: name, textStyle: { color: C.text, fontSize: 11, fontWeight: 'normal' }, left: 0, top: 0 },
      grid: { left: 44, right: 8, top: 22, bottom: 22 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#121216',
        borderColor: C.grid,
        textStyle: { color: '#e5e5e5', fontFamily: 'ui-monospace, monospace', fontSize: 11 },
        axisPointer: { type: 'line', lineStyle: { color: '#737373' } },
        formatter: (ps) => {
          const p = (Array.isArray(ps) ? ps[0] : ps) as unknown as { value: [number, number | null] }
          return `${xFmt(p.value[0])}<br/>${name} ${p.value[1] == null ? '—' : fmt(p.value[1])}`
        },
      },
      xAxis: { type: 'value', min: 'dataMin', max: 'dataMax', axisLabel: { formatter: axisX }, splitLine: { show: false }, axisLine: { lineStyle: { color: C.grid } } },
      yAxis: { type: 'value', scale: true, inverse: extra.inverse, min: extra.inverse ? (v: { min: number }) => Math.floor(v.min - 15) : undefined, max: extra.max, splitLine: { lineStyle: { color: C.grid } }, axisLabel: { formatter: (v: number) => fmt(v) } },
      dataZoom: [{ type: 'inside', throttle: 16 }],
      series: [
        {
          type: 'line',
          name,
          data: x.map((v, i) => [v, y[i] ?? null]),
          showSymbol: false,
          sampling: 'lttb',
          lineStyle: { color, width: 1.25 },
          itemStyle: { color },
          areaStyle: extra.area ? { color, opacity: 0.25 } : undefined,
          markArea: extra.bands && {
            silent: true,
            data: extra.bands.map(([lo, hi, c]) => [{ yAxis: lo, itemStyle: { color: c, opacity: 0.15 } }, { yAxis: hi }]),
          },
        },
      ],
    })

    const out: [string, EChartsOption][] = []
    if (has(series.pace)) out.push(['pace', mk('Pace', series.pace, C.pace, (v) => formatPace(v).replace(' /km', ''), { inverse: true, max: (v) => Math.min(v.max, CHART_PACE_MAX) + 15 })])
    if (has(st.hr)) {
      const z = settings?.hr_zones
      const hrs = st.hr!.filter((v): v is number => v != null)
      const lo = Math.min(...hrs)
      const hi = Math.max(settings?.hr_max ?? 0, ...hrs)
      const edges = z?.length === 4 ? [lo, ...z, hi] : null
      const bands = edges?.slice(0, 5).map((e, i): [number, number, string] => [e, edges[i + 1], ZONE_COLORS[i]]).filter(([a, b]) => b > a)
      out.push(['hr', mk('HR', st.hr!, C.hr, (v) => `${Math.round(v)}`, { bands })])
    }
    if (has(st.altitude)) out.push(['elev', mk('Elevation', st.altitude!, C.elev, (v) => `${Math.round(v)} m`, { area: true })])
    if (has(st.cadence)) out.push(['cad', mk('Cadence', st.cadence!, C.cad, (v) => `${Math.round(v)}`)])
    if (has(st.power)) out.push(['pow', mk('Power', st.power!, C.pow, (v) => `${Math.round(v)} W`)])
    return out
  }, [st, series, settings])

  useEffect(() => {
    echarts.connect(GROUP)
    return () => echarts.disconnect(GROUP)
  }, [])

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {options.map(([k, o]) => (
        <Chart key={k} option={o} onHover={onHover} />
      ))}
    </div>
  )
}

function Chart({ option, onHover }: { option: EChartsOption; onHover: (v: number | null) => void }) {
  const el = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const c = echarts.init(el.current!)
    c.group = GROUP
    echarts.connect(GROUP)
    inst.current = c
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(el.current!)
    return () => {
      ro.disconnect()
      c.dispose()
      inst.current = null
    }
  }, [])

  useEffect(() => {
    inst.current?.setOption(option, true)
  }, [option])

  useEffect(() => {
    const c = inst.current
    if (!c) return
    const h = (e: unknown) => {
      const v = (e as { axesInfo?: { value: number }[] }).axesInfo?.[0]?.value
      onHover(typeof v === 'number' ? v : null)
    }
    const out = () => onHover(null)
    c.on('updateAxisPointer', h)
    c.getZr().on('globalout', out)
    return () => {
      // the init effect's cleanup runs first on unmount and disposes the chart
      if (c.isDisposed()) return
      c.off('updateAxisPointer', h)
      c.getZr().off('globalout', out)
    }
  }, [onHover])

  return <div ref={el} className="h-48 w-full" />
}
