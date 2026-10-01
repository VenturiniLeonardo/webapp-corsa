import { keepPreviousData, useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { formatDuration, formatPace } from '../utils/formatters'
import { iso, PRESETS, presetFrom } from '../utils/period'

// --- API shapes (app/api/stats.py) ------------------------------------------
type Totals = {
  run_count: number
  distance_m: number
  moving_s: number
  elev_gain_m: number
  longest_run_m: number
  weighted_pace_s_per_km: number | null
}
type PeriodTotals = Totals & { avg_weekly_distance_m: number }
type Summary = { current: PeriodTotals; previous: PeriodTotals }
type BucketTotals = Totals & { start: string; end: string; partial: boolean }
type Volume = { bucket: Gran; items: BucketTotals[] }
type Calendar = { year: number; days: { date: string; run_count: number; distance_m: number }[] }
type Trends = {
  n: number
  points: { activity_id: number; date: string; value: number; rolling_median: number }[]
  trend: { slope_per_day: number; span_days: number } | null
}
type Zones = { totals_s: number[]; items: { start: string; partial: boolean; zones_s: number[] }[] }
type Dist = { n: number; buckets: { lo: number; hi: number | null; count: number }[] }
type PaceHr = { n: number; points: { activity_id: number; date: string; pace_s_per_km: number; avg_hr: number; moving_s: number }[] }
type TopWeek = Totals & { start: string; end: string }
type Effort = { activity_id: number; local_date: string; elapsed_s: number; workout_type: string | null }
type RecordRow = { label: string; progression: Effort[] }

type Gran = 'week' | 'month'
type VolMetric = 'km' | 'time' | 'elev' | 'runs'

const C = { pace: '#3b82f6', hr: '#ef4444', elev: '#6b7280', vol: '#a1a1aa', line: '#e5e5e5', grid: '#22222a', text: '#a3a3a3', panel: '#121216' }
const PACE_CLAMP = 600 // 10:00/km (PLAN §12.2)
const MIN_TREND_N = 8
const DAY = 86_400_000
const mono = 'font-mono tabular-nums'
const field = 'rounded border border-border bg-panel px-2 py-1 text-sm'
const ZC = ['#64748b', '#3b82f6', '#eab308', '#f97316', '#ef4444'] // Z1..Z5
const D10 = ['1K', '5K', '10K', 'Half Marathon']
const km = (m: number) => m / 1000

// --- helpers ----------------------------------------------------------------
const hm = (s: number) => `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
const clock = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(Math.round(s % 60)).padStart(2, '0')}` : mmss(s))
const signed =(d: number, fmt: (x: number) => string) => `${d < 0 ? '−' : '+'}${fmt(Math.abs(d))}`
function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b)
  const m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}
const autoGran = (from: string | undefined, to: string): Gran =>
  !from || (Date.parse(to) - Date.parse(from)) / DAY > 190 ? 'month' : 'week'

const base: EChartsOption = {
  animation: false,
  backgroundColor: 'transparent',
  textStyle: { color: C.text, fontFamily: 'ui-monospace, monospace', fontSize: 11 },
  grid: { left: 44, right: 44, top: 16, bottom: 24 },
  tooltip: { trigger: 'axis', backgroundColor: C.panel, borderColor: C.grid, textStyle: { color: '#e5e5e5' } },
}
const splitLine = { lineStyle: { color: C.grid } }

