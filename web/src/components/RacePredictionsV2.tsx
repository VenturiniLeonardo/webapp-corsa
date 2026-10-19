import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import { formatDuration, formatPace } from '../utils/formatters'
import PredictionInputs from './PredictionInputs'
import { BLUE, FC, FM, MUTED, SOFT, btnGhost, field, surface } from './ui'

type Confidence = 'medium' | 'low' | 'unavailable'
type Scenario = { elapsed_s: number; pace_s_per_km: number; pace_low_s_per_km: number; pace_high_s_per_km: number; elapsed_low_s: number; elapsed_high_s: number; heat_adjustment_pct: number; confidence: Confidence; weather_status: string; temp_c: number; dew_point_c: number; extreme: boolean }
type Source = { activity_id: number; date: string; kind: string; distance_m: number; elapsed_s: number; weight: number; hr_factor: number; preparation_factor: number; official: boolean; flags: string[] }
type Prediction = { distance_m: number; label: string; elapsed_s: number | null; elapsed_low_s?: number; elapsed_high_s?: number; pace_s_per_km: number | null; pace_low_s_per_km: number | null; pace_high_s_per_km: number | null; confidence: Confidence; reasons: string[]; sources: Source[]; scenario: Scenario | null; coherence_change_pct?: number; weighted_age_days?: number }
type Predictions = { model_version: string; input_hash: string; from_date: string; to_date: string; runs_8w: number; hr_runs_8w: number; avg_weekly_distance_m: number; longest_run_m: number; coverage_fraction: number; taper_applied: boolean; notices: string[]; hr_trend: { reliability: number; pairs: number; support: number }; weather: { status: string; message: string; location_name: string | null; race_date: string | null; race_month: number | null; race_time: string; timezone: string; time_assumed: boolean; sample_years?: number }; items: Prediction[] }
type Location = { id: number; label: string; latitude: number; longitude: number; timezone?: string }
type RaceChoice = { mode: 'standard' | 'date' | 'month'; date: string; month: string; time: string; timezone: string }
const confidence = { medium: 'Affidabilità media', low: 'Affidabilità bassa', unavailable: 'Dati insufficienti' }
const flagText: Record<string, string> = { grade_missing: 'Pendenza non disponibile', weather_missing: 'Meteo storico non disponibile', baseline_missing: 'Preparazione precedente non verificabile', stops_unknown: 'Durata delle soste non disponibile' }
const monthNames = ['Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno', 'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre']
const locationKey = 'corsa.predictionLocation'
const raceKey = 'corsa.predictionRaceV2'
const defaultRace: RaceChoice = { mode: 'standard', date: '', month: '11', time: '09:00', timezone: 'Europe/Rome' }
const day = (value: string) => new Date(`${value}T12:00:00`).toLocaleDateString('it-IT', { day: 'numeric', month: 'short', year: 'numeric' })

function savedLocation(): Location | null {
  try {
    const value = JSON.parse(localStorage.getItem(locationKey) ?? 'null')
    return value && Number.isInteger(value.id) && typeof value.label === 'string' && value.label.length <= 160
      && Number.isFinite(value.latitude) && Math.abs(value.latitude) <= 90
      && Number.isFinite(value.longitude) && Math.abs(value.longitude) <= 180 ? value : null
  } catch { return null }
}
function savedRace(): RaceChoice {
  try {
    const value = JSON.parse(localStorage.getItem(raceKey) ?? 'null')
    if (value && ['standard', 'date', 'month'].includes(value.mode) && ['date', 'month', 'time', 'timezone'].every(k => typeof value[k] === 'string')) return value
  } catch { /* Browser storage is optional. */ }
  return defaultRace
}
function save(key: string, value: unknown) {
  try {
    if (value == null) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
  } catch { /* Keep the current session usable. */ }
}
function Range({ lo, hi, timeLo, timeHi }: { lo: number | null; hi: number | null; timeLo?: number; timeHi?: number }) {
  if (lo == null || hi == null) return null
  return <div className="mt-2 text-xs" style={{ color: SOFT }}>
    <p>Fascia indicativa</p>
    <p className="font-mono tabular-nums">{formatPace(Math.floor(lo))} – {formatPace(Math.ceil(hi))}</p>
    {timeLo != null && timeHi != null && <p className="mt-1 font-mono tabular-nums">{formatDuration(Math.floor(timeLo))} – {formatDuration(Math.ceil(timeHi))}</p>}
  </div>
}

