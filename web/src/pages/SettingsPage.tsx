import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { ShoesPanel } from '../components/Shoes'
import { btn, FB, PageHead, Panel } from '../components/ui'
import { iso } from '../utils/period'

type Settings = {
  hr_max: number | null
  hr_rest: number | null
  hr_zones: number[] | null
  steady_cv_threshold: number | null
  ref_pace_s_per_km: number | null
  weather_enabled: boolean | null
  cycle_start: string | null
  races: string[] | null
  vo2max_reference: { value: number; date: string; baseline: number } | null
}

const field = 'rounded-lg border border-[#262b33] bg-[#111418] px-2.5 py-1 text-sm font-mono tabular-nums text-[#eef1f4]'
const n = (v: string) => (v.trim() === '' ? null : Number(v))
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
const parsePace = (v: string) => {
  const m = /^(\d{1,2}):([0-5]\d)$/.exec(v.trim())
  return m ? Number(m[1]) * 60 + Number(m[2]) : null
}

export default function SettingsPage() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['settings'], queryFn: () => api<Settings>('/api/settings') })
  const [hrMax, setHrMax] = useState('')
  const [hrRest, setHrRest] = useState('')
  const [zones, setZones] = useState(['', '', '', ''])
  const [cv, setCv] = useState(0.08)
  const [refPace, setRefPace] = useState('7:00')
  const [weather, setWeather] = useState(false)
  const [cycleStart, setCycleStart] = useState('')
  const [races, setRaces] = useState<string[]>([])
  const [toast, setToast] = useState('')
  const [appleVo2, setAppleVo2] = useState('')
  const [today] = useState(() => iso(new Date()))
  const [appleDate, setAppleDate] = useState(today)

  useEffect(() => {
    const d = q.data
    if (!d) return
    setHrMax(d.hr_max?.toString() ?? '')
    setHrRest(d.hr_rest?.toString() ?? '')
    setZones([0, 1, 2, 3].map((i) => d.hr_zones?.[i]?.toString() ?? ''))
    setCv(d.steady_cv_threshold ?? 0.08)
    setRefPace(mmss(d.ref_pace_s_per_km ?? 420))
    setWeather(!!d.weather_enabled)
    setCycleStart(d.cycle_start ?? '')
    setRaces(d.races ?? [])
    setAppleVo2(d.vo2max_reference?.value.toString() ?? '')
    setAppleDate(d.vo2max_reference?.date ?? today)
  }, [q.data, today])

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
  const ref = parsePace(refPace)
  const paceErr = ref == null || ref < 150 || ref > 600 ? 'Reference pace must be m:ss between 2:30 and 10:00' : ''
  const appleValue = n(appleVo2)
  const appleErr = appleValue !== null && (!Number.isFinite(appleValue) || appleValue < 14 || appleValue > 65 || !appleDate || appleDate > today) ? 'Inserisci una VO₂max tra 14 e 65 e una data non futura' : ''
  const err = zoneErr || hrErr || paceErr || appleErr

  const save = useMutation({
    mutationFn: () =>
      api<{ recompute_job_id: number | null }>('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ hr_max: max, hr_rest: rest, hr_zones: filled ? z : null, steady_cv_threshold: cv, ref_pace_s_per_km: ref, weather_enabled: weather, cycle_start: cycleStart || null, races: [...new Set(races.filter(Boolean))].sort(), vo2max_reference: appleValue === null ? null : { value: appleValue, date: appleDate } }),
      }),
    onSuccess: (r) => {
      setToast(r.recompute_job_id ? 'Saved — recomputation queued' : 'Saved')
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.invalidateQueries({ queryKey: ['/api/stats/fitness'] })
    },
    onError: (e: Error) => setToast('detail' in e && typeof e.detail === 'string' ? e.detail : e.message),
  })
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 4000)
    return () => clearTimeout(t)
  }, [toast])

  return (
    <div className="max-w-2xl space-y-4" style={{ fontFamily: FB }}>
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (!err) save.mutate()
      }}
    >
      <PageHead eyebrow="Frequenza cardiaca e soglie" title="Impostazioni" />
      {q.isError && <p className="text-sm text-red-400">Failed to load settings.</p>}
      <p role="alert" className="rounded-[14px] border border-amber-500/40 px-4 py-3 text-sm text-amber-400">
        Modifying HR zones will trigger a background recomputation of all activity metrics.
      </p>

      <Panel title="Heart rate">
        <div className="space-y-3">
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
        </div>
      </Panel>

      <Panel title="Calibrazione VO₂max · Apple Watch" className="space-y-3 text-sm">
        <p className="text-neutral-400">Inserisci il valore dell’app Salute e la sua data. La stima viene allineata a quel riferimento e continua ad aggiornarsi con le corse successive.</p>
        <div className="flex flex-wrap gap-4">
          <label className="block space-y-1">
            <span className="block text-neutral-400">VO₂max Apple Watch (ml/kg/min)</span>
            <input type="number" min={14} max={65} step="0.1" className={`${field} w-28`} placeholder="es. 43,5" value={appleVo2} onChange={(e) => setAppleVo2(e.target.value)} />
          </label>
          <label className="block space-y-1">
            <span className="block text-neutral-400">Data del valore Apple Watch</span>
            <input type="date" max={today} className={field} value={appleDate} onChange={(e) => setAppleDate(e.target.value)} />
          </label>
        </div>
        <p className="text-xs text-neutral-500">Usa una lettura recente con corse utilizzabili nei 28 giorni fino a quella data. Anche Apple Watch fornisce una stima: l’allineamento non garantisce maggiore accuratezza.</p>
        {q.data?.vo2max_reference && <p className="text-xs text-neutral-400">Riferimento salvato: {q.data.vo2max_reference.value.toLocaleString('it-IT')} del {q.data.vo2max_reference.date}. Stima di partenza: {q.data.vo2max_reference.baseline.toLocaleString('it-IT', { maximumFractionDigits: 1 })}.</p>}
        <button type="button" className="text-xs text-[#4c8dff] disabled:opacity-40" disabled={!appleVo2} onClick={() => setAppleVo2('')}>Rimuovi calibrazione</button>
        <p className="text-xs text-neutral-500">Premi Salva per applicare o rimuovere il riferimento.</p>
        {appleErr && <p className="text-sm text-red-400">{appleErr}</p>}
      </Panel>

      <Panel title="Steady runs" className="text-sm">
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
          className="w-full accent-[#4c8dff]"
          onChange={(e) => setCv(Number(e.target.value))}
        />
      </Panel>

      <Panel title="Analysis" className="space-y-3 text-sm">
        <label className="block space-y-1">
          <span className="block text-neutral-400">Reference pace for "HR at pace" (min/km)</span>
          <input inputMode="numeric" placeholder="7:00" className={`${field} w-24`} value={refPace} onChange={(e) => setRefPace(e.target.value)} />
        </label>
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-1" checked={weather} onChange={(e) => setWeather(e.target.checked)} />
          <span>
            <span className="text-neutral-200">Historical weather (Open-Meteo)</span>
            <span className="block text-xs text-neutral-500">Sends each run&apos;s start position rounded to ~1 km and its date to open-meteo.com. Used for the heat-corrected EF.</span>
          </span>
        </label>
      </Panel>

      <Panel title="Ciclo e gare" className="space-y-3 text-sm">
        <label className="block space-y-1">
          <span className="block text-neutral-400">Inizio di una 1ª settimana di carico (ciclo 3 carico + 1 scarico)</span>
          <input type="date" className={field} value={cycleStart} onChange={(e) => setCycleStart(e.target.value)} />
        </label>
        <div className="space-y-1">
          <span className="block text-neutral-400">Gare</span>
          {races.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <input type="date" className={field} value={r} onChange={(e) => setRaces(races.map((o, j) => (j === i ? e.target.value : o)))} />
              <button type="button" aria-label="Rimuovi gara" className="px-2 text-neutral-400 hover:text-red-400" onClick={() => setRaces(races.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          ))}
          <button type="button" className="text-xs text-[#4c8dff]" onClick={() => setRaces([...races, ''])}>
            + Aggiungi gara
          </button>
        </div>
        <p className="text-xs text-neutral-500">Gli avvisi di carico tengono conto della fase: niente allarme &quot;carico in calo&quot; in scarico e nei 7 giorni prima e dopo una gara. Se il ciclo slitta, aggiorna la data di inizio.</p>
      </Panel>

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={!q.data || !!err || save.isPending}
          className={btn}
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
    <ShoesPanel />
    </div>
  )
}