// --- page -------------------------------------------------------------------
export default function DashboardPage() {
  const [sp, setSp] = useSearchParams()
  const set = (kv: Record<string, string | null>) =>
    setSp((prev) => {
      const n = new URLSearchParams(prev)
      for (const [k, v] of Object.entries(kv)) {
        if (v) n.set(k, v)
        else n.delete(k)
      }
      return n
    })

  const custom = sp.has('from') || sp.has('to')
  const preset = custom ? '' : (sp.get('r') ?? '12W')
  const from = custom ? sp.get('from') || undefined : presetFrom(preset)
  const to = sp.get('to') || undefined
  const gran = (sp.get('g') as Gran | null) ?? autoGran(from, to ?? iso(new Date()))

  const scope = new URLSearchParams()
  if (from) scope.set('from_date', from)
  if (to) scope.set('to_date', to)
  const q = <T,>(path: string, extra: Record<string, string> = {}, enabled = true) => {
    const qs = new URLSearchParams(scope)
    for (const [k, v] of Object.entries(extra)) qs.set(k, v)
    return { queryKey: [path, qs.toString()], queryFn: () => api<T>(`${path}?${qs}`), placeholderData: keepPreviousData, enabled }
  }

  const volume = useQuery(q<Volume>('/api/stats/volume', { bucket: gran }))
  const weekly = useQuery(q<Volume>('/api/stats/volume', { bucket: 'week' }))
  // "All" has no start date: anchor the summary on the first bucket so avg km/wk spans real history
  const sumFrom = from ?? volume.data?.items[0]?.start
  const summary = useQuery(q<Summary>('/api/stats/summary', sumFrom ? { from_date: sumFrom } : {}, !!sumFrom))
  const pace = useQuery(q<Trends>('/api/stats/trends', { metric: 'pace' }))
  const ef = useQuery(q<Trends>('/api/stats/trends', { metric: 'ef' }))
  const paceHr = useQuery(q<PaceHr>('/api/stats/pace-hr'))
  const zones = useQuery(q<Zones>('/api/stats/zones', { bucket: 'week' }))
  const dist = useQuery(q<Dist>('/api/stats/distribution', { field: 'distance' }))
  const top = useQuery(q<TopWeek[]>('/api/stats/top-weeks', { limit: '10' }))
  const records = useQuery({ queryKey: ['/api/records'], queryFn: () => api<RecordRow[]>('/api/records') })

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1">
          {PRESETS.map((p) => (
            <button
              key={p}
              aria-pressed={preset === p}
              onClick={() => set({ r: p, from: null, to: null, g: null })}
              className={`${field} min-h-10 md:min-h-0 ${preset === p ? 'border-accent text-accent' : 'text-neutral-400'}`}
            >
              {p}
            </button>
          ))}
        </div>
        <input type="date" aria-label="From" className={`${field} ${mono}`} value={from ?? ''} onChange={(e) => set({ from: e.target.value || null, r: null, g: null })} />
        <input type="date" aria-label="To" className={`${field} ${mono}`} value={to ?? ''} onChange={(e) => set({ to: e.target.value || null, r: null, g: null })} />
        <Toggle value={gran} options={['week', 'month']} onChange={(g) => set({ g })} />
      </div>

      <SummaryStrip data={summary.data} showDelta={!!from} busy={summary.isFetching} />
      {(volume.isError || summary.isError) && <p className="text-sm text-red-400">Failed to load stats.</p>}

      <div className="grid gap-x-8 gap-y-6 xl:grid-cols-2">
        <VolumeChart data={volume.data} />
        <LongRunChart data={weekly.data} />
        <Section title="Consistency" help="Daily km over the last 12 months (ISO weeks, local date)." wide>
          <Heatmap />
        </Section>
        <PaceChart data={pace.data} />
        <EfChart data={ef.data} />
        <PaceHrChart data={paceHr.data} />
        <ZoneChart data={zones.data} />
        <DistChart data={dist.data} />
        <BestEffortChart data={records.data} />
        <TopWeeks data={top.data} />
      </div>
    </div>
  )
}

