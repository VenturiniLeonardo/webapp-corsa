import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api, queryClient } from '../api/client'
import { formatDuration } from '../utils/formatters'
import { BLUE, MUTED, SOFT, btnGhost, field, surface } from './ui'

type Profile = { coverage: { from_date: string; to_date: string }[]; sensor: string; sensor_id: string | null; hr_threshold: number | null; hr_max_measured: number | null; taper_start: string | null }
type ActivityInput = { official_verified: boolean; official_distance_m: number | null; official_elapsed_s: number | null; flat_confirmed: boolean; test_verified: boolean; test_distance_m: number | null; test_rpe: number | null; sensor: string | null; sensor_id: string | null; baseline_volume_km: number | null; baseline_long_km: number | null }
type Activity = { id: number; date: string; name: string | null; race: boolean; distance_m: number; elapsed_s: number; test_distances: number[] }
type Inputs = { profile: Profile; activities: Record<string, ActivityInput>; available_activities: Activity[]; updated_at?: string }
const emptyActivity: ActivityInput = { official_verified: false, official_distance_m: null, official_elapsed_s: null, flat_confirmed: false, test_verified: false, test_distance_m: null, test_rpe: null, sensor: null, sensor_id: null, baseline_volume_km: null, baseline_long_km: null }
const number = (value: string) => value === '' ? null : Number(value)
const errorText = (error: unknown) => error instanceof ApiError && typeof error.detail === 'string' ? error.detail : 'Salvataggio non riuscito. Controlla i dati e riprova.'
const refresh = () => {
  void queryClient.invalidateQueries({ queryKey: ['/api/records/prediction-inputs'] })
  void queryClient.invalidateQueries({ queryKey: ['/api/records/predictions'] })
}

function ProfileForm({ value }: { value: Profile }) {
  const [data, setData] = useState(value)
  const [confirmed, setConfirmed] = useState(value.coverage.length > 0)
  const save = useMutation({ mutationFn: () => api('/api/records/prediction-inputs/profile', { method: 'PUT', body: JSON.stringify({ ...data, coverage: data.coverage.filter(row => row.from_date && row.to_date) }) }), onSuccess: refresh })
  const input = (key: 'hr_threshold' | 'hr_max_measured', label: string) => <label className="text-xs" style={{ color: SOFT }}>{label}<input className={`${field} mt-1 w-full`} type="number" min={80} max={250} value={data[key] ?? ''} onChange={event => setData({ ...data, [key]: number(event.target.value) })} /></label>
  return <form className="space-y-3" onSubmit={event => { event.preventDefault(); save.mutate() }}>
    <p className="text-sm" style={{ color: SOFT }}>Conferma solo dati che conosci. I valori mancanti restano tali nel calcolo.</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-xs" style={{ color: SOFT }}>Sensore FC abituale<select className={`${field} mt-1 w-full`} value={data.sensor} onChange={event => setData({ ...data, sensor: event.target.value })}><option value="unknown">Sconosciuto</option><option value="optical">Ottico al polso/braccio</option><option value="chest">Fascia elettrica</option></select></label>
      <label className="text-xs" style={{ color: SOFT }}>Identificativo sensore (facoltativo)<input className={`${field} mt-1 w-full`} maxLength={100} value={data.sensor_id ?? ''} onChange={event => setData({ ...data, sensor_id: event.target.value || null })} /></label>
      {input('hr_threshold', 'FC soglia misurata (bpm)')}{input('hr_max_measured', 'FC massima misurata (bpm)')}
      <label className="text-xs" style={{ color: SOFT }}>Inizio scarico pre-gara dichiarato<input className={`${field} mt-1 w-full`} type="date" value={data.taper_start ?? ''} onChange={event => setData({ ...data, taper_start: event.target.value || null })} /></label>
    </div>
    <p className="text-xs" style={{ color: MUTED }}>Periodi per cui tutte le corse sono state importate. Le giornate senza attività verranno considerate riposo.</p>
    {data.coverage.map((row, i) => <div className="flex flex-wrap items-end gap-2" key={i}>
      <label className="min-w-0 flex-1 text-xs">Storico completo dal<input type="date" className={`${field} mt-1 w-full`} required={!!row.to_date} value={row.from_date} onChange={event => setData({ ...data, coverage: data.coverage.map((r, j) => j === i ? { ...r, from_date: event.target.value } : r) })} /></label>
      <label className="min-w-0 flex-1 text-xs">Al<input type="date" className={`${field} mt-1 w-full`} required={!!row.from_date} min={row.from_date} value={row.to_date} onChange={event => setData({ ...data, coverage: data.coverage.map((r, j) => j === i ? { ...r, to_date: event.target.value } : r) })} /></label>
      <button type="button" className={btnGhost} onClick={() => setData({ ...data, coverage: data.coverage.filter((_, j) => j !== i) })}>Rimuovi periodo</button>
    </div>)}
    <button className={btnGhost} type="button" onClick={() => setData({ ...data, coverage: [...data.coverage, { from_date: '', to_date: '' }] })}>Aggiungi periodo completo</button>
    {data.coverage.some(r => r.from_date && r.to_date) && <label className="flex items-start gap-2 text-xs"><input type="checkbox" required checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />Confermo la completezza delle importazioni per i periodi indicati.</label>}
    {save.isError && <p role="alert" className="text-xs text-red-400">{errorText(save.error)}</p>}
    {save.isSuccess && <p role="status" className="text-xs" style={{ color: BLUE }}>Profilo salvato.</p>}
    <button className={btnGhost} disabled={save.isPending} type="submit">{save.isPending ? 'Salvataggio…' : 'Salva dati del profilo'}</button>
  </form>
}

