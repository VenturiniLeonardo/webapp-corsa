import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { BLUE, FC, FM, FB, MUTED, PageHead, SOFT, surface } from '../components/ui'
import { formatDuration, formatPace } from '../utils/formatters'

type Effort = { activity_id: number; local_date: string; elapsed_s: number; workout_type: string | null }
type RecordRow = { distance_m: number; label: string; best: Effort | null; best_race: Effort | null; progression: Effort[] }

const mono = 'font-mono tabular-nums'
const pace = (r: RecordRow, e: Effort) => formatPace(e.elapsed_s / (r.distance_m / 1000))

function Line({ r, e, tag, big }: { r: RecordRow; e: Effort; tag?: string; big?: boolean }) {
  return (
    <span className={`flex flex-wrap items-baseline gap-x-3 ${mono}`}>
      <span className={big ? 'text-[44px] leading-none font-semibold text-[#eef1f4]' : 'text-[#eef1f4]'} style={big ? { fontFamily: FC } : undefined}>{formatDuration(e.elapsed_s)}</span>
      <span style={{ color: SOFT }}>{pace(r, e)}</span>
      <span style={{ color: MUTED }}>{e.local_date}</span>
      {tag && <span className="rounded-full bg-[#20252c] px-2 text-xs" style={{ color: SOFT }}>{tag}</span>}
      <Link to={`/activities/${e.activity_id}`} className="hover:underline" style={{ color: BLUE }}>
        activity
      </Link>
    </span>
  )
}

export default function RecordsPage() {
  const { data, isError } = useQuery({ queryKey: ['/api/records'], queryFn: () => api<RecordRow[]>('/api/records') })
  if (isError) return <p className="text-sm text-red-400">Failed to load records.</p>
  if (!data) return <p className="text-sm text-neutral-500">Loading…</p>
  return (
    <div className="space-y-4" style={{ fontFamily: FB }}>
      <PageHead eyebrow="Best efforts di sempre" title="Record" />
      <p className="text-[13px]" style={{ color: MUTED }}>All-time PRs from best efforts inside activities (outdoor, no GPS-suspect). A training effort is not a race.</p>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {data.map((r) => (
          <section key={r.label} className={surface}>
            <h2 className="mb-2 text-[22px] font-semibold tracking-[.02em] text-[#eef1f4] uppercase" style={{ fontFamily: FC }}>{r.label}</h2>
            {r.best ? (
              <>
                <Line r={r} e={r.best} big />
                {r.best_race && r.best_race.activity_id !== r.best.activity_id && (
                  <div className="mt-2 text-sm">
                    <Line r={r} e={r.best_race} tag="race" />
                  </div>
                )}
                <details className="mt-3">
                  <summary className="cursor-pointer text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM, color: MUTED }}>Progression ({r.progression.length})</summary>
                  <ol className="mt-2 space-y-1 text-sm">
                    {[...r.progression].reverse().map((e) => (
                      <li key={e.activity_id}>
                        <Line r={r} e={e} tag={e.workout_type === 'race' ? 'race' : undefined} />
                      </li>
                    ))}
                  </ol>
                </details>
              </>
            ) : (
              <p className="text-sm" style={{ color: MUTED }}>No effort yet.</p>
            )}
          </section>
        ))}
      </div>
    </div>
  )
}