// --- D1 -----------------------------------------------------------------------
function SummaryStrip({ data, showDelta, busy }: { data?: Summary; showDelta: boolean; busy: boolean }) {
  if (!data) return <p className={`text-sm text-neutral-500 ${mono}`}>…</p>
  const { current: c, previous: p } = data
  const d = (x: ReactNode) => (showDelta ? <span className="ml-1 text-xs text-neutral-500">{x}</span> : null)
  const f1 = (x: number) => x.toFixed(1)
  const paceDelta = c.weighted_pace_s_per_km != null && p.weighted_pace_s_per_km != null ? c.weighted_pace_s_per_km - p.weighted_pace_s_per_km : null
  const items: [ReactNode, ReactNode][] = [
    [`${f1(km(c.distance_m))} km`, signed(km(c.distance_m - p.distance_m), f1)],
    [hm(c.moving_s), signed(c.moving_s - p.moving_s, hm)],
    [`${c.run_count} runs`, signed(c.run_count - p.run_count, String)],
    [`${f1(km(c.avg_weekly_distance_m))} km/wk`, signed(km(c.avg_weekly_distance_m - p.avg_weekly_distance_m), f1)],
    [`longest ${f1(km(c.longest_run_m))} km`, signed(km(c.longest_run_m - p.longest_run_m), f1)],
    [formatPace(c.weighted_pace_s_per_km), paceDelta == null ? '—' : signed(paceDelta, mmss)],
  ]
  return (
    <p className={`flex flex-wrap gap-x-4 gap-y-1 text-sm text-neutral-200 ${mono} ${busy ? 'opacity-60' : ''}`} title={showDelta ? 'Δ vs the previous period of the same number of elapsed days' : undefined}>
      {items.map(([v, delta], i) => (
        <span key={i}>
          {v}
          {d(delta)}
        </span>
      ))}
    </p>
  )
}

// --- D2 -----------------------------------------------------------------------
const VOL: Record<VolMetric, { label: string; get: (b: BucketTotals) => number; fmt: (v: number) => string; color: string }> = {
  km: { label: 'km', get: (b) => km(b.distance_m), fmt: (v) => `${v.toFixed(1)} km`, color: C.vol },
  time: { label: 'time', get: (b) => b.moving_s / 3600, fmt: (v) => hm(v * 3600), color: C.vol },
  elev: { label: 'D+', get: (b) => b.elev_gain_m, fmt: (v) => `${Math.round(v)} m`, color: C.elev },
  runs: { label: 'runs', get: (b) => b.run_count, fmt: (v) => `${v} runs`, color: C.vol },
}

function VolumeChart({ data }: { data?: Volume }) {
  const [metric, setMetric] = useState<VolMetric>('km')
  const m = VOL[metric]
  const items = data?.items ?? []
  const vals = items.map(m.get)
  // trailing 4-bucket mean over complete buckets only, so a partial week doesn't drag it down
  const ma = data?.bucket === 'week' ? items.map((b, i) => (b.partial || i < 3 || items.slice(i - 3, i + 1).some((x) => x.partial) ? null : vals.slice(i - 3, i + 1).reduce((a, x) => a + x, 0) / 4)) : null
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, valueFormatter: (v) => (v == null ? '—' : m.fmt(Number(v))) },
    xAxis: { type: 'category', data: items.map((b) => b.start), axisLine: splitLine },
    yAxis: { type: 'value', splitLine, axisLabel: { formatter: metric === 'time' ? (v: number) => `${v}h` : undefined } },
    series: [
      {
        name: m.label,
        type: 'bar',
        data: items.map((b, i) => ({
          value: vals[i],
          itemStyle: b.partial ? { color: 'transparent', borderColor: m.color, borderType: 'dashed', borderWidth: 1 } : { color: m.color },
        })),
      },
      ...(ma ? [{ name: '4-wk avg', type: 'line' as const, data: ma, showSymbol: false, lineStyle: { color: C.line, width: 1.5 }, itemStyle: { color: C.line } }] : []),
    ],
  }
  return (
    <Section
      title="Volume"
      help="Sum per ISO week / calendar month. Dashed bar = partial bucket (in progress or cut by the period). Line = mean of the last 4 complete weeks."
      aside={<Toggle value={metric} options={Object.keys(VOL) as VolMetric[]} label={(k) => VOL[k].label} onChange={setMetric} />}
    >
      {items.length ? <Chart option={option} /> : <Empty />}
    </Section>
  )
}

