import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { formatDuration, formatPace } from '../utils/formatters'

type Effort = { activity_id: number; local_date: string; elapsed_s: number; workout_type: string | null }
type RecordRow = { distance_m: number; label: string; best: Effort | null; best_race: Effort | null; progression: Effort[] }

const mono = 'font-mono tabular-nums'
const pace = (r: RecordRow, e: Effort) => formatPace(e.elapsed_s / (r.distance_m / 1000))

function Line({ r, e, tag }: { r: RecordRow; e: Effort; tag?: string }) {
  return (
    <span className={`flex flex-wrap items-baseline gap-x-3 ${mono}`}>
      <span className="text-neutral-200">{formatDuration(e.elapsed_s)}</span>
      <span className="text-neutral-400">{pace(r, e)}</span>
      <span className="text-neutral-500">{e.local_date}</span>
      {tag && <span className="text-xs text-neutral-500">{tag}</span>}
      <Link to={`/activities/${e.activity_id}`} className="text-accent hover:underline">
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
    <div className="space-y-4">
      <h1 className="text-lg">Records</h1>
      <p className="text-xs text-neutral-500">All-time PRs from best efforts inside activities (outdoor, no GPS-suspect). A training effort is not a race.</p>
      <div className="grid gap-x-8 gap-y-4 md:grid-cols-2 xl:grid-cols-3">
        {data.map((r) => (
          <section key={r.label} className="min-w-0 rounded border border-border bg-panel p-3">
            <h2 className="mb-1 text-sm font-medium text-neutral-200">{r.label}</h2>
            {r.best ? (
              <>
                <div className="text-base">
                  <Line r={r} e={r.best} />
                </div>
                {r.best_race && r.best_race.activity_id !== r.best.activity_id && (
                  <div className="mt-1 text-sm">
                    <Line r={r} e={r.best_race} tag="race" />
                  </div>
                )}
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-neutral-500">Progression ({r.progression.length})</summary>
                  <ol className="mt-1 space-y-0.5 text-sm">
                    {[...r.progression].reverse().map((e) => (
                      <li key={e.activity_id}>
                        <Line r={r} e={e} tag={e.workout_type === 'race' ? 'race' : undefined} />
                      </li>
                    ))}
                  </ol>
                </details>
              </>
            ) : (
              <p className="text-sm text-neutral-500">No effort yet.</p>
            )}
          </section>
        ))}
      </div>
    </div>
  )
}
