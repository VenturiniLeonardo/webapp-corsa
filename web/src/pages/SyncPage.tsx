import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'

type Status = {
  connected: boolean
  athlete_id: number | null
  status: string | null
  last_sync_at: string | null
  rate_usage: { used_15m: number | null; used_day: number | null; limit_15m: number; limit_day: number }
}
type Job = {
  id: number
  kind: string
  status: string
  progress_done: number | null
  progress_total: number | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
}
type Failed = { id: number; external_id: string; error: string | null }

const mono = 'font-mono tabular-nums'
const btn = 'rounded border border-border bg-panel px-3 py-1 text-sm min-h-10 md:min-h-0 hover:border-accent disabled:opacity-50'
const th = 'px-2 py-1 text-left font-normal text-neutral-500'
const td = 'px-2 py-1 align-top'
const ts = (v: string | null | undefined) => (v ? new Date(v).toLocaleString('sv') : '—')

function Usage({ label, used, limit }: { label: string; used: number | null; limit: number }) {
  return (
    <div className="flex items-center gap-2 text-xs text-neutral-400">
      <span className="w-14">{label}</span>
      <progress className="h-1.5 w-40 accent-blue-500" max={limit} value={used ?? 0} />
      <span className={mono}>
        {used ?? '—'} / {limit}
      </span>
    </div>
  )
}

function badge(s: Status) {
  if (s.status === 'reauth_required') return ['Reauth required', 'text-amber-400']
  return s.connected ? ['Connected', 'text-neutral-200'] : ['Disconnected', 'text-neutral-500']
}

export default function SyncPage() {
  const qc = useQueryClient()
  const [msg, setMsg] = useState('')
  const status = useQuery({ queryKey: ['strava-status'], queryFn: () => api<Status>('/api/strava/status') })
  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api<Job[]>('/api/jobs?limit=20'),
    refetchInterval: (q) =>
      q.state.data?.some((j) => j.status === 'queued' || j.status === 'running') ? 3000 : false,
  })
  const failedJobs = (jobs.data ?? []).filter((j) => j.status === 'failed' || j.error)
  const failed = useQuery({
    queryKey: ['failed-records', failedJobs.map((j) => j.id)],
    queryFn: async () =>
      (await Promise.all(failedJobs.map((j) => api<{ failed_records: Failed[] }>(`/api/jobs/${j.id}`)))).flatMap(
        (d) => d.failed_records,
      ),
    enabled: jobs.isSuccess,
  })

  const done = (text: string) => () => {
    setMsg(text)
    qc.invalidateQueries({ queryKey: ['jobs'] })
    qc.invalidateQueries({ queryKey: ['strava-status'] })
    qc.invalidateQueries({ queryKey: ['failed-records'] })
  }
  const fail = (e: Error) => setMsg(e.message)
  const post = (path: string, body?: unknown) =>
    api(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
  const sync = useMutation({ mutationFn: () => post('/api/sync'), onSuccess: done('Sync queued'), onError: fail })
  const disconnect = useMutation({
    mutationFn: () => post('/api/strava/disconnect', { purge_data: false }),
    onSuccess: done('Disconnected'),
    onError: fail,
  })
  const retry = useMutation({
    mutationFn: (id: number) => post(`/api/source-records/${id}/retry`),
    onSuccess: done('Retry queued'),
    onError: fail,
  })

  const s = status.data
  const [label, color] = s ? badge(s) : ['…', 'text-neutral-500']

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h1 className="text-lg">Sync</h1>
        {status.isError && <p className="text-sm text-red-400">Failed to load Strava status.</p>}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span className={color}>{label}</span>
          <span className="text-neutral-400">
            Athlete <span className={mono}>{s?.athlete_id ?? '—'}</span>
          </span>
          <span className="text-neutral-400">
            Last sync <span className={mono}>{ts(s?.last_sync_at)}</span>
          </span>
        </div>
        {s && (
          <div className="space-y-1">
            <Usage label="15 min" used={s.rate_usage.used_15m} limit={s.rate_usage.limit_15m} />
            <Usage label="Day" used={s.rate_usage.used_day} limit={s.rate_usage.limit_day} />
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <a className={`${btn} inline-flex items-center`} href="/api/strava/connect">
            Connect Strava
          </a>
          <button
            className={btn}
            disabled={!s?.connected || disconnect.isPending}
            onClick={() => window.confirm('Disconnect Strava?') && disconnect.mutate()}
          >
            Disconnect
          </button>
          <button className={btn} disabled={!s?.connected || sync.isPending} onClick={() => sync.mutate()}>
            Sync Now
          </button>
        </div>
        {msg && (
          <p role="status" className="text-xs text-neutral-400">
            {msg}
          </p>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm text-neutral-400">Recent jobs</h2>
        {jobs.isError && <p className="text-sm text-red-400">Failed to load jobs.</p>}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className={th}>Kind</th>
                <th className={th}>Status</th>
                <th className={th}>Progress</th>
                <th className={th}>Created</th>
                <th className={th}>Finished</th>
                <th className={th}>Error</th>
              </tr>
            </thead>
            <tbody className={mono}>
              {jobs.data?.map((j) => (
                <tr key={j.id} className="border-t border-border">
                  <td className={td}>{j.kind}</td>
                  <td className={`${td} ${j.status === 'failed' ? 'text-red-400' : ''}`}>{j.status}</td>
                  <td className={td}>{j.progress_total ? `${j.progress_done ?? 0}/${j.progress_total}` : '—'}</td>
                  <td className={td}>{ts(j.created_at)}</td>
                  <td className={td}>{ts(j.finished_at)}</td>
                  <td className={`${td} max-w-xs break-words text-red-400`}>{j.error ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-sm text-neutral-400">Failed source records</h2>
        {failed.data?.length === 0 && <p className="text-sm text-neutral-500">None.</p>}
        {!!failed.data?.length && (
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className={th}>External ID</th>
                <th className={th}>Error</th>
                <th className={th} />
              </tr>
            </thead>
            <tbody>
              {failed.data.map((r) => (
                <tr key={r.id} className="border-t border-border">
                  <td className={`${td} ${mono}`}>{r.external_id}</td>
                  <td className={`${td} break-words text-red-400`}>{r.error}</td>
                  <td className={`${td} text-right`}>
                    <button className={btn} disabled={retry.isPending} onClick={() => retry.mutate(r.id)}>
                      Retry
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