// --- D4 -----------------------------------------------------------------------
function LongRunChart({ data }: { data?: Volume }) {
  const items = data?.items ?? []
  const option: EChartsOption = {
    ...base,
    xAxis: { type: 'category', data: items.map((b) => b.start), axisLine: splitLine },
    yAxis: [
      { type: 'value', splitLine, axisLabel: { formatter: '{value} km' } },
      { type: 'value', max: 100, splitLine: { show: false }, axisLabel: { formatter: '{value}%' } },
    ],
    series: [
      { name: 'longest', type: 'line', data: items.map((b) => +km(b.longest_run_m).toFixed(2)), lineStyle: { color: C.line }, itemStyle: { color: C.line }, tooltip: { valueFormatter: (v) => `${v} km` } },
      {
        name: '% of week',
        type: 'line',
        yAxisIndex: 1,
        data: items.map((b) => (b.distance_m > 0 ? Math.round((b.longest_run_m / b.distance_m) * 100) : null)),
        lineStyle: { color: C.vol, type: 'dashed' },
        itemStyle: { color: C.vol },
        symbol: 'rect',
        tooltip: { valueFormatter: (v) => (v == null ? '—' : `${v}%`) },
      },
    ],
  }
  return (
    <Section title="Weekly long run" help="Longest single run per ISO week, and its share of that week's km. Weeks with no long run read low; that is not a regression.">
      {items.length ? <Chart option={option} /> : <Empty />}
    </Section>
  )
}

// --- D3 -----------------------------------------------------------------------
function Heatmap() {
  const end = new Date()
  const start = new Date(end.getTime() - 364 * DAY)
  const years = [...new Set([start.getFullYear(), end.getFullYear()])]
  const cal = useQuery({
    queryKey: ['calendar', years],
    queryFn: () => Promise.all(years.map((y) => api<Calendar>(`/api/stats/calendar?year=${y}`))),
  })
  if (!cal.data) return <p className="text-sm text-neutral-500">{cal.isError ? 'Failed to load.' : 'Loading…'}</p>
  const days = cal.data.flatMap((c) => c.days).map((d) => [d.date, +km(d.distance_m).toFixed(2)] as [string, number])
  const max = Math.max(10, ...days.map((d) => d[1]))
  const option: EChartsOption = {
    ...base,
    tooltip: { backgroundColor: C.panel, borderColor: C.grid, textStyle: { color: '#e5e5e5' }, formatter: (p) => { const [d, v] = (p as unknown as { value: [string, number] }).value; return `${d}<br/>${v.toFixed(1)} km` } },
    visualMap: { show: false, min: 0.01, max, inRange: { color: ['#27272a', '#52525b', '#a1a1aa', '#f4f4f5'] }, outOfRange: { color: C.panel } },
    calendar: {
      range: [iso(start), iso(end)],
      top: 20,
      left: 28,
      right: 4,
      bottom: 4,
      cellSize: ['auto', 'auto'],
      itemStyle: { color: C.panel, borderColor: '#0a0a0c', borderWidth: 2 },
      splitLine: { show: false },
      dayLabel: { firstDay: 1, color: C.text, nameMap: ['S', 'M', 'T', 'W', 'T', 'F', 'S'], fontSize: 9 },
      monthLabel: { color: C.text, fontSize: 10 },
      yearLabel: { show: false },
    },
    series: [{ type: 'heatmap', coordinateSystem: 'calendar', data: days }],
  }
  return <Chart option={option} className="h-36" />
}

// --- D5 / D6 ------------------------------------------------------------------
function trendSeries(t: Trends, color: string, fmt: (v: number) => string, clamp?: number): EChartsOption['series'] {
  const v = (x: number) => (clamp ? Math.min(x, clamp) : x)
  return [
    { name: 'steady run', type: 'scatter', symbolSize: 6, itemStyle: { color, opacity: 0.55 }, data: t.points.map((p) => [p.date, v(p.value)]), tooltip: { valueFormatter: (x) => fmt(Number(x)) } },
    { name: '28-day median', type: 'line', showSymbol: false, lineStyle: { color, width: 2 }, itemStyle: { color }, data: t.points.map((p) => [p.date, v(p.rolling_median)]), tooltip: { valueFormatter: (x) => fmt(Number(x)) } },
  ]
}

