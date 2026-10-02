import { keepPreviousData, useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import { Info } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import AiPanel from '../components/AiPanel'
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

type Fitness = {
  vo2max: { vdot: number | null; vdot_source: string | null; vdot_date: string | null; hr_based: number | null; hr_based_n: number }
  predictions: { label: string; seconds: number }[]
  ctl: number | null
  atl: number | null
  tsb: number | null
  acwr: number | null
  monotony: number | null
  strain: number | null
  ramp_pct: number | null
  alerts: { level: 'info' | 'warn' | 'high'; code: string; message: string }[]
  phase: 'load1' | 'load2' | 'load3' | 'deload' | 'race_week' | 'post_race' | null
}
type CadenceBands = {
  n: number
  bands: { lo: number | null; hi: number | null; n: number; median_spm: number | null; trend: { slope_per_day: number } | null }[]
  points: { activity_id: number; date: string; pace_s_per_km: number; cadence_spm: number; band: number }[]
}

type Gran = 'week' | 'month'

const C = { pace: '#3b82f6', hr: '#ef4444', cad: '#a855f7', elev: '#6b7280', vol: '#10b981', line: '#e5e5e5', grid: '#1f1f26', text: '#737373', panel: '#121216' }
const PACE_CLAMP = 600 // 10:00/km (PLAN §12.2)
const MIN_TREND_N = 8
const DAY = 86_400_000
const mono = 'font-mono tabular-nums'
const MESI = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic']
const GG = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
const card = 'rounded-xl border border-border bg-panel p-4'
const field = 'rounded-md border border-border bg-panel px-2 py-1 text-sm'
const D10 = ['1K', '5K', '10K', 'Half Marathon']
const km = (m: number) => m / 1000

// --- helpers ----------------------------------------------------------------
const hm = (s: number) => `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
const clock = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(Math.round(s % 60)).padStart(2, '0')}` : mmss(s))
function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b)
  const m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

const base: EChartsOption = {
  animation: false,
  backgroundColor: 'transparent',
  textStyle: { color: C.text, fontFamily: 'ui-monospace, monospace', fontSize: 11 },
  grid: { left: 44, right: 44, top: 16, bottom: 24 },
  tooltip: { trigger: 'axis', backgroundColor: '#18181d', borderColor: '#2a2a33', borderRadius: 8, padding: [6, 10], textStyle: { color: '#e5e5e5' } },
}
const splitLine = { lineStyle: { color: C.grid, type: 'dashed' as const } }
const axisLine = { lineStyle: { color: C.grid } }
const timeAxis = { type: 'time' as const, axisLine, splitLine: { show: false }, axisLabel: { hideOverlap: true } }

// --- page -------------------------------------------------------------------
const PRESET_LABEL: Record<string, string> = { Week: 'Settimana corrente', '4W': 'Last 4 weeks', '12W': 'Last 12 weeks', '6M': 'Last 6 months', YTD: 'Year to date', '1Y': 'Last 12 months', All: 'All time' }

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
  const [showCustom, setShowCustom] = useState(custom)
  const preset = custom ? '' : (sp.get('r') ?? '12W')
  const from = custom ? sp.get('from') || undefined : presetFrom(preset)
  const to = sp.get('to') || undefined

  const scope = new URLSearchParams()
  if (from) scope.set('from_date', from)
  if (to) scope.set('to_date', to)
  const q = <T,>(path: string, extra: Record<string, string> = {}, enabled = true) => {
    const qs = new URLSearchParams(scope)
    for (const [k, v] of Object.entries(extra)) qs.set(k, v)
    return { queryKey: [path, qs.toString()], queryFn: () => api<T>(`${path}?${qs}`), placeholderData: keepPreviousData, enabled }
  }

  const monthly = useQuery(q<Volume>('/api/stats/volume', { bucket: 'month' }))
  const weekly = useQuery(q<Volume>('/api/stats/volume', { bucket: 'week' }))
  // "All" has no start date: anchor the summary on the first bucket so avg km/wk spans real history
  const sumFrom = from ?? monthly.data?.items[0]?.start
  const summary = useQuery(q<Summary>('/api/stats/summary', sumFrom ? { from_date: sumFrom } : {}, !!sumFrom))
  const pace = useQuery(q<Trends>('/api/stats/trends', { metric: 'pace' }))
  const gap = useQuery(q<Trends>('/api/stats/trends', { metric: 'gap' }))
  const ef = useQuery(q<Trends>('/api/stats/trends', { metric: 'ef' }))
  const efAdj = useQuery(q<Trends>('/api/stats/trends', { metric: 'ef_adj' }))
  const hrRef = useQuery(q<Trends>('/api/stats/trends', { metric: 'hr_ref' }))
  const decoupling = useQuery(q<Trends>('/api/stats/trends', { metric: 'decoupling' }))
  const cadence = useQuery(q<CadenceBands>('/api/stats/cadence-bands'))
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api<{ ref_pace_s_per_km: number | null }>('/api/settings') })
  const paceHr = useQuery(q<PaceHr>('/api/stats/pace-hr'))
  const zones = useQuery(q<Zones>('/api/stats/zones', { bucket: 'week' }))
  const dist = useQuery(q<Dist>('/api/stats/distribution', { field: 'distance' }))
  const top = useQuery(q<TopWeek[]>('/api/stats/top-weeks', { limit: '10' }))
  const fitness = useQuery({ queryKey: ['/api/stats/fitness'], queryFn: () => api<Fitness>('/api/stats/fitness') })
  const records = useQuery({ queryKey: ['/api/records'], queryFn: () => api<RecordRow[]>('/api/records') })

  const subtitle = custom ? `${from ?? '…'} → ${to ?? 'today'}` : PRESET_LABEL[preset]

  return (
    <div className="space-y-4" style={{ fontFamily: FB }}>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs tracking-[.08em] text-neutral-500 uppercase" style={{ fontFamily: FM }}>{subtitle}</div>
          <h1 className="mt-1 text-[44px] leading-none font-bold tracking-[.01em] text-[#eef1f4] uppercase" style={{ fontFamily: FC }}>La tua stagione</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Pills value={showCustom ? 'Custom' : preset} options={[...PRESETS, 'Custom']} onChange={(p) => {
            if (p === 'Custom') return setShowCustom(true)
            setShowCustom(false)
            set({ r: p, from: null, to: null, g: null })
          }} />
        </div>
        {showCustom && (
          <div className="flex w-full flex-wrap items-center justify-end gap-2">
            <input type="date" aria-label="From" className={`${field} ${mono}`} value={from ?? ''} onChange={(e) => set({ from: e.target.value || null, r: null, g: null })} />
            <span className="text-neutral-600">→</span>
            <input type="date" aria-label="To" className={`${field} ${mono}`} value={to ?? ''} onChange={(e) => set({ to: e.target.value || null, r: null, g: null })} />
          </div>
        )}
      </header>

      {(monthly.isError || summary.isError) && <p className="text-sm text-red-400">Failed to load stats.</p>}

      <Hero sum={summary.data} months={monthly.data?.items} showDelta={!!from} />
      <section className="flex flex-wrap gap-4">
        <WeeklyVolume data={weekly.data} />
        <ZoneDonut data={zones.data} />
      </section>
      <section className="flex flex-wrap gap-4">
        <MonthlyPace months={monthly.data?.items} />
        <LengthChart data={dist.data} longest={summary.data?.current.longest_run_m} />
      </section>
      <Consistency from={from} to={to} />

      <Alerts data={fitness.data} />
      <FitnessIndices data={fitness.data} />
      <Group title="Fitness">
        <PaceChart data={pace.data} gap={gap.data} />
        <EfChart data={ef.data} adj={efAdj.data} />
        <HrRefChart data={hrRef.data} refPace={settings.data?.ref_pace_s_per_km ?? 420} />
        <DecouplingChart data={decoupling.data} />
        <CadenceChart data={cadence.data} />
        <PaceHrChart data={paceHr.data} />
      </Group>

      <Group title="Training" cols="lg:grid-cols-2">
        <LongRunChart data={weekly.data} />
      </Group>

      <Group title="Records" cols="lg:grid-cols-2">
        <BestEffortChart data={records.data} />
        <TopWeeks data={top.data} />
      </Group>

      <AiPanel path="/api/ai/period" body={{ from_date: from, to_date: to }} />
    </div>
  )
}