function ActivityForm({ activity, value }: { activity: Activity; value: ActivityInput }) {
  const [data, setData] = useState(value)
  const [timeText, setTimeText] = useState(value.official_elapsed_s == null ? '' : `${Math.floor(value.official_elapsed_s / 3600)}:${String(Math.floor(value.official_elapsed_s / 60) % 60).padStart(2, '0')}:${String(Math.round(value.official_elapsed_s) % 60).padStart(2, '0')}`)
  const [timeError, setTimeError] = useState(false)
  const save = useMutation({ mutationFn: (body: ActivityInput) => api(`/api/records/prediction-inputs/activities/${activity.id}`, { method: 'PUT', body: JSON.stringify(body) }), onSuccess: refresh })
  const num = (key: 'test_rpe' | 'baseline_volume_km' | 'baseline_long_km', label: string, max: number) => <label className="text-xs" style={{ color: SOFT }}>{label}<input type="number" step="0.1" min={0} max={max} className={`${field} mt-1 w-full`} value={data[key] ?? ''} onChange={event => setData({ ...data, [key]: number(event.target.value) })} /></label>
  return <form className="space-y-3 border-t border-[#303640] pt-3" onSubmit={event => {
    event.preventDefault()
    const parts = timeText.split(':').map(Number)
    const valid = !timeText || (/^\d{1,2}:\d{2}(:\d{2})?$/.test(timeText) && parts.every(Number.isFinite) && parts.at(-1)! < 60 && (parts.length === 2 || parts[1] < 60))
    setTimeError(!valid)
    if (!valid) return
    const seconds = timeText ? parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1] : null
    save.mutate({ ...data, official_elapsed_s: seconds })
  }}>
    <p className="text-xs" style={{ color: MUTED }}>Importato: {(activity.distance_m / 1000).toFixed(2)} km in {formatDuration(activity.elapsed_s)}. Le conferme seguenti sono usate solo dalle previsioni.</p>
    {activity.race && <>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={data.official_verified} onChange={event => setData({ ...data, official_verified: event.target.checked })} />Confermo distanza e tempo della gara ufficiale</label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs">Distanza ufficiale (km)<input className={`${field} mt-1 w-full`} type="number" step="0.0001" min={3} max={100} required={data.official_verified} value={data.official_distance_m == null ? '' : data.official_distance_m / 1000} onChange={event => setData({ ...data, official_distance_m: event.target.value ? Number(event.target.value) * 1000 : null })} /></label>
        <label className="text-xs">Tempo ufficiale (MM:SS o H:MM:SS)<input className={`${field} mt-1 w-full`} placeholder="Vuoto: usa il tempo importato" value={timeText} onChange={event => setTimeText(event.target.value)} /></label>
      </div>
    </>}
    <label className="flex gap-2 text-sm"><input type="checkbox" checked={data.flat_confirmed} onChange={event => setData({ ...data, flat_confirmed: event.target.checked })} />Confermo che il percorso del riferimento è pianeggiante</label>
    {!activity.race && <>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={data.test_verified} onChange={event => setData({ ...data, test_verified: event.target.checked })} />Confermo un test continuo massimale/quasi massimale</label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs">Tratto del test<select className={`${field} mt-1 w-full`} required={data.test_verified} value={data.test_distance_m ?? ''} onChange={event => setData({ ...data, test_distance_m: number(event.target.value) })}><option value="">Seleziona distanza</option>{activity.test_distances.map(distance => <option key={distance} value={distance}>{distance / 1000} km</option>)}</select></label>
        {num('test_rpe', 'RPE del test (0–10; minimo 9 per usarlo)', 10)}
      </div>
    </>}
    <div className="grid gap-3 sm:grid-cols-2">
      {num('baseline_volume_km', 'Volume verificato nelle 8 settimane prima (km/settimana)', 300)}
      {num('baseline_long_km', 'Lungo massimo verificato nelle 8 settimane prima (km)', 150)}
      <label className="text-xs">Sensore per questa corsa<select className={`${field} mt-1 w-full`} value={data.sensor ?? ''} onChange={event => setData({ ...data, sensor: event.target.value || null })}><option value="">Come profilo</option><option value="unknown">Sconosciuto</option><option value="optical">Ottico</option><option value="chest">Fascia elettrica</option></select></label>
      <label className="text-xs">Identificativo sensore della corsa<input className={`${field} mt-1 w-full`} maxLength={100} value={data.sensor_id ?? ''} onChange={event => setData({ ...data, sensor_id: event.target.value || null })} /></label>
    </div>
    {timeError && <p role="alert" className="text-xs text-red-400">Formato tempo non valido.</p>}
    {save.isError && <p role="alert" className="text-xs text-red-400">{errorText(save.error)}</p>}
    <button className={btnGhost} type="submit" disabled={save.isPending}>{save.isPending ? 'Salvataggio…' : 'Salva conferme della corsa'}</button>
  </form>
}