function PaceChart({ data }: { data?: Trends }) {
  const option: EChartsOption | null = data?.n
    ? {
        ...base,
        tooltip: { ...base.tooltip, trigger: 'item' },
        xAxis: { type: 'time', axisLine: splitLine, splitLine: { show: false } },
        yAxis: { type: 'value', inverse: true, scale: true, max: (e) => Math.min(e.max, PACE_CLAMP), splitLine, axisLabel: { formatter: (v: number) => mmss(v) } },
        series: trendSeries(data, C.pace, (x) => formatPace(x), PACE_CLAMP),
      }
    : null
  return (
    <Section
      title="Pace"
      n={data?.n}
      help="Steady runs only (outdoor, ≥ 20 min, with HR, low pace variability, not workout/race). Line = median of steady runs in the trailing 28 days. Axis inverted: faster is higher; clamped at 10:00/km."
    >
      {option ? <Chart option={option} /> : <Empty what="steady runs" />}
    </Section>
  )
}

function EfChart({ data }: { data?: Trends }) {
  const n = data?.n ?? 0
  let label: string | null = null
  const series = data && n ? [...(trendSeries(data, C.hr, (x) => x.toFixed(3)) as object[])] : []
  if (data && n) {
    if (data.trend) {
      const { slope_per_day: k, span_days: span } = data.trend
      const t = (d: string) => Date.parse(d) / DAY
      const fit = data.points.slice(-500)
      const x0 = t(fit[0].date)
      const b = median(fit.map((p) => p.value - k * (t(p.date) - x0)))
      const pct = (k * span * 100) / b
      label = `${pct < 0 ? '−' : '+'}${Math.abs(pct).toFixed(1)}% in ${Math.max(1, Math.round(span / 7))} weeks`
      series.push({
        name: 'Theil-Sen',
        type: 'line',
        showSymbol: false,
        lineStyle: { color: C.line, type: 'dashed', width: 1 },
        itemStyle: { color: C.line },
        data: [[fit[0].date, b], [fit[fit.length - 1].date, b + k * span]],
        tooltip: { show: false },
      })
    } else label = `trend hidden: needs ≥ ${MIN_TREND_N} steady runs`
  }
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item' },
    xAxis: { type: 'time', axisLine: splitLine, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, splitLine, axisLabel: { formatter: (v: number) => v.toFixed(2) } },
    series: series as EChartsOption['series'],
  }
  return (
    <Section
      title="Aerobic efficiency (EF)"
      n={data?.n}
      extra={label}
      help="EF = moving speed (m/min) / avg HR, steady runs only; higher = faster per heartbeat. Line = trailing 28-day median. Dashed = Theil-Sen slope (median of pairwise slopes), hidden when n < 8. Heat and humidity raise HR."
    >
      {n ? <Chart option={option} /> : <Empty what="steady runs" />}
    </Section>
  )
}