export default function RacePredictionsV2() {
  const [location, setLocation] = useState<Location | null>(savedLocation)
  const [race, setRace] = useState<RaceChoice>(savedRace)
  const [draft, setDraft] = useState<RaceChoice>(savedRace)
  const [search, setSearch] = useState('')
  const [term, setTerm] = useState('')
  const [exportError, setExportError] = useState(false)
  const locations = useQuery({ queryKey: ['/api/records/predictions/locations', term], queryFn: () => api<Location[]>(`/api/records/predictions/locations?q=${encodeURIComponent(term)}`), enabled: term.length >= 2, staleTime: 3600000 })
  const query = new URLSearchParams()
  if (location) {
    query.set('latitude', String(location.latitude)); query.set('longitude', String(location.longitude)); query.set('location_name', location.label)
  }
  if (race.mode === 'date' && race.date) query.set('race_date', race.date)
  if (race.mode === 'month') query.set('race_month', race.month)
  if (race.mode !== 'standard') {
    if (race.time) query.set('race_time', race.time)
    query.set('race_timezone', race.timezone)
  }
  const params = query.size ? `?${query}` : ''
  const { data, isError, error, isFetching, refetch } = useQuery({ queryKey: ['/api/records/predictions', params], queryFn: () => api<Predictions>(`/api/records/predictions${params}`), staleTime: 15 * 60000, refetchInterval: 15 * 60000 })
  function chooseLocation(value: Location | null) {
    setLocation(value); save(locationKey, value); setTerm(''); setSearch('')
    if (value?.timezone) {
      setDraft(previous => ({ ...previous, timezone: value.timezone! }))
      if (race.mode !== 'standard') {
        const next = { ...race, timezone: value.timezone }
        setRace(next); save(raceKey, next)
      }
    }
  }
  async function exportSnapshot() {
    setExportError(false)
    try {
      const snapshot = await api(`/api/records/predictions${params}${params ? '&' : '?'}include_snapshot=true`)
      const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a'); link.href = url; link.download = 'previsioni-v2-dati-calcolo.json'; link.click()
      URL.revokeObjectURL(url)
    } catch { setExportError(true) }
  }
  return <section aria-labelledby="race-predictions-title" className="space-y-4 border-t border-[#262b33] pt-6">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p className="text-[11px] tracking-[.08em] uppercase" style={{ fontFamily: FM, color: MUTED }}>La tua forma attuale · modello v2</p>
        <h2 id="race-predictions-title" className="text-[30px] font-semibold uppercase" style={{ fontFamily: FC }}>Previsione passo gara</h2>
        <p className="text-[13px]" style={{ color: SOFT }}>Stime standard su strada pianeggiante, con storico e FC delle ultime 8 settimane.</p>
      </div>
      <button className={btnGhost} disabled={isFetching} onClick={() => void refetch()}>{isFetching ? 'Aggiornamento…' : 'Aggiorna stime'}</button>
    </div>
    <div className={`${surface} space-y-4`}>
      <p className="text-sm" style={{ color: SOFT }}>Località scenario: <strong className="text-[#eef1f4]">{location?.label ?? 'Automatica · ultima corsa GPS'}</strong></p>
      {location && <button className={btnGhost} onClick={() => chooseLocation(null)}>Usa ultima corsa GPS</button>}
      <form className="flex flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); if (search.trim().length >= 2) { if (term === search.trim()) void locations.refetch(); else setTerm(search.trim()) } }}>
        <label className="min-w-0 flex-1 text-xs" style={{ color: MUTED }}>Città o località per il calcolo<input className={`${field} mt-1 w-full`} value={search} minLength={2} maxLength={100} placeholder="Es. Torino, Italia" onChange={event => { setSearch(event.target.value); setTerm('') }} /></label>
        <button className={btnGhost} disabled={search.trim().length < 2 || locations.isFetching} type="submit">{locations.isFetching ? 'Ricerca…' : 'Cerca località'}</button>
      </form>
      {term && locations.isError && <p role="alert" className="text-xs text-red-400">Ricerca località non disponibile. Riprova.</p>}
      {term && locations.data?.length === 0 && <p className="text-xs" style={{ color: SOFT }}>Nessuna località trovata. Prova città e paese.</p>}
      {term && locations.data && locations.data.length > 0 && <ul aria-label="Località trovate">{locations.data.map(place => <li key={place.id}><button className="min-h-10 w-full rounded-lg px-3 py-2 text-left text-sm break-words hover:bg-[#262b33]" type="button" onClick={() => chooseLocation(place)}>{place.label}</button></li>)}</ul>}
      <form className="space-y-3 border-t border-[#303640] pt-3" onSubmit={event => { event.preventDefault(); setRace(draft); save(raceKey, draft) }}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs">Scenario meteo<select className={`${field} mt-1 w-full`} value={draft.mode} onChange={event => setDraft({ ...draft, mode: event.target.value as RaceChoice['mode'] })}><option value="standard">Solo condizioni standard</option><option value="date">Data della gara</option><option value="month">Climatologia del mese</option></select></label>
          {draft.mode === 'date' && <label className="text-xs">Data gara<input className={`${field} mt-1 w-full`} type="date" required value={draft.date} onChange={event => setDraft({ ...draft, date: event.target.value })} /></label>}
          {draft.mode === 'month' && <label className="text-xs">Mese gara<select className={`${field} mt-1 w-full`} value={draft.month} onChange={event => setDraft({ ...draft, month: event.target.value })}>{monthNames.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}</select></label>}
          {draft.mode !== 'standard' && <>
            <label className="text-xs">Ora locale di partenza<input className={`${field} mt-1 w-full`} type="time" value={draft.time} onChange={event => setDraft({ ...draft, time: event.target.value })} /></label>
            <label className="text-xs">Fuso orario IANA<input className={`${field} mt-1 w-full`} maxLength={80} required value={draft.timezone} onChange={event => setDraft({ ...draft, timezone: event.target.value })} /></label>
          </>}
        </div>
        <button className={btnGhost} type="submit">Applica scenario</button>
        <p className="text-xs" style={{ color: MUTED }}>La località e il meteo della gara modificano solo lo scenario aggiuntivo. La forma atletica resta quella di oggi. Le scelte vengono ricordate su questo dispositivo.</p>
      </form>
    </div>
    {isError && <p role="alert" className="text-sm text-red-400">{error instanceof ApiError && typeof error.detail === 'string' ? error.detail : 'Impossibile aggiornare le previsioni. Riprova con “Aggiorna stime”.'}</p>}
    {!data && !isError && <p role="status" className="text-sm" style={{ color: MUTED }}>Calcolo delle previsioni{race.mode !== 'standard' ? ' e dello scenario meteo' : ''}…</p>}
    {data && <>
      <div className={`${surface} flex flex-wrap gap-x-8 gap-y-3 text-[13px]`}>
        <div><p style={{ color: MUTED }}>{day(data.from_date)} – {day(data.to_date)}</p><p>{data.runs_8w} corse · {data.hr_runs_8w} con FC · {(data.avg_weekly_distance_m / 1000).toFixed(1)} km/settimana</p></div>
        <div><p style={{ color: MUTED }}>Lungo massimo</p><p>{(data.longest_run_m / 1000).toFixed(1)} km</p></div>
        <div><p style={{ color: MUTED }}>Supporto del segnale FC</p><p>{(data.hr_trend.reliability * 100).toFixed(0)}% · {data.hr_trend.pairs} confronti omogenei</p></div>
        <div><p style={{ color: MUTED }}>Copertura dello storico</p><p>{(data.coverage_fraction * 100).toFixed(0)}% confermato{data.taper_applied ? ' · scarico dichiarato' : ''}</p></div>
      </div>
      <div className={surface}>
        <p className="text-sm" style={{ color: SOFT }}>{data.weather.message}</p>
        {data.weather.status !== 'no_date' && <p className="mt-1 text-xs" style={{ color: MUTED }}>{data.weather.location_name} · {data.weather.race_date ? day(data.weather.race_date) : data.weather.race_month ? monthNames[data.weather.race_month - 1] : ''} · {data.weather.race_time} ({data.weather.timezone}){data.weather.time_assumed ? ' · ora convenzionale' : ''}{data.weather.sample_years ? ` · ${data.weather.sample_years} anni di campioni` : ''}</p>}
        {data.weather.status === 'disabled' && <Link className="mt-2 inline-block text-xs underline" to="/settings">Attiva il meteo nelle Impostazioni</Link>}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {data.items.map(item => <article className={surface} key={item.distance_m}>
          <h3 className="text-[25px] font-semibold uppercase" style={{ fontFamily: FC }}>{item.label}</h3>
          <p className="mt-1 text-[10px] uppercase" style={{ color: item.confidence === 'medium' ? SOFT : '#d9b779' }}>{confidence[item.confidence]}</p>
          <p className="mt-3 text-xs" style={{ color: MUTED }}>Condizioni standard · piano</p>
          <p className="mt-1 font-mono text-[30px] leading-tight tabular-nums" style={{ color: BLUE }}>{formatPace(item.pace_s_per_km)}</p>
          <p className="mt-1 font-mono text-sm tabular-nums">{item.elapsed_s == null ? 'Tempo non stimabile' : formatDuration(item.elapsed_s)}</p>
          <Range lo={item.pace_low_s_per_km} hi={item.pace_high_s_per_km} timeLo={item.elapsed_low_s} timeHi={item.elapsed_high_s} />
          {item.scenario && <div className="mt-4 border-t border-[#303640] pt-3">
            <p className="text-xs" style={{ color: SOFT }}>{item.scenario.weather_status === 'forecast' ? 'Scenario gara · previsione meteo' : 'Scenario gara · climatologia'}</p>
            <p className="mt-1 font-mono text-xl tabular-nums">{formatPace(item.scenario.pace_s_per_km)}</p>
            <p className="font-mono text-sm tabular-nums">{formatDuration(item.scenario.elapsed_s)}</p>
            <Range lo={item.scenario.pace_low_s_per_km} hi={item.scenario.pace_high_s_per_km} timeLo={item.scenario.elapsed_low_s} timeHi={item.scenario.elapsed_high_s} />
            <p className="mt-2 text-xs" style={{ color: MUTED }}>{item.scenario.temp_c.toFixed(1)} °C · rugiada {item.scenario.dew_point_c.toFixed(1)} °C · caldo +{item.scenario.heat_adjustment_pct.toFixed(1)}%</p>
            <p className="mt-1 text-[10px] uppercase" style={{ color: '#d9b779' }}>{confidence[item.scenario.confidence]}{item.scenario.extreme ? ' · condizioni estreme' : ''}</p>
          </div>}
          {item.reasons.map(reason => <p key={reason} className="mt-2 text-xs" style={{ color: MUTED }}>{reason}</p>)}
          {item.sources.length > 0 && <details className="mt-4 text-xs">
            <summary className="cursor-pointer" style={{ color: SOFT }}>Riferimenti e correzioni ({item.sources.length})</summary>
            <p className="mt-2" style={{ color: MUTED }}>Età media ponderata: {item.weighted_age_days?.toFixed(0)} giorni.</p>
            {item.sources.map(source => <div key={`${source.activity_id}-${source.distance_m}`} className="mt-3 space-y-1">
              <Link className="hover:underline" style={{ color: BLUE }} to={`/activities/${source.activity_id}`}>{source.kind === 'race' ? 'Gara' : 'Test'} · {day(source.date)} · {(source.distance_m / 1000).toFixed(2)} km</Link>
              <p>Peso {(source.weight * 100).toFixed(1)}%{source.official ? ' · distanza ufficiale' : ' · distanza GPS'}</p>
              <p style={{ color: MUTED }}>Correzione preparazione +{((source.preparation_factor - 1) * 100).toFixed(1)}% · fattore forma FC {source.hr_factor.toFixed(4)}</p>
              {source.flags.map(flag => <p key={flag} style={{ color: MUTED }}>{flagText[flag] ?? flag}</p>)}
            </div>)}
          </details>}
        </article>)}
      </div>
      {data.notices.length > 0 && <ul className="space-y-1 text-xs" style={{ color: SOFT }}>{data.notices.map(note => <li key={note}>{note}</li>)}</ul>}
      <details className={surface}>
        <summary className="cursor-pointer text-sm" style={{ color: SOFT }}>Come leggere le previsioni</summary>
        <div className="mt-3 space-y-2 text-[13px]" style={{ color: SOFT }}>
          <p>Le condizioni standard permettono di confrontare la forma senza il meteo del momento. Lo scenario usa una previsione meteo vicina alla data gara, oppure la climatologia del periodo. Non presume miglioramenti futuri di allenamento.</p>
          <p>Gare ufficiali confermate e test di intensità verificabile alimentano Riegel. Pendenza e caldo dei riferimenti sono normalizzati quando i dati lo consentono. Il segnale FC viene attenuato in base al supporto di sensori e condizioni omogenee.</p>
          <p>Le quattro distanze vengono rese coerenti. Le fasce sono più ampie sul lato lento, soprattutto in maratona: sono margini indicativi, non intervalli statistici validati.</p>
          <p>Il modello v2 usa parametri iniziali da calibrare. Storico incompleto, distanza non confermata e test non qualificati restano espliciti. Vento, sole, alimentazione e percorso di gara specifico non sono modellati.</p>
          <p>Dati meteo: <a className="underline" href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a>. Località: <a className="underline" href="https://www.geonames.org/" target="_blank" rel="noreferrer">GeoNames</a>. Dati e parametri del calcolo sono esportabili per una verifica.</p>
          <button className={btnGhost} onClick={() => void exportSnapshot()}>Esporta dati del calcolo</button>
          {exportError && <p className="text-red-400" role="alert">Esportazione non riuscita.</p>}
          <p className="break-all font-mono text-[10px]" style={{ color: MUTED }}>{data.model_version} · {data.input_hash.slice(0, 16)}</p>
        </div>
      </details>
    </>}
    <PredictionInputs />
  </section>
}