// --- mockup home -----------------------------------------------------------------
const FC = "'Barlow Condensed', sans-serif"
const FM = "'IBM Plex Mono', monospace"
const FB = "'Barlow', Inter, system-ui, sans-serif"
const BLUE = '#4c8dff'
const BLUE_D = '#3366cc'
const MUTED = '#8a93a0'
const SOFT = '#aab2bd'
const SURF = '#191d23'
const ZB = ['#2a4f96', '#3366cc', '#4c8dff', '#86b0ff', '#c2d6ff']
const ZN = ['Z1 Recupero', 'Z2 Fondo lento', 'Z3 Medio', 'Z4 Soglia', 'Z5 VO₂max']
const it = (n: number, dg = 0) => n.toLocaleString('it-IT', { maximumFractionDigits: dg, minimumFractionDigits: dg })
const pct = (a: number, b: number) => {
  const v = b ? ((a - b) / b) * 100 : 0
  return `${v >= 0 ? '▲' : '▼'} ${Math.abs(Math.round(v))}%`
}
const monthOf = (d: string) => MESI[+d.slice(5, 7) - 1]
const thin = (n: number, i: number) => n <= 14 || i % Math.ceil(n / 12) === 0
const UP = { color: '#3fbf6a', fontWeight: 600 }
const DOWN = { color: '#ef7a6a', fontWeight: 600 }

function Panel({ title, sub, flex, aside, children }: { title: string; sub?: string; flex: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <article className="min-w-0 rounded-[14px] px-[22px] pt-5 pb-4" style={{ background: SURF, flex }}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="m-0 text-[22px] font-semibold tracking-[.02em] text-[#eef1f4] uppercase" style={{ fontFamily: FC }}>{title}</h2>
          {sub && <p className="mt-0.5 text-[13px]" style={{ color: MUTED }}>{sub}</p>}
        </div>
        {aside}
      </div>
      {children}
    </article>
  )
}

const NoData = ({ h = 160 }: { h?: number }) => (
  <p className="mt-4 flex items-center justify-center rounded-lg border border-dashed border-border text-sm text-neutral-600" style={{ height: h }}>Nessun dato nel periodo.</p>
)

function Pills<T extends string>({ value, options, onChange }: { value: T; options: readonly T[]; onChange: (v: T) => void }) {
  return (
    <div role="group" className="flex flex-wrap gap-0.5 rounded-3xl border border-[#262b33] p-1" style={{ background: SURF }}>
      {options.map((o) => (
        <button key={o} aria-pressed={value === o} onClick={() => onChange(o)} className="min-h-10 cursor-pointer rounded-full px-4 text-sm font-semibold" style={value === o ? { background: '#eef1f4', color: '#111418' } : { color: SOFT }}>
          {o}
        </button>
      ))}
    </div>
  )
}