// --- D7 -----------------------------------------------------------------------
function PaceHrChart({ data }: { data?: PaceHr }) {
  const pts = (data?.points ?? []).map((p) => [Math.min(p.pace_s_per_km, PACE_CLAMP), p.avg_hr, Date.parse(p.date), p.moving_s])
  const ts = pts.map((p) => p[2])
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item', formatter: (p) => { const [x, y, t, mv] = (p as unknown as { value: number[] }).value; return `${iso(new Date(t))}<br/>${formatPace(x)} · ${Math.round(y)} bpm · ${formatDuration(mv)}` } },
    visualMap: { show: false, dimension: 2, min: Math.min(...ts), max: Math.max(...ts), inRange: { color: ['#1e2a40', '#60a5fa'] } },
    xAxis: { type: 'value', inverse: true, scale: true, max: (e) => Math.min(e.max, PACE_CLAMP), axisLine: splitLine, splitLine: { show: false }, axisLabel: { formatter: (v: number) => mmss(v) } },
    yAxis: { type: 'value', scale: true, splitLine },
    series: [{ type: 'scatter', symbolSize: 7, data: pts }],
  }
  return (
    <Section
      title="Pace vs HR"
      n={data?.n}
      extra="older → newer"
      help="Steady runs: avg pace vs avg HR. Colour = date (faded = older). Down-right = faster at lower HR. Runs of different length are mixed: cardiac drift grows with duration. Axis inverted, clamped at 10:00/km."
    >
      {pts.length ? <Chart option={option} /> : <Empty what="steady runs" />}
    </Section>
  )
}

// --- D8 -----------------------------------------------------------------------
function ZoneChart({ data }: { data?: Zones }) {
  const items = data?.items ?? []
  const tot = data?.totals_s ?? []
  const all = tot.reduce((a, x) => a + x, 0)
  const extra = all ? tot.map((x, i) => `Z${i + 1} ${((x / all) * 100).toFixed(1)}%`).join(' · ') : null
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, valueFormatter: (v) => `${Math.round(Number(v))} min` },
    xAxis: { type: 'category', data: items.map((b) => b.start), axisLine: splitLine },
    yAxis: { type: 'value', splitLine, axisLabel: { formatter: (v: number) => `${Math.round(v / 60)}h` } },
    series: ZC.map((color, i) => ({
      name: `Z${i + 1}`,
      type: 'bar' as const,
      stack: 'z',
      itemStyle: { color },
      data: items.map((b) => +(b.zones_s[i] / 60).toFixed(1)),
    })),
  }
  return (
    <Section
      title="Time in zones"
      extra={extra}
      help="Time per HR zone (Z1–Z5) per ISO week, from runs with HR; the percentages are the share of the whole period and sum to 100%. Entirely dependent on the max HR set in Settings."
    >
      {all ? <Chart option={option} /> : <Empty what="HR zone data" />}
    </Section>
  )
}

// --- D9 -----------------------------------------------------------------------
function DistChart({ data }: { data?: Dist }) {
  const b = data?.buckets ?? []
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, valueFormatter: (v) => `${v} runs` },
    xAxis: { type: 'category', data: b.map((x) => (x.hi == null ? `${km(x.lo)}+` : `${km(x.lo)}–${km(x.hi)}`)), axisLine: splitLine },
    yAxis: { type: 'value', minInterval: 1, splitLine },
    series: [{ type: 'bar', data: b.map((x) => x.count), itemStyle: { color: C.vol }, label: { show: true, position: 'top', color: C.text } }],
  }
  return (
    <Section title="Run distances (km)" n={data?.n} help="Number of runs per distance class, [lo, hi) in km. Counts, not percentages: n is small.">
      {data?.n ? <Chart option={option} /> : <Empty />}
    </Section>
  )
}

// --- D10 ----------------------------------------------------------------------
function BestEffortChart({ data }: { data?: RecordRow[] }) {
  const [label, setLabel] = useState(D10[1])
  const prog = data?.find((r) => r.label === label)?.progression ?? []
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item', formatter: (p) => { const [d, s] = (p as unknown as { value: [string, number] }).value; return `${d}<br/>${clock(s)}` } },
    xAxis: { type: 'time', axisLine: splitLine, splitLine: { show: false } },
    yAxis: { type: 'value', inverse: true, scale: true, splitLine, axisLabel: { formatter: (v: number) => clock(v) } },
    series: [
      { type: 'line', step: 'end', showSymbol: false, silent: true, lineStyle: { color: C.pace, width: 2 }, data: prog.map((e) => [e.local_date, e.elapsed_s]) },
      { type: 'scatter', itemStyle: { color: C.pace }, data: prog.map((e) => ({ value: [e.local_date, e.elapsed_s], symbol: e.workout_type === 'race' ? 'diamond' : 'circle', symbolSize: e.workout_type === 'race' ? 11 : 7 })) },
    ],
  }
  return (
    <Section
      title="Best efforts"
      n={prog.length}
      aside={<Toggle value={label} options={D10} onChange={setLabel} />}
      help="Each point is a new all-time PR at that distance (strictly faster than every earlier effort); the step line is the PR at each date. Diamond = race. A best effort inside a training run is not a race; GPS error can inflate or deflate distance. Not affected by the period."
    >
      {prog.length ? <Chart option={option} /> : <Empty what="best efforts" />}
    </Section>
  )
}

