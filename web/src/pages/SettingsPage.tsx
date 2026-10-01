import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

type Settings = {
  hr_max: number | null
  hr_rest: number | null
  hr_zones: number[] | null
  steady_cv_threshold: number | null
}

const field = 'rounded border border-border bg-panel px-2 py-1 text-sm font-mono tabular-nums'
const n = (v: string) => (v.trim() === '' ? null : Number(v))

export default function SettingsPage() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['settings'], queryFn: () => api<Settings>('/api/settings') })
  const [hrMax, setHrMax] = useState('')
  const [hrRest, setHrRest] = useState('')
  const [zones, setZones] = useState(['', '', '', ''])
  const [cv, setCv] = useState(0.08)
  const [toast, setToast] = useState('')

  useEffect(() => {
    const d = q.data
    if (!d) return
    setHrMax(d.hr_max?.toString() ?? '')
    setHrRest(d.hr_rest?.toString() ?? '')
    setZones([0, 1, 2, 3].map((i) => d.hr_zones?.[i]?.toString() ?? ''))
    setCv(d.steady_cv_threshold ?? 0.08)
  }, [q.data])

  const z = zones.map(n)
  const filled = z.every((v) => v !== null)
  const zoneErr =
    zones.some((v) => v.trim() !== '') && !filled
      ? 'Fill all four zone bounds'
      : filled && (z[0]! <= 0 || z.some((v, i) => i > 0 && v! <= z[i - 1]!))
        ? 'Zone bounds must be strictly ascending'
        : ''
  const max = n(hrMax)
  const rest = n(hrRest)
  const hrErr = max !== null && rest !== null && rest >= max ? 'Resting HR must be below max HR' : ''
  const err = zoneErr || hrErr

  const save = useMutation({
    mutationFn: () =>
      api<{ recompute_job_id: number | null }>('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ hr_max: max, hr_rest: rest, hr_zones: filled ? z : null, steady_cv_threshold: cv }),
      }),
    onSuccess: (r) => {
      setToast(r.recompute_job_id ? 'Saved — recomputation queued' : 'Saved')
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (e: Error) => setToast(e.message),
  })
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 4000)
    return () => clearTimeout(t)
  }, [toast])

  return (
    <form
      className="max-w-xl space-y-6"
      onSubmit={(e) => {
        e.preventDefault()
        if (!err) save.mutate()
      }}
    >
      <h1 className="text-lg">Settings</h1>
      {q.isError && <p className="text-sm text-red-400">Failed to load settings.</p>}
      <p role="alert" className="rounded border border-amber-500/40 px-3 py-2 text-sm text-amber-400">
        Modifying HR zones will trigger a background recomputation of all activity metrics.
      </p>

      <section className="space-y-3">
        <h2 className="text-sm text-neutral-400">Heart rate</h2>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="space-y-1">
            <span className="block text-neutral-400">Max HR (bpm)</span>
            <input type="number" min={100} max={250} className={`${field} w-24`} value={hrMax} onChange={(e) => setHrMax(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="block text-neutral-400">Resting HR (bpm)</span>
            <input type="number" min={20} max={120} className={`${field} w-24`} value={hrRest} onChange={(e) => setHrRest(e.target.value)} />
          </label>
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          {zones.map((v, i) => (
            <label key={i} className="space-y-1">
              <span className="block text-neutral-400">Z{i + 1} up to (bpm)</span>
              <input
                type="number"
                min={1}
                className={`${field} w-24`}
                value={v}
                onChange={(e) => setZones(zones.map((o, j) => (j === i ? e.target.value : o)))}
              />
            </label>
          ))}
        </div>
        <p className="text-xs text-neutral-500">Z5 is everything above Z4.</p>
        {err && <p className="text-sm text-red-400">{err}</p>}
      </section>

      <section className="space-y-2 text-sm">
        <label className="block text-neutral-400" htmlFor="cv">
          Steady run CV threshold <span className="font-mono tabular-nums text-neutral-200">{cv.toFixed(2)}</span>
        </label>
        <input
          id="cv"
          type="range"
          min={0.02}
          max={0.3}
          step={0.01}
          value={cv}
          className="w-full accent-blue-500"
          onChange={(e) => setCv(Number(e.target.value))}
        />
      </section>

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={!!err || save.isPending}
          className="min-h-10 rounded border border-border bg-panel px-3 py-1 text-sm hover:border-accent disabled:opacity-50 md:min-h-0"
        >
          Save
        </button>
        {toast && (
          <span role="status" className="text-xs text-neutral-400">
            {toast}
          </span>
        )}
      </div>
    </form>
  )
}
