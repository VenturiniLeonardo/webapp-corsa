import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { btn, btnGhost, FB, FM, MUTED, PageHead, Panel, pill, SOFT } from '../components/ui'

// app/api/plans.py: start/end are local wall clock ("YYYY-MM-DDTHH:MM", or a date if all-day)
type PlanEvent = { uid: string | null; start: string; end: string | null; summary: string; description: string }
type Plan = { id: number; name: string; description: string | null; updated_at: string; events: PlanEvent[] }

const DOW = ['Dom', 'Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab']
const MON = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic']
// date math in UTC so DST never shifts a day
const utc = (day: string) => new Date(`${day}T00:00:00Z`)
const addDays = (day: string, n: number) => {
  const d = utc(day)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}
const monday = (day: string) => addDays(day, -((utc(day).getUTCDay() + 6) % 7))
const dm = (day: string) => `${Number(day.slice(8, 10))} ${MON[Number(day.slice(5, 7)) - 1]}`
const badge = 'rounded-full bg-[#20252c] px-2 text-[11px] text-[#eef1f4]'

function Session({ e, today }: { e: PlanEvent; today: string }) {
  const day = e.start.slice(0, 10)
  return (
    <details className={`rounded-lg border border-[#262b33] bg-[#111418] ${day < today ? 'opacity-60' : ''}`}>
      <summary className="cursor-pointer px-3 py-2 text-sm">
        <span className="font-mono text-xs tabular-nums" style={{ color: MUTED }}>
          {DOW[utc(day).getUTCDay()]} {Number(day.slice(8, 10))}/{Number(day.slice(5, 7))}
          {e.start.length > 10 && ` · ${e.start.slice(11)}`}
        </span>{' '}
        <span className="font-semibold text-[#eef1f4]">{e.summary}</span>
        {day === today && <span className={`ml-2 ${badge}`}>oggi</span>}
        {/^Modifica /m.test(e.description) && <span className={`ml-2 ${badge}`}>modificata</span>}
      </summary>
      {e.description && (
        <p className="px-3 pb-3 text-[13px] whitespace-pre-line" style={{ color: SOFT }}>
          {e.description}
        </p>
      )}
    </details>
  )
}

function Week({ mon, events, today }: { mon: string; events: PlanEvent[]; today: string }) {
  const label = events[0].description.match(/^Settimana: (.+)$/m)?.[1]
  return (
    <section className="space-y-1.5">
      <h3 className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs" style={{ color: MUTED }}>
        <span className="tracking-[.07em] uppercase tabular-nums" style={{ fontFamily: FM }}>
          {dm(mon)} – {dm(addDays(mon, 6))}
        </span>
        {label && <span style={{ color: SOFT }}>{label}</span>}
        {mon <= today && today <= addDays(mon, 6) && <span className={badge}>in corso</span>}
      </h3>
      {events.map((e) => (
        <Session key={e.uid ?? e.start} e={e} today={today} />
      ))}
    </section>
  )
}

export default function CalendarPage() {
  const { data, isError } = useQuery({ queryKey: ['/api/plans'], queryFn: () => api<Plan[]>('/api/plans') })
  const [sel, setSel] = useState<number | null>(null)
  const [today] = useState(() => new Date().toLocaleDateString('sv'))
  if (isError) return <p className="text-sm text-red-400">Failed to load plans.</p>
  if (!data) return <p className="text-sm text-neutral-500">Loading…</p>
  const last = (p: Plan) => p.events[p.events.length - 1].start.slice(0, 10)
  // default: the plan in progress or next; else the latest
  const plan = data.find((p) => p.id === sel) ?? data.find((p) => last(p) >= today) ?? data[data.length - 1]

  const weeks = new Map<string, PlanEvent[]>()
  for (const e of plan?.events ?? []) {
    const k = monday(e.start.slice(0, 10))
    weeks.set(k, [...(weeks.get(k) ?? []), e])
  }
  const done = [...weeks].filter(([mon]) => addDays(mon, 6) < today)
  const ahead = [...weeks].filter(([mon]) => addDays(mon, 6) >= today)

  return (
    <div className="space-y-4" style={{ fontFamily: FB }}>
      <PageHead eyebrow="Piani di allenamento" title="Calendario" />
      {!plan && <p className="text-sm" style={{ color: MUTED }}>Nessun piano caricato.</p>}
      {data.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {data.map((p) => (
            <button key={p.id} className={pill(p.id === plan?.id)} aria-pressed={p.id === plan?.id} onClick={() => setSel(p.id)}>
              {p.name}
            </button>
          ))}
        </div>
      )}
      {plan && (
        <Panel
          title={plan.name}
          sub={
            <span className="tabular-nums">
              {dm(plan.events[0].start.slice(0, 10))} {plan.events[0].start.slice(0, 4)} – {dm(last(plan))} {last(plan).slice(0, 4)} · {plan.events.length} sedute
            </span>
          }
        >
          {plan.description && (
            <p className="text-[13px]" style={{ color: SOFT }}>
              {plan.description}
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <a className={btn} href={`webcal://${location.host}/api/plans/${plan.id}.ics`}>
              Aggiungi ad Apple Calendar
            </a>
            <a className={btnGhost} href={`/api/plans/${plan.id}.ics`} download={`${plan.name}.ics`}>
              Scarica .ics
            </a>
          </div>
          <p className="mt-2 text-xs" style={{ color: MUTED }}>
            Aggiungi = iscrizione: il calendario si aggiorna da solo quando il piano cambia (sul Mac scegli la posizione «Sul Mac»: iCloud non raggiunge la
            webapp). Scarica = file da importare a mano. Se avevi già importato il piano originale, eliminalo per non avere doppioni.
          </p>
          <div className="mt-5 space-y-5">
            {done.length > 0 && (
              <details>
                <summary className="cursor-pointer text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM, color: MUTED }}>
                  {done.length} settimane concluse
                </summary>
                <div className="mt-3 space-y-5">
                  {done.map(([mon, evs]) => (
                    <Week key={mon} mon={mon} events={evs} today={today} />
                  ))}
                </div>
              </details>
            )}
            {ahead.map(([mon, evs]) => (
              <Week key={mon} mon={mon} events={evs} today={today} />
            ))}
          </div>
        </Panel>
      )}
    </div>
  )
}