// --- D11 ----------------------------------------------------------------------
function TopWeeks({ data }: { data?: TopWeek[] }) {
  const th = 'px-2 py-1 font-normal'
  const td = 'px-2 py-1'
  return (
    <Section title="Top weeks" help="The 10 ISO weeks with the most km in the period. Click a week to list its runs.">
      {data?.length ? (
        <div className="overflow-x-auto">
          <table className={`w-full text-sm ${mono}`}>
            <thead className="text-xs text-neutral-500">
              <tr className="border-b border-border text-right">
                <th className={`${th} text-left`}>Week</th>
                <th className={th}>km</th>
                <th className={th}>runs</th>
                <th className={th}>time</th>
                <th className={th}>pace</th>
                <th className={th}>D+</th>
              </tr>
            </thead>
            <tbody>
              {data.map((w) => (
                <tr key={w.start} className="border-b border-border text-right text-neutral-200">
                  <td className={`${td} text-left`}>
                    <Link to={`/activities?from=${w.start}&to=${w.end}`} className="text-accent hover:underline">
                      {w.start}
                    </Link>
                  </td>
                  <td className={td}>{km(w.distance_m).toFixed(1)}</td>
                  <td className={td}>{w.run_count}</td>
                  <td className={td}>{hm(w.moving_s)}</td>
                  <td className={td}>{formatPace(w.weighted_pace_s_per_km)}</td>
                  <td className={td}>{Math.round(w.elev_gain_m)} m</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty />
      )}
    </Section>
  )
}

// --- primitives ----------------------------------------------------------------
function Section({ title, help, n, extra, aside, wide, children }: { title: string; help: string; n?: number; extra?: string | null; aside?: ReactNode; wide?: boolean; children: ReactNode }) {
  return (
    <section className={`min-w-0 ${wide ? 'xl:col-span-2' : ''}`}>
      <div className="mb-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-sm font-medium text-neutral-200">{title}</h2>
        <abbr title={help} className="cursor-help text-xs text-neutral-500 no-underline">
          How is this computed?
        </abbr>
        {extra && <span className={`text-xs text-neutral-300 ${mono}`}>{extra}</span>}
        {n != null && <span className={`text-xs text-neutral-500 ${mono}`}>n={n}</span>}
        {aside && <div className="ml-auto">{aside}</div>}
      </div>
      {children}
    </section>
  )
}

function Toggle<T extends string>({ value, options, onChange, label = (x) => x }: { value: T; options: T[]; onChange: (v: T) => void; label?: (v: T) => string }) {
  return (
    <div className="flex rounded border border-border" role="group">
      {options.map((o) => (
        <button key={o} aria-pressed={value === o} onClick={() => onChange(o)} className={`min-h-10 px-2 text-xs md:min-h-0 md:py-1 ${value === o ? 'bg-panel text-accent' : 'text-neutral-400'}`}>
          {label(o)}
        </button>
      ))}
    </div>
  )
}

const Empty = ({ what = 'runs' }: { what?: string }) => <p className="py-8 text-sm text-neutral-500">No {what} in this period.</p>

function Chart({ option, className = 'h-56' }: { option: EChartsOption; className?: string }) {
  const el = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)
  useEffect(() => {
    const c = echarts.init(el.current!)
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
  return <div ref={el} className={`w-full ${className}`} />
}
