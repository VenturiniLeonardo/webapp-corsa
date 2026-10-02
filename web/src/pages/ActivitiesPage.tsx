import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { field, FB, FM, PageHead, pill, surface, btnGhost } from '../components/ui'
import { formatDate, formatDistance, formatDuration, formatPace } from '../utils/formatters'
import { PRESETS, presetFrom } from '../utils/period'

type Activity = {
  id: number
  name: string | null
  sport_type: string
  workout_type: string | null
  start_time_utc: string
  timezone: string | null
  distance_m: number | null
  moving_s: number | null
  elev_gain_m: number | null
  avg_hr: number | null
}
type ActivityList = {
  items: Activity[]
  total: number
  aggregate: { count: number; distance_m: number; moving_s: number; weighted_pace_s_per_km: number | null }
}

const PAGE_SIZE = 50
const SPORTS = ['run', 'trail_run', 'treadmill']
const WORKOUTS = ['easy', 'long', 'workout', 'race', 'other']
const COLS: [sort: string, label: string, right: boolean][] = [
  ['date', 'Date', false],
  ['name', 'Name', false],
  ['type', 'Type', false],
  ['distance', 'Dist', true],
  ['duration', 'Time', true],
  ['pace', 'Pace', true],
  ['hr', 'HR', true],
  ['elev', 'D+', true],
]

// "5:30" -> 330 s/km; plain number = seconds
function parsePace(v: string): number | undefined {
  const m = /^(\d+):([0-5]?\d)$/.exec(v.trim())
  const n = m ? Number(m[1]) * 60 + Number(m[2]) : Number(v)
  return v.trim() && Number.isFinite(n) ? n : undefined
}
const num = (v: string | null, k = 1) => (v && Number.isFinite(Number(v)) ? Number(v) * k : undefined)

