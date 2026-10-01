import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type ApiError } from '../api/client'
import { btn as btnPrimary, btnGhost, FB, Fonts, PageHead, Panel } from '../components/ui'

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
type ImportResult = { mapped: number; skipped: number; duplicate: number; failed: { id: string; error: string }[] }

function ImportPanel({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState<string[]>([])
  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    setLog([])
    for (const f of Array.from(files)) {
      setLog((l) => [...l, `${f.name}: caricamento…`])
      let line: string
      try {
        const r = await api<ImportResult>('/api/imports/file', {
          method: 'POST',
          body: f,
          headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(f.name) },
        })
        line =
          `${f.name}: ${r.mapped} importate, ${r.duplicate} già presenti, ${r.skipped} non corsa` +
          (r.failed.length ? `, ${r.failed.length} errori (${r.failed[0].id}: ${r.failed[0].error})` : '')
      } catch (e) {
        line = `${f.name}: ${(e as ApiError).detail ?? (e as Error).message}`
      }
      setLog((l) => [...l.slice(0, -1), line])
    }
    setBusy(false)
    onDone()
  }
  return (
    <Panel title="Importa file" sub="Strava (export .zip o file .fit/.gpx/.tcx) · Health Auto Export (.json/.zip)">
      <div className="space-y-3">
        <label className={`${btnPrimary} cursor-pointer ${busy ? 'pointer-events-none opacity-50' : ''}`}>
          {busy ? 'Importazione…' : 'Scegli file'}
          <input
            type="file"
            multiple
            hidden
            accept=".zip,.json,.fit,.gpx,.tcx,.gz"
            disabled={busy}
            onChange={(e) => {
              upload(e.target.files)
              e.target.value = ''
            }}
          />
        </label>
        {log.map((l, i) => (
          <p key={i} role="status" className={`${mono} break-words text-xs text-neutral-400`}>
            {l}
          </p>
        ))}
      </div>
    </Panel>
  )
}

const mono = 'font-mono tabular-nums'
const btn = btnGhost
const th = 'px-2 py-1.5 text-left text-[11px] font-normal tracking-[.07em] uppercase text-[#8a93a0]'
const td = 'px-2 py-2 align-top'
const ts = (v: string | null | undefined) => (v ? new Date(v).toLocaleString('sv') : '—')

function Usage({ label, used, limit }: { label: string; used: number | null; limit: number }) {
  return (
    <div className="flex items-center gap-2 text-xs text-neutral-400">
      <span className="w-14">{label}</span>
      <progress className="h-1.5 w-40 accent-[#4c8dff]" max={limit} value={used ?? 0} />
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
    <div className="space-y-4" style={{ fontFamily: FB }}>
      <Fonts />
      <PageHead eyebrow={`Strava · ultimo sync ${ts(s?.last_sync_at)}`} title="Sync" />
      <Panel title="Strava">
        <div className="space-y-3">
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
          <a className={btnPrimary} href="/api/strava/connect">
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
        </div>
      </Panel>

      <ImportPanel onDone={() => qc.invalidateQueries()} />

      <Panel title="Recent jobs">
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
                <tr key={j.id} className="border-t border-[#262b33]">
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
      </Panel>

      <Panel title="Failed source records">
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
                <tr key={r.id} className="border-t border-[#262b33]">
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
      </Panel>
    </div>
  )
}
