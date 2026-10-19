import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { BLUE, FC, FM, MUTED, SOFT, btnGhost, surface } from './ui'
import { formatDuration, formatPace } from '../utils/formatters'

type Prediction = {
  distance_m: number
  label: string
  elapsed_s: number | null
  pace_s_per_km: number | null
  pace_low_s_per_km: number | null
  pace_high_s_per_km: number | null
  confidence: 'medium' | 'low' | 'unavailable'
  reasons: string[]
  anchor_activity_id: number | null
}
type Predictions = {
  from_date: string
  to_date: string
  runs_8w: number
  hr_runs_8w: number
  steady_hr_runs_8w: number
  hr_trend_applied: boolean
  hr_efficiency_change_pct: number | null
  avg_weekly_distance_m: number
  longest_run_m: number
  heat_adjustment_pct: number
  notices: string[]
  weather: {
    status: 'current' | 'unavailable' | 'disabled' | 'no_location'
    temp_c: number | null
    dew_point_c: number | null
    observed_at: string | null
    location_activity_id: number | null
  }
  items: Prediction[]
}

const confidence = { medium: 'Affidabilità media', low: 'Affidabilità bassa', unavailable: 'Dati insufficienti' }
const day = (value: string) => new Date(`${value}T12:00:00`).toLocaleDateString('it-IT', { day: 'numeric', month: 'short' })

export default function RacePredictions() {
  const { data, isError, isFetching, refetch } = useQuery({
    queryKey: ['/api/records/predictions'],
    queryFn: () => api<Predictions>('/api/records/predictions'),
    staleTime: 15 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
  })
  return (
    <section aria-labelledby="race-predictions-title" className="space-y-4 border-t border-[#262b33] pt-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] tracking-[.08em] uppercase" style={{ fontFamily: FM, color: MUTED }}>La tua forma attuale · stima</p>
          <h2 id="race-predictions-title" className="text-[30px] font-semibold uppercase" style={{ fontFamily: FC }}>Previsione passo gara</h2>
          <p className="text-[13px]" style={{ color: SOFT }}>Storico delle prestazioni, FC e preparazione delle ultime 8 settimane, meteo attuale.</p>
        </div>
        <button className={btnGhost} disabled={isFetching} onClick={() => void refetch()}>
          {isFetching ? 'Aggiornamento…' : 'Aggiorna stime'}
        </button>
      </div>
      {isError && <p role="alert" className="text-sm text-red-400">Impossibile aggiornare le previsioni. Riprova con “Aggiorna stime”.</p>}
      {!data && !isError && <p role="status" className="text-sm" style={{ color: MUTED }}>Calcolo delle previsioni e recupero del meteo…</p>}
      {data && <>
        <div className={`${surface} flex flex-wrap gap-x-8 gap-y-3 text-[13px]`}>
          <div><p style={{ color: MUTED }}>{day(data.from_date)} – {day(data.to_date)} · 8 settimane</p>
            <p>{data.runs_8w} corse · {data.hr_runs_8w} con FC · {(data.avg_weekly_distance_m / 1000).toFixed(1)} km/settimana</p></div>
          <div><p style={{ color: MUTED }}>Lungo massimo</p><p>{(data.longest_run_m / 1000).toFixed(1)} km</p></div>
          <div><p style={{ color: MUTED }}>Andamento efficienza passo/FC</p>
            <p>{data.hr_trend_applied && data.hr_efficiency_change_pct != null
              ? `${data.hr_efficiency_change_pct > 0 ? '+' : ''}${data.hr_efficiency_change_pct.toFixed(1)}% su 8 settimane`
              : 'Campione confrontabile insufficiente'}</p></div>
          <div><p style={{ color: MUTED }}>Meteo attuale · zona dell’ultima corsa GPS</p>
            {data.weather.status === 'current' ? <p>
              {data.weather.temp_c?.toFixed(1)} °C · punto di rugiada {data.weather.dew_point_c?.toFixed(1)} °C
              {data.weather.observed_at && ` · ${new Date(data.weather.observed_at).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}`}
              {' · '}correzione caldo +{data.heat_adjustment_pct.toFixed(1)}%
            </p> : <p>{data.weather.status === 'disabled' ? 'Disattivato nelle impostazioni' : data.weather.status === 'no_location' ? 'Nessuna posizione GPS recente disponibile' : 'Temporaneamente non disponibile'}</p>}
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {data.items.map(item => <article key={item.distance_m} className={surface}>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-[25px] font-semibold uppercase" style={{ fontFamily: FC }}>{item.label}</h3>
              <span className="text-[10px] uppercase" style={{ color: item.confidence === 'medium' ? SOFT : '#d9b779' }}>{confidence[item.confidence]}</span>
            </div>
            <p className="mt-3 font-mono text-[30px] leading-tight tabular-nums" style={{ color: BLUE }}>{formatPace(item.pace_s_per_km)}</p>
            <p className="mt-1 font-mono text-sm tabular-nums">{item.elapsed_s == null ? 'Tempo non stimabile' : formatDuration(item.elapsed_s)}</p>
            {item.pace_low_s_per_km != null && item.pace_high_s_per_km != null && <p className="mt-3 text-xs" style={{ color: SOFT }}>
              Fascia indicativa<br /><span className="font-mono tabular-nums">{formatPace(item.pace_low_s_per_km)} – {formatPace(item.pace_high_s_per_km)}</span>
            </p>}
            {item.reasons.map(reason => <p key={reason} className="mt-2 text-xs" style={{ color: MUTED }}>{reason}</p>)}
            {item.anchor_activity_id != null && <Link className="mt-3 inline-block text-xs hover:underline" style={{ color: BLUE }} to={`/activities/${item.anchor_activity_id}`}>Corsa di riferimento →</Link>}
          </article>)}
        </div>
        {data.notices.length > 0 && <ul className="space-y-1 text-xs" style={{ color: SOFT }}>{data.notices.map(note => <li key={note}>{note}</li>)}</ul>}
        <details className={surface}>
          <summary className="cursor-pointer text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM, color: MUTED }}>Come leggere le previsioni</summary>
          <div className="mt-3 space-y-2 text-[13px]" style={{ color: SOFT }}>
            <p>Stime per una gara su strada pianeggiante corsa oggi. Le prestazioni dell’ultimo anno hanno un peso che si dimezza ogni 8 settimane; le gare recenti hanno priorità sugli allenamenti.</p>
            <p>La forma viene aggiornata confrontando velocità corretta per pendenza e caldo a FC e durata simili, nelle ultime 8 settimane. Servono almeno 8 corse regolari e 12 confronti: {data.steady_hr_runs_8w} corse utilizzabili. I battiti non vengono estrapolati a una percentuale fissa di FC massima.</p>
            <p>Volume e lungo massimo delle 8 settimane rendono più prudente l’estrapolazione su mezza e maratona. Le fasce sono margini indicativi del modello, non intervalli statistici validati. Un record in allenamento può sottostimare il potenziale di gara.</p>
            <p>Temperatura e punto di rugiada: <a className="underline" href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a>, zona di partenza dell’ultima corsa GPS. Dati aggiornati ogni 15 minuti. Correzione caldo empirica; vento, sole, alimentazione e percorso di gara non sono modellati. Senza meteo, il riferimento è fresco.</p>
          </div>
        </details>
      </>}
    </section>
  )
}