export default function PredictionInputs() {
  const [selected, setSelected] = useState('')
  const { data, isError } = useQuery({ queryKey: ['/api/records/prediction-inputs'], queryFn: () => api<Inputs>('/api/records/prediction-inputs') })
  const activity = data?.available_activities.find(row => row.id === Number(selected))
  return <details className={surface}>
    <summary className="cursor-pointer text-sm" style={{ color: SOFT }}>Dati e conferme per le previsioni</summary>
    {isError && <p className="mt-3 text-sm text-red-400">Impossibile caricare le conferme.</p>}
    {!data && !isError && <p className="mt-3 text-xs" style={{ color: MUTED }}>Caricamento…</p>}
    {data && <div className="mt-4 space-y-5">
      <ProfileForm key={`profile-${data.updated_at ?? 'new'}`} value={data.profile} />
      <div className="space-y-3 border-t border-[#303640] pt-4">
        <label className="text-xs">Gara o allenamento da confermare<select className={`${field} mt-1 w-full`} value={selected} onChange={event => setSelected(event.target.value)}><option value="">Seleziona una corsa</option>{data.available_activities.map(row => <option key={row.id} value={row.id}>{row.date} · {row.name || 'Corsa'} · {row.race ? 'gara' : 'allenamento'}</option>)}</select></label>
        {activity && <ActivityForm key={`${selected}-${data.updated_at ?? 'new'}`} activity={activity} value={{ ...emptyActivity, ...data.activities?.[selected] }} />}
      </div>
    </div>}
  </details>
}
