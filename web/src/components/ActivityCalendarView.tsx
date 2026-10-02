import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { formatPace } from '../utils/formatters'
import { btnGhost, FM, MUTED, surface } from './ui'

// app/api/stats.py calendar_month
type Item = { id: number; name: string | null; workout_type: string | null; distance_m: number; moving_s: number; has_pr: boolean }
type Week = { total_distance_m: number; total_moving_s: number; run_count: number; elev_gain_m: number; delta_pct: number | null }
type Month = { days: Record<string, Item[]>; weeks: Record<string, Week> }

const BORDER: Record<string, string> = { easy: '#4c8dff', long: '#f59e0b', workout: '#a855f7', race: '#ef4444' }
const DOW = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
const km = (m: number) => `${(m / 1000).toFixed(1)} km`
const hm = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}` : `${Math.round(s / 60)}m`)
const pace = (r: Item) => (r.distance_m > 0 ? formatPace((r.moving_s * 1000) / r.distance_m) : '—')
// date math in UTC so DST never shifts a day
const iso = (d: Date) => d.toISOString().slice(0, 10)
const addDays = (s: string, n: number) => {
  const d = new Date(`${s}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return iso(d)
}

function Run({ r }: { r: Item }) {
  return (
    <Link
      to={`/activities/${r.id}`}
      title={r.name ?? undefined}
      className="block rounded-md border-l-[3px] bg-[#111418] px-1.5 py-1 hover:bg-[#20252c]"
      style={{ borderColor: BORDER[r.workout_type ?? ''] ?? '#4a515c' }}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="font-mono font-bold tabular-nums text-[#eef1f4]">{km(r.distance_m)}</span>
        {r.has_pr && <span className="rounded bg-amber-500/20 px-1 text-[10px] font-bold text-amber-400">PR</span>}
      </div>
      <div className="font-mono text-[11px] tabular-nums" style={{ color: MUTED }}>
        {pace(r)} · {hm(r.moving_s)}
      </div>
    </Link>
  )
}

function WeekTotal({ w }: { w: Week }) {
  return (
    <div className="font-mono tabular-nums">
      <div className="font-bold text-[#eef1f4]">{km(w.total_distance_m)}</div>
      <div className="text-[11px]" style={{ color: MUTED }}>
        {hm(w.total_moving_s)} · {Math.round(w.elev_gain_m)} m D+
      </div>
      {w.delta_pct != null && (
        <div className={`text-[11px] ${w.delta_pct >= 0 ? 'text-green-400' : 'text-orange-400'}`}>
          {w.delta_pct >= 0 ? '+' : ''}
          {Math.round(w.delta_pct)}%
        </div>
      )}
    </div>
  )
}

/** `month` is "YYYY-MM"; `setMonth` moves the view. */
export default function ActivityCalendarView({ month, setMonth }: { month: string; setMonth: (m: string) => void }) {
  const [y, m] = month.split('-').map(Number)
  const q = useQuery({
    queryKey: ['calendar', month],
    queryFn: () => api<Month>(`/api/stats/calendar-month?year=${y}&month=${m}`),
    placeholderData: keepPreviousData,
  })
  const shift = (n: number) => setMonth(iso(new Date(Date.UTC(y, m - 1 + n, 1))).slice(0, 7))
  const label = new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('it-IT', { month: 'long', year: 'numeric', timeZone: 'UTC' })
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate()
  const monthRuns = Object.entries(q.data?.days ?? {}).filter(([d]) => d.startsWith(month)).flatMap(([, rs]) => rs)
  const monthDist = monthRuns.reduce((a, r) => a + r.distance_m, 0)
  const weeks = Object.entries(q.data?.weeks ?? {})

  return (
    <div className={`${surface} space-y-3`}>
      <div className="flex flex-wrap items-center gap-3">
        <button className={btnGhost} aria-label="Mese precedente" onClick={() => shift(-1)}>
          ←
        </button>
        <span className="min-w-36 text-center font-semibold text-[#eef1f4] capitalize">{label}</span>
        <button className={btnGhost} aria-label="Mese successivo" onClick={() => shift(1)}>
          →
        </button>
        <span className={`font-mono text-sm tabular-nums ${q.isFetching ? 'opacity-60' : ''}`} style={{ color: MUTED }}>
          {km(monthDist)} · {monthRuns.length} uscite · {km((monthDist / daysInMonth) * 7)}/sett
        </span>
      </div>
      {q.isError && <p className="text-sm text-red-400">Failed to load calendar.</p>}

      {/* desktop: 7 days + week total */}
      <div className="hidden grid-cols-[repeat(7,minmax(0,1fr))_minmax(0,1.1fr)] gap-1 text-sm md:grid">
        {[...DOW, 'Settimana'].map((d) => (
          <div key={d} className="px-1 text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM, color: MUTED }}>
            {d}
          </div>
        ))}
        {weeks.map(([mon, w]) => [
          ...DOW.map((_, i) => {
            const day = addDays(mon, i)
            return (
              <div key={day} className={`min-h-24 space-y-1 rounded-md border border-[#262b33] p-1 ${day.startsWith(month) ? '' : 'opacity-40'}`}>
                <div className="font-mono text-[11px] tabular-nums" style={{ color: MUTED }}>
                  {Number(day.slice(8))}
                </div>
                {q.data?.days[day]?.map((r) => <Run key={r.id} r={r} />)}
              </div>
            )
          }),
          <div key={`w${mon}`} className="rounded-md bg-[#191d23] p-2 ring-1 ring-[#262b33]">
            <WeekTotal w={w} />
          </div>,
        ])}
      </div>

      {/* mobile: compact list grouped by week */}
      <div className="space-y-3 md:hidden">
        {weeks.map(([mon, w]) => (
          <div key={mon} className="rounded-md border border-[#262b33] p-2">
            <div className="mb-2 flex items-start justify-between gap-2">
              <span className="font-mono text-xs tabular-nums" style={{ color: MUTED }}>
                {Number(mon.slice(8))}/{Number(mon.slice(5, 7))} – {Number(addDays(mon, 6).slice(8))}/{Number(addDays(mon, 6).slice(5, 7))}
              </span>
              <WeekTotal w={w} />
            </div>
            <div className="space-y-1">
              {DOW.map((dow, i) =>
                q.data?.days[addDays(mon, i)]?.map((r) => (
                  <div key={r.id} className="flex items-center gap-2">
                    <span className="w-8 shrink-0 text-xs" style={{ color: MUTED }}>
                      {dow}
                    </span>
                    <div className="min-w-0 flex-1">
                      <Run r={r} />
                    </div>
                  </div>
                )),
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