const hm = (s: number) => `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
const pace = (a: Activity) => (a.moving_s && a.distance_m ? (a.moving_s * 1000) / a.distance_m : null)
const mono = 'font-mono tabular-nums'

export default function ActivitiesPage() {
  const [sp, setSp] = useSearchParams()
  const nav = useNavigate()
  const get = (k: string) => sp.get(k) ?? ''
  const set = (kv: Record<string, string | string[] | null>, keepPage = false) =>
    setSp((prev) => {
      const n = new URLSearchParams(prev)
      for (const [k, v] of Object.entries(kv)) {
        n.delete(k)
        for (const x of Array.isArray(v) ? v : v ? [v] : []) n.append(k, x)
      }
      if (!keepPage) n.delete('page')
      return n
    })

  // debounced text search; URL is the source of truth (back/forward resyncs the box)
  const urlQ = get('q')
  const [q, setQ] = useState(urlQ)
  useEffect(() => setQ(urlQ), [urlQ])
  useEffect(() => {
    if (q === urlQ) return
    const t = setTimeout(() => set({ q: q || null }), 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])

  const custom = sp.has('from') || sp.has('to')
  const preset = custom ? '' : get('r') || 'All'
  const sort = get('sort') || 'date'
  const order = get('order') || 'desc'
  const page = Math.max(1, Number(get('page')) || 1)

  const qs = new URLSearchParams()
  const from = custom ? get('from') : presetFrom(preset)
  if (from) qs.set('from_date', from)
  if (get('to')) qs.set('to_date', get('to'))
  const ranges: [string, number | undefined][] = [
    ['dist_min', num(sp.get('dmin'), 1000)],
    ['dist_max', num(sp.get('dmax'), 1000)],
    ['pace_min', parsePace(get('pmin'))],
    ['pace_max', parsePace(get('pmax'))],
    ['hr_min', num(sp.get('hmin'))],
    ['hr_max', num(sp.get('hmax'))],
  ]
  for (const [k, v] of ranges) if (v !== undefined) qs.set(k, String(v))
  for (const t of sp.getAll('type')) qs.append('type[]', t)
  for (const t of sp.getAll('wt')) qs.append('workout_type[]', t)
  if (urlQ) qs.set('q', urlQ)
  qs.set('sort', sort)
  qs.set('order', order)
  qs.set('page', String(page))
  qs.set('page_size', String(PAGE_SIZE))

  const { data, isPending, isError, isFetching } = useQuery({
    queryKey: ['activities', qs.toString()],
    queryFn: () => api<ActivityList>(`/api/activities?${qs}`),
    placeholderData: keepPreviousData,
  })

  const input = (k: string, ph: string) => (
    <input
      aria-label={k}
      placeholder={ph}
      inputMode="decimal"
      className={`${field} w-16 ${mono}`}
      value={get(k)}
      onChange={(e) => set({ [k]: e.target.value || null })}
    />
  )
  const multi = (k: string, opts: string[]) => (
    <div className="flex flex-wrap gap-1">
      {opts.map((o) => {
        const on = sp.getAll(k).includes(o)
        return (
          <button
            key={o}
            aria-pressed={on}
            onClick={() => set({ [k]: on ? sp.getAll(k).filter((x) => x !== o) : [...sp.getAll(k), o] })}
            className={pill(on)}
          >
            {o.replace('_', ' ')}
          </button>
        )
      })}
    </div>
  )

  const agg = data?.aggregate
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <div className="space-y-4" style={{ fontFamily: FB }}>
      <PageHead eyebrow="Tutti gli allenamenti" title="Allenamenti" />
      <div className={`${surface} space-y-3`}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1">
          {PRESETS.map((p) => (
            <button
              key={p}
              aria-pressed={preset === p}
              onClick={() => set({ r: p === 'All' ? null : p, from: null, to: null })}
              className={pill(preset === p)}
            >
              {p}
            </button>
          ))}
        </div>
        <input type="date" aria-label="From" className={`${field} ${mono}`} value={from ?? ''} onChange={(e) => set({ from: e.target.value || null, r: null })} />
        <input type="date" aria-label="To" className={`${field} ${mono}`} value={get('to')} onChange={(e) => set({ to: e.target.value || null, from: from ?? null, r: null })} />
        <input type="search" aria-label="Search" placeholder="Search name/notes" className={`${field} w-44`} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-neutral-400">
        Dist km {input('dmin', 'min')} {input('dmax', 'max')}
        Pace /km {input('pmin', '4:30')} {input('pmax', '6:00')}
        HR {input('hmin', 'min')} {input('hmax', 'max')}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {multi('type', SPORTS)}
        {multi('wt', WORKOUTS)}
      </div>
      </div>

      <p className={`text-sm text-[#eef1f4] ${mono} ${isFetching ? 'opacity-60' : ''}`}>
        {agg
          ? `${agg.count} runs · ${(agg.distance_m / 1000).toFixed(1)} km · ${hm(agg.moving_s)} · ${formatPace(agg.weighted_pace_s_per_km)}`
          : '…'}
      </p>

      {isError && <p className="text-sm text-red-400">Failed to load activities.</p>}
      {isPending && <p className="text-sm text-neutral-500">Loading…</p>}
      {data?.items.length === 0 && <p className="text-sm text-neutral-500">No activities match.</p>}

      {data && data.items.length > 0 && (
        <div className={surface}>
          <table className="hidden w-full text-sm md:table">
            <thead className="border-b border-[#262b33] text-left text-[11px] tracking-[.07em] text-[#8a93a0] uppercase" style={{ fontFamily: FM }}>
              <tr>
                {COLS.map(([s, label, right]) => (
                  <th key={s} className={`py-1 pr-3 font-normal ${right ? 'text-right' : ''}`} aria-sort={sort === s ? (order === 'asc' ? 'ascending' : 'descending') : undefined}>
                    <button onClick={() => set({ sort: s, order: sort === s && order === 'desc' ? 'asc' : 'desc' })}>
                      {label}
                      {sort === s && (order === 'asc' ? ' ↑' : ' ↓')}
                    </button>
                  </th>
                ))}
                <th className="py-1 font-normal">Workout</th>
              </tr>
            </thead>
            <tbody className={mono}>
              {data.items.map((a) => (
                <tr key={a.id} onClick={() => nav(`/activities/${a.id}`)} className={`cursor-pointer border-b hover:bg-[#20252c] ${a.workout_type === 'race' ? 'border-red-500/40 bg-red-500/10' : 'border-[#262b33]'}`}>
                  <td className="py-1 pr-3 whitespace-nowrap">{formatDate(a.start_time_utc, a.timezone ?? 'UTC')}</td>
                  <td className="max-w-64 truncate pr-3 font-sans">
                    <Link to={`/activities/${a.id}`} className="hover:text-accent" onClick={(e) => e.stopPropagation()}>
                      {a.name ?? '—'}
                    </Link>
                  </td>
                  <td className="pr-3 font-sans text-neutral-400">{a.sport_type.replace('_', ' ')}</td>
                  <td className="pr-3 text-right">{a.distance_m != null ? formatDistance(a.distance_m) : '—'}</td>
                  <td className="pr-3 text-right">{a.moving_s != null ? formatDuration(a.moving_s) : '—'}</td>
                  <td className="pr-3 text-right">{formatPace(pace(a))}</td>
                  <td className="pr-3 text-right">{a.avg_hr != null ? Math.round(a.avg_hr) : '—'}</td>
                  <td className="pr-3 text-right">{a.elev_gain_m != null ? `${Math.round(a.elev_gain_m)} m` : '—'}</td>
                  <td className="font-sans text-neutral-400">{a.workout_type ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <ul className="md:hidden">
            {data.items.map((a) => (
              <li key={a.id} className={`border-b ${a.workout_type === 'race' ? 'border-red-500/40 bg-red-500/10' : 'border-[#262b33]'}`}>
                <Link to={`/activities/${a.id}`} className="block min-h-11 py-2">
                  <div className="flex justify-between gap-2">
                    <span className="truncate">{a.name ?? '—'}</span>
                    <span className={`shrink-0 text-xs text-neutral-400 ${mono}`}>{formatDate(a.start_time_utc, a.timezone ?? 'UTC')}</span>
                  </div>
                  <div className={`flex flex-wrap gap-x-3 text-xs text-neutral-400 ${mono}`}>
                    <span>{a.distance_m != null ? formatDistance(a.distance_m) : '—'}</span>
                    <span>{a.moving_s != null ? formatDuration(a.moving_s) : '—'}</span>
                    <span>{formatPace(pace(a))}</span>
                    {a.avg_hr != null && <span>{Math.round(a.avg_hr)} bpm</span>}
                    {a.elev_gain_m != null && <span>{Math.round(a.elev_gain_m)} m D+</span>}
                    <span className="font-sans">
                      {a.sport_type.replace('_', ' ')}
                      {a.workout_type && ` · ${a.workout_type}`}
                    </span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>

          <div className={`mt-3 flex items-center justify-between text-sm text-neutral-400 ${mono}`}>
            <button disabled={page <= 1} onClick={() => set({ page: String(page - 1) }, true)} className={btnGhost}>
              Prev
            </button>
            <span>
              {page} / {pages} · {data.total}
            </span>
            <button disabled={page >= pages} onClick={() => set({ page: String(page + 1) }, true)} className={btnGhost}>
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