function Hero({ sum, months, showDelta }: { sum?: Summary; months?: BucketTotals[]; showDelta: boolean }) {
  if (!sum || !months) return <div className="h-[330px] animate-pulse rounded-[14px]" style={{ background: SURF }} />
  const { current: c, previous: p } = sum
  const n = months.length
  const mx = Math.max(1, ...months.map((m) => m.distance_m)) * 1.15
  const hx = (i: number) => (n === 1 ? 600 : 30 + (i / (n - 1)) * 1140)
  const hy = (v: number) => 150 - (v / mx) * 128
  const line = months.map((m, i) => `${i ? 'L' : 'M'}${hx(i).toFixed(1)},${hy(m.distance_m).toFixed(1)}`).join('')
  const area = n ? `${line}L${hx(n - 1)},150L${hx(0)},150Z` : ''
  const pace = c.weighted_pace_s_per_km
  const dp = pace != null && p.weighted_pace_s_per_km != null ? Math.round(p.weighted_pace_s_per_km - pace) : null
  const kpis: { label: string; value: string; unit: string; delta?: string; style?: object; note: string }[] = [
    { label: 'Uscite', value: String(c.run_count), unit: 'corse', ...(showDelta && { delta: pct(c.run_count, p.run_count), style: c.run_count >= p.run_count ? UP : DOWN }), note: showDelta ? 'vs prec.' : 'nel periodo' },
    { label: 'Tempo di corsa', value: String(Math.round(c.moving_s / 3600)), unit: 'ore', ...(showDelta && { delta: pct(c.moving_s, p.moving_s), style: c.moving_s >= p.moving_s ? UP : DOWN }), note: showDelta ? 'vs prec.' : 'nel periodo' },
    { label: 'Passo medio', value: pace == null ? '—' : mmss(pace), unit: '/km', ...(showDelta && dp != null && { delta: `${dp >= 0 ? '▲' : '▼'} ${Math.abs(dp)} s/km`, style: dp >= 0 ? UP : DOWN }), note: dp == null || !showDelta ? 'ponderato' : dp >= 0 ? 'più veloce' : 'più lento' },
    { label: 'Media settimanale', value: it(km(c.avg_weekly_distance_m), 1), unit: 'km', delta: `picco ${it(km(c.longest_run_m), 0)} km`, style: { color: '#eef1f4', fontWeight: 600 }, note: 'uscita più lunga' },
  ]
  return (
    <section className="flex flex-wrap gap-4">
      <div className="relative flex min-w-0 flex-col overflow-hidden rounded-[14px] px-7 pt-6" style={{ flex: '2 1 560px', background: BLUE, color: '#111418' }}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-xs font-medium tracking-[.08em] uppercase" style={{ fontFamily: FM }}>Distanza totale</div>
            <div className="mt-2 text-[96px] leading-[.9] font-bold" style={{ fontFamily: FC }}>{it(km(c.distance_m))}<span className="ml-1.5 text-[32px]">km</span></div>
          </div>
          {showDelta && <div className="rounded-full bg-[#111418] px-3 py-1.5 text-sm font-semibold text-[#eef1f4]">{pct(c.distance_m, p.distance_m)} vs periodo prec.</div>}
        </div>
        <div className="relative -mx-7 mt-[18px] h-[150px]">
          <svg viewBox="0 0 1200 150" preserveAspectRatio="none" className="absolute inset-0 h-full w-full" aria-label="Chilometri al mese">
            <path d={area} fill="#111418" fillOpacity={0.16} />
            <path d={line} fill="none" stroke="#111418" strokeWidth={2.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          </svg>
          {months.map((m, i) => thin(n, i) && (
            <div key={m.start} className="absolute flex flex-col items-center" style={{ left: `${hx(i) / 12}%`, top: `${hy(m.distance_m) / 1.5}%`, transform: 'translate(-50%, -5px)' }}>
              <div className="h-[9px] w-[9px] rounded-full border-2 bg-[#111418]" style={{ borderColor: BLUE }} />
              <div className="mt-1 text-[11px] font-medium" style={{ fontFamily: FM }}>{it(km(m.distance_m))}</div>
            </div>
          ))}
        </div>
        <div className="relative -mx-7 h-[34px] text-[11px] font-medium tracking-[.04em] uppercase" style={{ fontFamily: FM }}>
          {months.map((m, i) => thin(n, i) && <span key={m.start} className="absolute top-2 -translate-x-1/2" style={{ left: `${hx(i) / 12}%` }}>{monthOf(m.start)}</span>)}
        </div>
      </div>
      <div className="grid min-w-0 grid-cols-2 gap-4" style={{ flex: '1 1 300px' }}>
        {kpis.map((k) => (
          <div key={k.label} className="flex min-w-0 flex-col justify-between gap-2.5 rounded-[14px] px-[18px] pt-[18px] pb-4" style={{ background: SURF }}>
            <div className="text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM, color: MUTED }}>{k.label}</div>
            <div className="text-[44px] leading-none font-semibold text-[#eef1f4]" style={{ fontFamily: FC }}>{k.value}<span className="ml-1 text-lg" style={{ color: SOFT }}>{k.unit}</span></div>
            <div className="text-[13px] leading-[1.3]" style={{ color: SOFT }}>{k.delta && <b style={k.style}>{k.delta}</b>} {k.note}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

function WeeklyVolume({ data }: { data?: Volume }) {
  const wk = data?.items ?? []
  const n = wk.length
  const raw = Math.max(0, ...wk.map((w) => km(w.distance_m)))
  const wMax = Math.max(20, Math.ceil(raw / 20) * 20)
  const avg = wk.map((_, i) => {
    const sl = wk.slice(Math.max(0, i - 3), i + 1)
    return sl.reduce((a, w) => a + km(w.distance_m), 0) / sl.length
  })
  const avgLine = avg.map((a, i) => `${i ? 'L' : 'M'}${(((i + 0.5) / n) * 1000).toFixed(1)},${(220 - (a / wMax) * 220).toFixed(1)}`).join('')
  const labels: { t: string; left: number }[] = []
  let last = -1
  let lastLeft = -99
  wk.forEach((w, i) => {
    const m = +w.start.slice(5, 7)
    if (m === last) return
    last = m
    const left = (i / n) * 100
    if (left - lastLeft > 5) {
      labels.push({ t: MESI[m - 1], left })
      lastLeft = left
    }
  })
  return (
    <Panel
      flex="2 1 560px"
      title="Volume settimanale"
      sub={`Km per settimana · picco ${it(raw)} km`}
      aside={
        <div className="flex gap-4 text-[13px]" style={{ color: SOFT }}>
          <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px]" style={{ background: BLUE }} />km settimana</span>
          <span className="flex items-center gap-1.5"><span className="h-0.5 w-4" style={{ background: '#dfe6ee' }} />media 4 sett.</span>
        </div>
      }
    >
      {n ? (
        <div className="mt-4 flex gap-2">
          <div className="-mt-[7px] mb-[7px] flex h-[220px] w-[22px] flex-col justify-between text-right text-[11px]" style={{ fontFamily: FM, color: MUTED }}>
            {[4, 3, 2, 1, 0].map((k) => <span key={k}>{(wMax / 4) * k}</span>)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="relative h-[220px] border-b border-[#3a414b]" style={{ backgroundImage: 'linear-gradient(#262b33 1px, transparent 1px)', backgroundSize: '100% 55px' }}>
              <div className="absolute inset-0 flex items-end">
                {wk.map((w) => (
                  <div key={w.start} className="box-border flex h-full flex-1 items-end px-[1.5px]" title={`${w.start} · ${it(km(w.distance_m), 1)} km`}>
                    <div className="w-full rounded-t-[3px]" style={{ height: `${(km(w.distance_m) / wMax) * 100}%`, background: km(w.distance_m) === raw ? '#c2d6ff' : BLUE, opacity: w.partial ? 0.55 : 1 }} />
                  </div>
                ))}
              </div>
              <svg viewBox="0 0 1000 220" preserveAspectRatio="none" className="absolute inset-0 h-full w-full" aria-hidden>
                <path d={avgLine} fill="none" stroke={SURF} strokeWidth={5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
                <path d={avgLine} fill="none" stroke="#dfe6ee" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
              </svg>
            </div>
            <div className="relative h-5 text-[11px]" style={{ fontFamily: FM, color: MUTED }}>
              {labels.map((l) => <span key={l.left} className="absolute top-1" style={{ left: `${l.left}%` }}>{l.t}</span>)}
            </div>
          </div>
        </div>
      ) : (
        <NoData h={220} />
      )}
    </Panel>
  )
}

function ZoneDonut({ data }: { data?: Zones }) {
  const tot = data?.totals_s ?? []
  const all = tot.reduce((a, x) => a + x, 0)
  const circ = 2 * Math.PI * 70
  let acc = 0
  const top = all ? tot.indexOf(Math.max(...tot)) : 0
  return (
    <Panel flex="1 1 300px" title="Zone cardiache" sub={`Quota del tempo di corsa · ${Math.round(all / 3600)} h con FC, gare escluse`}>
      {all ? (
        <div className="mt-4 flex flex-wrap items-center gap-5">
          <div className="relative h-[168px] w-[168px] shrink-0">
            <svg viewBox="0 0 180 180" width={168} height={168} style={{ transform: 'rotate(-90deg)' }} aria-label="Distribuzione del tempo per zona">
              {tot.map((v, j) => {
                const len = (v / all) * circ
                const seg = Math.max(0, len - 3)
                const off = -acc
                acc += len
                return <circle key={j} cx={90} cy={90} r={70} fill="none" stroke={ZB[j]} strokeWidth={24} strokeDasharray={`${seg.toFixed(2)} ${(circ - seg).toFixed(2)}`} strokeDashoffset={off.toFixed(2)} />
              })}
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <div className="text-4xl leading-none font-bold text-[#eef1f4]" style={{ fontFamily: FC }}>{Math.round((tot[top] / all) * 100)}%</div>
              <div className="text-xs" style={{ color: SOFT }}>in Z{top + 1}</div>
            </div>
          </div>
          <div className="flex min-w-[140px] flex-1 flex-col gap-[9px]">
            {tot.map((v, j) => (
              <div key={j} className="flex items-center gap-2.5 text-sm">
                <span className="h-3 w-3 rounded-[3px]" style={{ background: ZB[j] }} />
                <span className="flex-1" style={{ color: SOFT }}>{ZN[j]}</span>
                <span className="text-base font-semibold text-[#eef1f4]" style={{ fontFamily: FC }}>{Math.round((v / all) * 100)}%</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <NoData />
      )}
    </Panel>
  )
}

function MonthlyPace({ months }: { months?: BucketTotals[] }) {
  const pm = (months ?? []).filter((m) => m.weighted_pace_s_per_km != null).map((m) => ({ m: +m.start.slice(5, 7) - 1, p: m.weighted_pace_s_per_km as number }))
  if (!pm.length)
    return (
      <Panel flex="1 1 420px" title="Passo medio mensile" sub="min/km · più in alto = più veloce">
        <NoData />
      </Panel>
    )
  const lo = Math.floor(Math.min(...pm.map((o) => o.p)) / 15) * 15
  let hi = Math.ceil(Math.max(...pm.map((o) => o.p)) / 15) * 15
  if (hi === lo) hi = lo + 15
  const py = (v: number) => 14 + ((v - lo) / (hi - lo)) * 166
  const px = (i: number) => (pm.length === 1 ? 318 : 60 + (i / (pm.length - 1)) * 520)
  const best = pm.reduce((a, b) => (b.p < a.p ? b : a))
  const line = pm.map((o, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)},${py(o.p).toFixed(1)}`).join('')
  const area = `${line}L${px(pm.length - 1)},180L${px(0)},180Z`
  const ticks: number[] = []
  for (let v = lo; v <= hi; v += 15) ticks.push(v)
  return (
    <Panel
      flex="1 1 420px"
      title="Passo medio mensile"
      sub="min/km · più in alto = più veloce"
      aside={
        <div className="text-right">
          <div className="text-[28px] leading-none font-semibold" style={{ fontFamily: FC, color: BLUE }}>{mmss(best.p)}</div>
          <div className="text-xs" style={{ color: MUTED }}>mese migliore · {MESI[best.m]}</div>
        </div>
      }
    >
      <svg viewBox="0 0 600 210" className="mt-3 block h-auto w-full" aria-label="Passo medio per mese">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={44} x2={592} y1={py(v)} y2={py(v)} stroke="#262b33" />
            <text x={36} y={py(v) + 4} textAnchor="end" fill={MUTED} fontFamily="IBM Plex Mono, monospace" fontSize={11}>{mmss(v)}</text>
          </g>
        ))}
        <path d={area} fill={BLUE} fillOpacity={0.12} />
        <path d={line} fill="none" stroke={BLUE} strokeWidth={2.5} strokeLinejoin="round" />
        {pm.map((o, i) => (
          <g key={i}>
            <circle cx={px(i)} cy={py(o.p)} r={o === best ? 6 : 4} fill={o === best ? '#eef1f4' : BLUE} stroke={SURF} strokeWidth={2} />
            {thin(pm.length, i) && <text x={px(i)} y={204} textAnchor="middle" fill={MUTED} fontFamily="IBM Plex Mono, monospace" fontSize={11}>{MESI[o.m]}</text>}
          </g>
        ))}
      </svg>
    </Panel>
  )
}

function LengthChart({ data, longest }: { data?: Dist; longest?: number }) {
  const b = data?.buckets ?? []
  const mx = Math.max(1, ...b.map((x) => x.count))
  const name = (x: { lo: number; hi: number | null }) => (x.hi == null ? `${km(x.lo)}+` : `${km(x.lo)}–${km(x.hi)}`)
  return (
    <Panel flex="1 1 420px" title="Lunghezza delle uscite" sub={`Numero di corse per fascia, in km${longest ? ` · più lunga ${it(km(longest), 1)} km` : ''}`}>
      {data?.n ? (
        <>
          <div className="mt-[18px] flex h-[190px] items-end gap-3 border-b border-[#3a414b]">
            {b.map((x) => (
              <div key={name(x)} className="flex h-full flex-1 flex-col items-center justify-end gap-1.5">
                <span className="text-[13px] font-semibold text-[#eef1f4]">{x.count}</span>
                <div className="w-full max-w-16 rounded-t" style={{ height: Math.max(1, (x.count / mx) * 150), background: x.count === mx ? BLUE : BLUE_D }} />
              </div>
            ))}
          </div>
          <div className="mt-1.5 flex gap-3 text-[11px]" style={{ fontFamily: FM, color: MUTED }}>
            {b.map((x) => <span key={name(x)} className="flex-1 text-center">{name(x)}</span>)}
          </div>
        </>
      ) : (
        <NoData h={190} />
      )}
    </Panel>
  )
}

const CAL = ['#20252c', '#2a4f96', '#3366cc', '#4c8dff', '#86b0ff', '#c2d6ff']
const TH = [0, 0.1, 6, 9, 13, 18]
const calLevel = (k: number) => {
  for (let q = TH.length - 1; q > 0; q--) if (k >= TH[q]) return q
  return 0
}
const parse = (d: string) => new Date(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10))

// Calendar API is per-year: window is the period clamped to the last 52 weeks
function Consistency({ from, to }: { from?: string; to?: string }) {
  const end = to ? parse(to) : new Date()
  const floor = new Date(end.getFullYear(), end.getMonth(), end.getDate() - 364)
  const start = from && parse(from) > floor ? parse(from) : floor
  const years = [...new Set([start.getFullYear(), end.getFullYear()])]
  const cal = useQuery({
    queryKey: ['calendar', years],
    queryFn: () => Promise.all(years.map((y) => api<Calendar>(`/api/stats/calendar?year=${y}`))),
  })
  const byDay = new Map<string, number>()
  cal.data?.forEach((c) => c.days.forEach((d) => byDay.set(d.date, km(d.distance_m))))
  const mon = new Date(start)
  mon.setDate(mon.getDate() - ((mon.getDay() + 6) % 7))
  const weeks: { m: string; days: (number | null)[] }[] = []
  const dow = [0, 0, 0, 0, 0, 0, 0]
  let active = 0
  let total = 0
  let lm = -1
  for (const w = new Date(mon); w <= end; w.setDate(w.getDate() + 7)) {
    const days = Array.from({ length: 7 }, (_, g) => {
      const d = new Date(w.getFullYear(), w.getMonth(), w.getDate() + g)
      if (d < start || d > end) return null
      const v = byDay.get(iso(d)) ?? 0
      total++
      if (v) active++
      dow[g] += v
      return v
    })
    weeks.push({ m: w.getMonth() !== lm ? MESI[w.getMonth()] : '', days })
    lm = w.getMonth()
  }
  const dM = Math.max(1, ...dow)
  return (
    <section className="flex flex-wrap gap-4">
      <Panel
        flex="2 1 560px"
        title="Costanza"
        sub={`Km al giorno · ${active} giorni di corsa su ${total}`}
        aside={
          <div className="flex items-center gap-1 text-xs" style={{ color: MUTED }}>
            riposo {CAL.map((c) => <span key={c} className="h-3 w-3 rounded-[3px]" style={{ background: c }} />)} 18+ km
          </div>
        }
      >
        {cal.data ? (
          <div className="mt-4 flex gap-1.5">
            <div className="flex w-[26px] flex-col gap-[3px] pt-[18px] text-[10px]" style={{ fontFamily: FM, color: MUTED }}>
              {GG.map((g, i) => <span key={g} className="flex flex-1 items-center">{i % 2 ? '' : g}</span>)}
            </div>
            <div className="flex min-w-0 flex-1 gap-[3px]">
              {weeks.map((w, i) => (
                <div key={i} className="flex min-w-0 flex-1 flex-col gap-[3px]">
                  <span className="h-[15px] overflow-visible text-[10px] whitespace-nowrap" style={{ fontFamily: FM, color: MUTED }}>{w.m}</span>
                  {w.days.map((v, g) => <div key={g} className="aspect-square w-full rounded-[3px]" style={v == null ? undefined : { background: CAL[calLevel(v)] }} title={v == null ? undefined : `${it(v, 1)} km`} />)}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <NoData h={150} />
        )}
      </Panel>
      <Panel flex="1 1 300px" title="Giorni della settimana" sub="Km totali per giorno">
        <div className="mt-4 flex flex-col gap-2">
          {dow.map((v, j) => (
            <div key={GG[j]} className="flex items-center gap-2.5">
              <span className="w-8 text-[13px]" style={{ color: SOFT }}>{GG[j]}</span>
              <div className="flex h-[18px] flex-1 items-center gap-2">
                <div className="h-[18px] rounded-r" style={{ width: `${Math.max(1, (v / dM) * 82)}%`, background: v === dM ? BLUE : BLUE_D }} />
                <span className="text-[13px] font-semibold text-[#eef1f4]">{it(v)}</span>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </section>
  )
}

// --- injury-risk alerts ------------------------------------------------------------
const ALERT_STYLE = { high: 'border-red-500/50 text-red-300', warn: 'border-amber-500/50 text-amber-300', info: 'border-[#3a414b] text-[#aab2bd]' }

function Alerts({ data }: { data?: Fitness }) {
  if (!data?.alerts.length) return null
  return (
    <section role="status" className="space-y-2">
      {data.alerts.map((a) => (
        <p key={a.code} className={`rounded-[14px] border px-4 py-3 text-sm ${ALERT_STYLE[a.level]}`} style={{ background: SURF }}>
          <b className="mr-2 text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM }}>{a.level === 'info' ? 'Nota' : 'Rischio'}</b>
          {a.message}
        </p>
      ))}
    </section>
  )
}

// --- fitness indices -------------------------------------------------------------
const PHASE: Record<NonNullable<Fitness['phase']>, string> = {
  load1: 'carico 1/3',
  load2: 'carico 2/3',
  load3: 'carico 3/3',
  deload: 'scarico',
  race_week: 'settimana gara',
  post_race: 'post gara',
}
function FitnessIndices({ data }: { data?: Fitness }) {
  if (!data) return <div className="h-[200px] animate-pulse rounded-[14px]" style={{ background: SURF }} />
  const { vo2max: v, predictions: pr } = data
  const f = (x: number | null, dg = 0) => (x == null ? '—' : it(x, dg))
  const easing = data.phase === 'deload' || data.phase === 'race_week' || data.phase === 'post_race'
  const building = data.phase === 'load2' || data.phase === 'load3'
  const acwrNote = data.acwr == null ? '' : data.acwr > 1.5 ? 'picco · rischio' : data.acwr > 1.3 ? (building ? 'sopra la media · atteso' : 'sopra la media') : data.acwr >= 0.8 ? 'zona ottimale' : easing ? 'in calo · atteso' : 'in calo'
  const tsbNote = data.tsb == null ? '' : data.tsb > 5 ? 'carico in calo · riposato' : data.tsb >= -10 ? 'in equilibrio' : data.tsb >= -30 ? 'blocco di carico' : 'carico molto sopra la media'
  const phase = data.phase && PHASE[data.phase]
  const monoNote = data.monotony == null ? '' : data.monotony > 2 ? 'alta · poca varietà' : 'ok'
  const idx = [
    { label: 'Forma (CTL)', value: f(data.ctl), note: 'carico cronico, 42 gg', help: 'Media esponenziale (τ=42 giorni) del carico giornaliero (TRIMP di Edwards da zone FC) (Banister).' },
    { label: 'Carico acuto (ATL)', value: f(data.atl), note: 'media 7 gg', help: 'Media esponenziale (τ=7 giorni) del carico giornaliero (TRIMP di Edwards da zone FC). Misura il lavoro recente, non la stanchezza percepita.' },
    { label: 'Bilancio (TSB)', value: f(data.tsb), note: phase ? `${tsbNote} · ${phase}` : tsbNote, help: 'CTL − ATL: carico recente rispetto alla media. Negativo nelle settimane di carico, positivo in scarico e prima delle gare: è atteso. Non considera sonno, HRV o recupero.' },
    { label: 'ACWR', value: f(data.acwr, 2), note: acwrNote, help: 'Carico ultimi 7 gg / media settimanale degli ultimi 28 gg. 0,8–1,3 = zona ottimale, >1,5 = rischio infortuni. Con un ciclo 3+1 scende sotto 0,8 nella settimana di scarico.' },
    { label: 'Monotonia', value: f(data.monotony, 2), note: monoNote, help: 'Foster: media / deviazione standard del carico degli ultimi 7 giorni. >2 = allenamento troppo uniforme.' },
    { label: 'Strain', value: f(data.strain), note: 'carico sett. × monotonia', help: 'Foster: carico settimanale × monotonia.' },
  ]
  return (
    <section className="flex flex-wrap gap-4">
      <div className="min-w-0 rounded-[14px] px-7 py-6" style={{ flex: '1 1 340px', background: BLUE, color: '#111418' }}>
        <div className="text-xs font-medium tracking-[.08em] uppercase" style={{ fontFamily: FM }}>VO₂max stimato</div>
        <div className="mt-2 text-[96px] leading-[.9] font-bold" style={{ fontFamily: FC }}>{f(v.vdot ?? v.hr_based, 1)}<span className="ml-1.5 text-[24px]">ml/kg/min</span></div>
        <div className="mt-3 space-y-0.5 text-[13px] font-medium">
          <div>{v.vdot != null ? `VDOT Daniels · ${v.vdot_source} del ${v.vdot_date}` : 'Nessuna prestazione negli ultimi 180 gg'}</div>
          <div>{v.hr_based != null ? `Da FC (%FCR): ${it(v.hr_based, 1)} · ${v.hr_based_n} corse costanti, 28 gg` : 'Da FC: imposta FC max e a riposo in Impostazioni'}</div>
        </div>
        {pr.length > 0 && (
          <div className="mt-4 grid grid-cols-4 gap-2 border-t border-[#111418]/30 pt-3">
            {pr.map((p) => (
              <div key={p.label}>
                <div className="text-[11px] tracking-[.06em] uppercase" style={{ fontFamily: FM }}>{({ 'Half Marathon': 'HM', Marathon: 'Mar' } as Record<string, string>)[p.label] ?? p.label}</div>
                <div className="text-[22px] leading-tight font-semibold" style={{ fontFamily: FC }}>{clock(p.seconds)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="grid min-w-0 grid-cols-2 gap-4 sm:grid-cols-3" style={{ flex: '2 1 480px' }}>
        {idx.map((k) => (
          <div key={k.label} title={k.help} className="flex min-w-0 cursor-help flex-col justify-between gap-2.5 rounded-[14px] px-[18px] pt-[18px] pb-4" style={{ background: SURF }}>
            <div className="text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM, color: MUTED }}>{k.label}</div>
            <div className="text-[44px] leading-none font-semibold text-[#eef1f4]" style={{ fontFamily: FC }}>{k.value}</div>
            <div className="text-[13px] leading-[1.3]" style={{ color: SOFT }}>{k.note || ' '}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

// --- D4 -----------------------------------------------------------------------
function LongRunChart({ data }: { data?: Volume }) {
  const items = data?.items ?? []
  const option: EChartsOption = {
    ...base,
    xAxis: { type: 'category', data: items.map((b) => b.start), axisLine, axisTick: { show: false } },
    yAxis: [
      { type: 'value', splitLine, axisLabel: { formatter: '{value} km' } },
      { type: 'value', max: 100, splitLine: { show: false }, axisLabel: { formatter: '{value}%' } },
    ],
    series: [
      {
        name: 'longest',
        type: 'line',
        smooth: true,
        data: items.map((b) => +km(b.longest_run_m).toFixed(2)),
        lineStyle: { color: C.vol, width: 2 },
        itemStyle: { color: C.vol },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: `${C.vol}40` }, { offset: 1, color: `${C.vol}00` }]) },
        tooltip: { valueFormatter: (v) => `${v} km` },
      },
      {
        name: '% of week',
        type: 'line',
        yAxisIndex: 1,
        showSymbol: false,
        data: items.map((b) => (b.distance_m > 0 ? Math.round((b.longest_run_m / b.distance_m) * 100) : null)),
        lineStyle: { color: C.text, type: 'dashed', width: 1 },
        itemStyle: { color: C.text },
        tooltip: { valueFormatter: (v) => (v == null ? '—' : `${v}%`) },
      },
    ],
  }
  return (
    <Card title="Weekly long run" help="Longest single run per ISO week (line), and its share of that week's km (dashed). Weeks with no long run read low; that is not a regression.">
      {items.length ? <Chart option={option} /> : <Empty />}
    </Card>
  )
}

// --- D5 / D6 ------------------------------------------------------------------
function trendSeries(t: Trends, color: string, fmt: (v: number) => string, clamp?: number): EChartsOption['series'] {
  const v = (x: number) => (clamp ? Math.min(x, clamp) : x)
  return [
    { name: 'steady run', type: 'scatter', symbolSize: 6, itemStyle: { color, opacity: 0.4 }, data: t.points.map((p) => [p.date, v(p.value)]), tooltip: { valueFormatter: (x) => fmt(Number(x)) } },
    { name: '28-day median', type: 'line', smooth: true, showSymbol: false, lineStyle: { color, width: 2.5 }, itemStyle: { color }, data: t.points.map((p) => [p.date, v(p.rolling_median)]), tooltip: { valueFormatter: (x) => fmt(Number(x)) } },
  ]
}

function PaceChart({ data: raw, gap }: { data?: Trends; gap?: Trends }) {
  const [mode, setMode] = useState<'Passo' | 'GAP'>('Passo')
  const data = mode === 'GAP' ? gap : raw
  const option: EChartsOption | null = data?.n
    ? {
        ...base,
        tooltip: { ...base.tooltip, trigger: 'item' },
        xAxis: timeAxis,
        yAxis: { type: 'value', inverse: true, scale: true, max: (e) => Math.min(e.max, PACE_CLAMP), splitLine, axisLabel: { formatter: (v: number) => mmss(v) } },
        series: trendSeries(data, C.pace, (x) => formatPace(x), PACE_CLAMP),
      }
    : null
  return (
    <Card
      title="Pace"
      n={data?.n}
      aside={<Toggle value={mode} options={['Passo', 'GAP'] as const} onChange={setMode} />}
      help="Steady runs only (outdoor, ≥ 20 min, with HR, low pace variability, not workout/race). Line = median of steady runs in the trailing 28 days. Axis inverted: faster is higher; clamped at 10:00/km. GAP (model) = grade-adjusted pace: each stretch weighted by the Minetti energy cost of its slope, so hilly runs compare with flat ones."
    >
      {option ? <Chart option={option} /> : <Empty what="steady runs" />}
    </Card>
  )
}

const CAD_RAMP = ['#f3e8ff', '#e9d5ff', '#d8b4fe', '#c084fc', '#a855f7', '#9333ea', '#7e22ce', '#581c87'] // fast → slow, one per band
const bandName = (b: { lo: number | null; hi: number | null }) => (b.lo == null ? `<${mmss(b.hi!)}` : b.hi == null ? `>${mmss(b.lo)}` : `${mmss(b.lo)}–${mmss(b.hi)}`)

function CadenceChart({ data }: { data?: CadenceBands }) {
  const used = (data?.bands ?? []).map((b, i) => ({ ...b, i })).filter((b) => b.n)
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item', formatter: (p) => { const [d, c, pace] = (p as unknown as { value: [string, number, number] }).value; return `${d}<br/>${Math.round(c)} spm · ${formatPace(pace)}` } },
    xAxis: timeAxis,
    yAxis: { type: 'value', scale: true, minInterval: 1, splitLine },
    series: used.map((b) => ({
      name: bandName(b),
      type: 'scatter',
      symbolSize: 6,
      itemStyle: { color: CAD_RAMP[b.i] },
      data: data!.points.filter((p) => p.band === b.i).map((p) => [p.date, p.cadence_spm, p.pace_s_per_km]),
    })),
  }
  return (
    <Card title="Cadence by pace" n={data?.n} help="PLAN D12. Cadence rises with speed, so it is compared only within the same pace band (steady runs, avg cadence and pace of the run). Trend = Theil-Sen slope per band, shown with n ≥ 8.">
      {used.length ? (
        <>
          <Chart option={option} className="h-44" />
          <table className={`mt-2 w-full text-xs ${mono}`}>
            <tbody>
              {used.map((b) => (
                <tr key={b.i} className="border-t border-border text-neutral-300">
                  <td className="py-1"><span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: CAD_RAMP[b.i] }} />{bandName(b)}</td>
                  <td className="text-right text-neutral-500">n={b.n}</td>
                  <td className="text-right">{b.median_spm != null ? `${Math.round(b.median_spm)} spm` : '—'}</td>
                  <td className="text-right text-neutral-400">{b.trend ? `${b.trend.slope_per_day >= 0 ? '+' : '−'}${Math.abs(b.trend.slope_per_day * 30).toFixed(1)} /mese` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <Empty what="steady runs with cadence" />
      )}
    </Card>
  )
}

/** Scatter + rolling median + dashed Theil-Sen line; label = change over the span. */
function fitted(data: Trends | undefined, color: string, fmt: (v: number) => string, delta: (change: number, base: number) => string, what: string) {
  const n = data?.n ?? 0
  let label: string | null = null
  const series = data && n ? [...(trendSeries(data, color, fmt) as object[])] : []
  if (data && n) {
    if (data.trend) {
      const { slope_per_day: k, span_days: span } = data.trend
      const t = (d: string) => Date.parse(d) / DAY
      const fit = data.points.slice(-500)
      const x0 = t(fit[0].date)
      const b = median(fit.map((p) => p.value - k * (t(p.date) - x0)))
      label = `${delta(k * span, b)} in ${Math.max(1, Math.round(span / 7))} weeks`
      series.push({
        name: 'Theil-Sen',
        type: 'line',
        showSymbol: false,
        lineStyle: { color: C.line, type: 'dashed', width: 1 },
        itemStyle: { color: C.line },
        data: [[fit[0].date, b], [fit[fit.length - 1].date, b + k * span]],
        tooltip: { show: false },
      })
    } else label = `trend needs ≥ ${MIN_TREND_N} ${what}`
  }
  return { label, series: series as EChartsOption['series'] }
}
const signed = (x: number, s: string) => `${x < 0 ? '−' : '+'}${s}`

function EfChart({ data: raw, adj }: { data?: Trends; adj?: Trends }) {
  const [mode, setMode] = useState<'EF' | 'Corretto'>('EF')
  const data = mode === 'Corretto' ? adj : raw
  const n = data?.n ?? 0
  const { label, series } = fitted(data, C.hr, (x) => x.toFixed(3), (c, b) => signed(c, `${Math.abs((c * 100) / b).toFixed(1)}%`), 'steady runs')
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item' },
    xAxis: timeAxis,
    yAxis: { type: 'value', scale: true, splitLine, axisLabel: { formatter: (v: number) => v.toFixed(2) } },
    series,
  }
  return (
    <Card
      title="Aerobic efficiency"
      n={data?.n}
      extra={label}
      aside={<Toggle value={mode} options={['EF', 'Corretto'] as const} onChange={setMode} />}
      help="EF = moving speed (m/min) / avg HR, steady runs only; higher = faster per heartbeat. Line = trailing 28-day median. Dashed = Theil-Sen slope (median of pairwise slopes), hidden when n < 8. Corretto (model) = EF on grade-adjusted speed, raised by the expected heat slowdown (Hadley: temperature + dew point, °F) when weather is enabled in Settings."
    >
      {n ? <Chart option={option} /> : <Empty what="steady runs" />}
    </Card>
  )
}

function DecouplingChart({ data }: { data?: Trends }) {
  const { label, series } = fitted(data, C.hr, (x) => `${x.toFixed(1)}%`, (c) => signed(c, `${Math.abs(c).toFixed(1)} pt`), 'runs')
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item' },
    xAxis: timeAxis,
    yAxis: { type: 'value', scale: true, splitLine, axisLabel: { formatter: '{value}%' } },
    series,
  }
  return (
    <Card
      title="Aerobic decoupling"
      n={data?.n}
      extra={label}
      help="Pa:HR (Friel): how much EF drops from the first to the second half of moving time, on grade-adjusted distance. Steady runs over 45 min only. Under 5% = good aerobic endurance at that effort; it rises with duration, heat and dehydration. Lower over time = better endurance."
    >
      {data?.n ? <Chart option={option} /> : <Empty what="steady runs over 45 min" />}
    </Card>
  )
}

function HrRefChart({ data, refPace }: { data?: Trends; refPace: number }) {
  const n = data?.n ?? 0
  const { label, series } = fitted(data, C.hr, (x) => `${Math.round(x)} bpm`, (c) => signed(c, `${Math.abs(c).toFixed(1)} bpm`), 'runs')
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item' },
    xAxis: timeAxis,
    yAxis: { type: 'value', scale: true, splitLine },
    series,
  }
  return (
    <Card
      title={`HR at ${mmss(refPace)}/km`}
      n={data?.n}
      extra={label}
      help={`Model: per run, a least-squares line HR ~ speed on flat samples (|grade| ≤ 2%, pace over 60 s windows, first 5 min dropped), read at ${mmss(refPace)}/km. Only runs whose own pace range (10th–90th percentile) covers that pace and with ≥ 10 min of samples: no extrapolation. All outdoor runs, not only steady ones. Lower = fitter. Reference pace is set in Settings.`}
    >
      {n ? <Chart option={option} /> : <Empty what="runs at that pace" />}
    </Card>
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
    xAxis: { type: 'value', inverse: true, scale: true, max: (e) => Math.min(e.max, PACE_CLAMP), axisLine, splitLine: { show: false }, axisLabel: { formatter: (v: number) => mmss(v), hideOverlap: true } },
    yAxis: { type: 'value', scale: true, splitLine },
    series: [{ type: 'scatter', symbolSize: 7, data: pts }],
  }
  return (
    <Card
      title="Pace vs HR"
      n={data?.n}
      extra="faded = older"
      help="Steady runs: avg pace vs avg HR. Colour = date (faded = older). Down-right = faster at lower HR. Runs of different length are mixed: cardiac drift grows with duration. Axis inverted, clamped at 10:00/km."
    >
      {pts.length ? <Chart option={option} /> : <Empty what="steady runs" />}
    </Card>
  )
}

// --- D10 ----------------------------------------------------------------------
function BestEffortChart({ data }: { data?: RecordRow[] }) {
  const [label, setLabel] = useState(D10[1])
  const prog = data?.find((r) => r.label === label)?.progression ?? []
  const pr = prog.at(-1)
  const option: EChartsOption = {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item', formatter: (p) => { const [d, s] = (p as unknown as { value: [string, number] }).value; return `${d}<br/>${clock(s)}` } },
    xAxis: timeAxis,
    yAxis: { type: 'value', inverse: true, scale: true, splitLine, axisLabel: { formatter: (v: number) => clock(v) } },
    series: [
      { type: 'line', step: 'end', showSymbol: false, silent: true, lineStyle: { color: C.pace, width: 2 }, data: prog.map((e) => [e.local_date, e.elapsed_s]) },
      { type: 'scatter', itemStyle: { color: C.pace }, data: prog.map((e) => ({ value: [e.local_date, e.elapsed_s], symbol: e.workout_type === 'race' ? 'diamond' : 'circle', symbolSize: e.workout_type === 'race' ? 11 : 7 })) },
    ],
  }
  return (
    <Card
      title="Best efforts"
      extra={pr ? `PR ${clock(pr.elapsed_s)} · ${pr.local_date}` : null}
      aside={<Toggle value={label} options={D10} label={(x) => (x === 'Half Marathon' ? 'HM' : x)} onChange={setLabel} />}
      help="Each point is a new all-time PR at that distance (strictly faster than every earlier effort); the step line is the PR at each date. Diamond = race. A best effort inside a training run is not a race; GPS error can inflate or deflate distance. Not affected by the period."
    >
      {prog.length ? <Chart option={option} className="h-64" /> : <Empty what="best efforts" className="h-64" />}
    </Card>
  )
}

// --- D11 ----------------------------------------------------------------------
function TopWeeks({ data }: { data?: TopWeek[] }) {
  const th = 'px-2 py-1.5 font-normal'
  const td = 'px-2 py-1.5'
  const maxKm = Math.max(1, ...(data ?? []).map((w) => w.distance_m))
  return (
    <Card title="Top weeks" help="The 10 ISO weeks with the most km in the period. Click a week to list its runs.">
      {data?.length ? (
        <div className="overflow-x-auto">
          <table className={`w-full text-sm ${mono}`}>
            <thead className="text-xs text-neutral-500">
              <tr className="text-right">
                <th className={`${th} text-left`}>Week</th>
                <th className={`${th} w-1/3 text-left`}>km</th>
                <th className={th}>runs</th>
                <th className={th}>time</th>
                <th className={th}>pace</th>
                <th className={th}>D+</th>
              </tr>
            </thead>
            <tbody>
              {data.map((w) => (
                <tr key={w.start} className="border-t border-border text-right text-neutral-300 hover:bg-white/[0.02]">
                  <td className={`${td} text-left`}>
                    <Link to={`/activities?from=${w.start}&to=${w.end}`} className="text-neutral-200 hover:text-accent">
                      {w.start}
                    </Link>
                  </td>
                  <td className={`${td} text-left`}>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 rounded-full bg-volume" style={{ width: `${(w.distance_m / maxKm) * 70}%` }} />
                      <span className="text-neutral-100">{km(w.distance_m).toFixed(1)}</span>
                    </div>
                  </td>
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
        <Empty className="h-64" />
      )}
    </Card>
  )
}

// --- primitives ----------------------------------------------------------------
function Group({ title, cols = 'lg:grid-cols-2 xl:grid-cols-3', children }: { title: string; cols?: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-xs font-medium tracking-widest text-neutral-500 uppercase">{title}</h2>
      <div className={`grid gap-4 ${cols}`}>{children}</div>
    </section>
  )
}

function Card({ title, help, n, extra, aside, children }: { title: string; help: string; n?: number; extra?: string | null; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className={`min-w-0 ${card}`}>
      <div className="mb-3 flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0">
          <h3 className="flex items-center gap-1.5 text-sm font-medium text-neutral-100">
            {title}
            <span title={help} aria-label={help} className="cursor-help text-neutral-600 hover:text-neutral-400">
              <Info size={13} />
            </span>
            {n != null && <span className={`rounded bg-white/5 px-1.5 text-[10px] font-normal text-neutral-500 ${mono}`}>n={n}</span>}
          </h3>
          {extra && <p className={`mt-0.5 text-xs text-neutral-400 ${mono}`}>{extra}</p>}
        </div>
        {aside && <div className="ml-auto">{aside}</div>}
      </div>
      {children}
    </section>
  )
}

function Toggle<T extends string>({ value, options, onChange, label = (x) => x }: { value: T; options: readonly T[]; onChange: (v: T) => void; label?: (v: T) => string }) {
  return (
    <div className="flex rounded-lg border border-border bg-panel p-0.5" role="group">
      {options.map((o) => (
        <button
          key={o}
          aria-pressed={value === o}
          onClick={() => onChange(o)}
          className={`min-h-9 rounded-md px-2.5 text-xs transition-colors md:min-h-0 md:py-1 ${value === o ? 'bg-white/10 text-neutral-50' : 'text-neutral-500 hover:text-neutral-300'}`}
        >
          {label(o)}
        </button>
      ))}
    </div>
  )
}

const Empty = ({ what = 'runs', className = 'h-56' }: { what?: string; className?: string }) => (
  <p className={`flex items-center justify-center rounded-lg border border-dashed border-border text-sm text-neutral-600 ${className}`}>No {what} in this period.</p>
)

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
