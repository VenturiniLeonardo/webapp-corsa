import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { type Item, km, Run } from "../components/ActivityCalendarView";
import {
  btn,
  btnGhost,
  FB,
  FM,
  MUTED,
  PageHead,
  Panel,
  pill,
  SOFT,
} from "../components/ui";

// app/api/plans.py: start/end are local wall clock ("YYYY-MM-DDTHH:MM", or a date if all-day)
type PlanEvent = {
  uid: string | null;
  start: string;
  end: string | null;
  summary: string;
  description: string;
};
type Plan = {
  id: number;
  name: string;
  description: string | null;
  updated_at: string;
  events: PlanEvent[];
};

const MON = [
  "gen",
  "feb",
  "mar",
  "apr",
  "mag",
  "giu",
  "lug",
  "ago",
  "set",
  "ott",
  "nov",
  "dic",
];
// date math in UTC so DST never shifts a day
const utc = (day: string) => new Date(`${day}T00:00:00Z`);
const addDays = (day: string, n: number) => {
  const d = utc(day);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const monday = (day: string) => addDays(day, -((utc(day).getUTCDay() + 6) % 7));
const dm = (day: string) =>
  `${Number(day.slice(8, 10))} ${MON[Number(day.slice(5, 7)) - 1]}`;
const last = (p: Plan) => p.events[p.events.length - 1].start.slice(0, 10);
const badge = "rounded-full bg-[#20252c] px-2 text-[11px] text-[#eef1f4]";
const h4 = "text-[11px] tracking-[.07em] uppercase";

// "Key: value" lines -> definition list; Settimana is already in the header
function Description({ text }: { text: string }) {
  const lines = text
    .split("\n")
    .filter((l) => l.trim() && !l.startsWith("Settimana: "));
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
      {lines.map((l, i) => {
        const m = l.match(/^([^:]{1,24}): (.+)$/);
        return m ? (
          <div key={i} className="contents">
            <dt style={{ color: MUTED }}>{m[1]}</dt>
            <dd className="text-[#eef1f4] tabular-nums">{m[2]}</dd>
          </div>
        ) : (
          <dd key={i} className="col-span-2" style={{ color: SOFT }}>
            {l}
          </dd>
        );
      })}
    </dl>
  );
}

function DayDetail({
  day,
  events,
  runs,
  week,
  today,
  onClose,
}: {
  day: string;
  events: PlanEvent[];
  runs: Item[];
  week?: string;
  today: string;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => ref.current?.showModal(), []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && ref.current.close()} // backdrop click
      className="m-auto max-h-[85vh] w-[min(32rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-[#262b33] bg-[#0b0d10] p-0 text-[#eef1f4] backdrop:bg-black/70"
    >
      <section className="space-y-2 p-4">
        <h3 className="flex items-start gap-3">
          <span className="flex flex-1 flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-semibold text-[#eef1f4] capitalize tabular-nums">
              {utc(day).toLocaleDateString("it-IT", {
                weekday: "long",
                day: "numeric",
                month: "long",
                year: "numeric",
                timeZone: "UTC",
              })}
            </span>
            {day === today && <span className={badge}>oggi</span>}
            {week && (
              <span className="text-xs" style={{ color: SOFT }}>
                {week}
              </span>
            )}
          </span>
          <button
            className={btnGhost}
            aria-label="Chiudi"
            onClick={() => ref.current?.close()}
          >
            ✕
          </button>
        </h3>
        <h4 className={h4} style={{ fontFamily: FM, color: MUTED }}>
          Programmato
        </h4>
        {events.length === 0 && (
          <p className="text-sm" style={{ color: MUTED }}>
            Nessuna seduta in programma.
          </p>
        )}
        {events.map((e) => (
          <article
            key={e.uid ?? e.start}
            className="rounded-lg border border-[#262b33] bg-[#111418] px-3 py-2"
          >
            <div className="text-sm">
              {e.start.length > 10 && (
                <span
                  className="mr-2 font-mono text-xs tabular-nums"
                  style={{ color: MUTED }}
                >
                  {e.start.slice(11)}
                  {e.end && e.end.length > 10 && `–${e.end.slice(11)}`}
                </span>
              )}
              <span className="font-semibold text-[#eef1f4]">{e.summary}</span>
              {/^Modifica /m.test(e.description) && (
                <span className={`ml-2 ${badge}`}>modificata</span>
              )}
            </div>
            {e.description && <Description text={e.description} />}
          </article>
        ))}
        <h4 className={`${h4} pt-2`} style={{ fontFamily: FM, color: MUTED }}>
          Svolto
        </h4>
        {runs.length === 0 && (
          <p className="text-sm" style={{ color: MUTED }}>
            {day > today ? "—" : "Nessuna corsa registrata."}
          </p>
        )}
        {runs.map((r) => (
          <Run key={r.id} r={r} named />
        ))}
      </section>
    </dialog>
  );
}

function MonthGrid({
  month,
  byDay,
  runs,
  sel,
  today,
  onPick,
}: {
  month: string;
  byDay: Map<string, PlanEvent[]>;
  runs: Record<string, Item[]>;
  sel: string;
  today: string;
  onPick: (d: string) => void;
}) {
  const first = `${month}-01`;
  const start = monday(first);
  const len = new Date(
    Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0),
  ).getUTCDate();
  const cells =
    Math.ceil(
      ((utc(first).getTime() - utc(start).getTime()) / 864e5 + len) / 7,
    ) * 7;
  return (
    <div className="grid grid-cols-7 gap-1">
      {["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"].map((d) => (
        <div
          key={d}
          className="px-1 text-[11px] tracking-[.07em] uppercase"
          style={{ fontFamily: FM, color: MUTED }}
        >
          {d}
        </div>
      ))}
      {Array.from({ length: cells }, (_, i) => {
        const day = addDays(start, i);
        const evs = byDay.get(day) ?? [];
        const done = runs[day] ?? [];
        return (
          <button
            key={day}
            onClick={() => onPick(day)}
            aria-pressed={day === sel}
            aria-label={`${dm(day)}: ${evs.map((e) => e.summary).join(", ") || "nessuna seduta"}${done.length ? `; svolto ${done.map((r) => km(r.distance_m)).join(", ")}` : ""}`}
            className={`flex min-h-14 min-w-0 flex-col items-start rounded-md border p-1 text-left md:min-h-20 ${
              day === sel
                ? "border-[#4c8dff]"
                : "border-[#262b33] hover:border-[#4a515c]"
            } ${evs.length || done.length ? "bg-[#111418]" : ""} ${day.startsWith(month) ? "" : "opacity-40"}`}
          >
            <span
              className={`font-mono text-[11px] tabular-nums ${day === today ? "rounded-full bg-[#eef1f4] px-1.5 font-bold text-[#111418]" : ""}`}
              style={day === today ? undefined : { color: MUTED }}
            >
              {Number(day.slice(8))}
            </span>
            {evs.map((e) => (
              <span
                key={e.uid ?? e.start}
                className="mt-0.5 hidden w-full truncate text-[11px] leading-tight text-[#eef1f4] md:block"
              >
                {e.summary}
              </span>
            ))}
            {done.map((r) => (
              <span
                key={r.id}
                className="mt-0.5 hidden w-full truncate font-mono text-[11px] leading-tight text-green-400 tabular-nums md:block"
              >
                ✓ {km(r.distance_m)}
              </span>
            ))}
            <span className="mt-1 flex gap-1 md:hidden">
              {evs.length > 0 && (
                <span className="h-1.5 w-1.5 rounded-full bg-[#4c8dff]" />
              )}
              {done.length > 0 && (
                <span className="h-1.5 w-1.5 rounded-full bg-green-400" />
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function PlanMonth({
  plan,
  byDay,
  today,
}: {
  plan: Plan;
  byDay: Map<string, PlanEvent[]>;
  today: string;
}) {
  const [pick, setPick] = useState<string | null>(null); // selected day; month follows it
  const [open, setOpen] = useState(false);
  // default day: today, clamped into the plan's range
  const firstDay = plan.events[0].start.slice(0, 10);
  const day =
    pick ??
    (today > last(plan) ? last(plan) : today < firstDay ? firstDay : today);
  const month = day.slice(0, 7);
  const q = useQuery({
    queryKey: ["calendar", month],
    queryFn: () =>
      api<{ days: Record<string, Item[]> }>(
        `/api/stats/calendar-month?year=${month.slice(0, 4)}&month=${Number(month.slice(5))}`,
      ),
    placeholderData: keepPreviousData,
  });
  const runs = q.data?.days ?? {};
  const shift = (n: number) => {
    const d = utc(`${month}-01`);
    d.setUTCMonth(d.getUTCMonth() + n);
    setPick(d.toISOString().slice(0, 10));
  };
  const week = plan.events
    .find((e) => monday(e.start.slice(0, 10)) === monday(day))
    ?.description.match(/^Settimana: (.+)$/m)?.[1];

  return (
    <>
      <div className="mt-5 mb-3 flex items-center gap-3">
        <button
          className={btnGhost}
          aria-label="Mese precedente"
          onClick={() => shift(-1)}
        >
          ←
        </button>
        <span className="min-w-36 text-center font-semibold text-[#eef1f4] capitalize">
          {utc(`${month}-01`).toLocaleDateString("it-IT", {
            month: "long",
            year: "numeric",
            timeZone: "UTC",
          })}
        </span>
        <button
          className={btnGhost}
          aria-label="Mese successivo"
          onClick={() => shift(1)}
        >
          →
        </button>
      </div>
      <MonthGrid
        month={month}
        byDay={byDay}
        runs={runs}
        sel={day}
        today={today}
        onPick={(d) => {
          setPick(d);
          setOpen(true);
        }}
      />
      {open && (
        <DayDetail
          day={day}
          events={byDay.get(day) ?? []}
          runs={runs[day] ?? []}
          week={week}
          today={today}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

export default function CalendarPage() {
  const { data, isError } = useQuery({
    queryKey: ["/api/plans"],
    queryFn: () => api<Plan[]>("/api/plans"),
  });
  const [sel, setSel] = useState<number | null>(null);
  const [today] = useState(() => new Date().toLocaleDateString("sv"));
  if (isError)
    return <p className="text-sm text-red-400">Failed to load plans.</p>;
  if (!data) return <p className="text-sm text-neutral-500">Loading…</p>;
  // default: the plan in progress or next; else the latest
  const plan =
    data.find((p) => p.id === sel) ??
    data.find((p) => last(p) >= today) ??
    data[data.length - 1];

  const byDay = new Map<string, PlanEvent[]>();
  for (const e of plan?.events ?? []) {
    const k = e.start.slice(0, 10);
    byDay.set(k, [...(byDay.get(k) ?? []), e]);
  }
  return (
    <div className="space-y-4" style={{ fontFamily: FB }}>
      <PageHead eyebrow="Piani di allenamento" title="Calendario" />
      {!plan && (
        <p className="text-sm" style={{ color: MUTED }}>
          Nessun piano caricato.
        </p>
      )}
      {data.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {data.map((p) => (
            <button
              key={p.id}
              className={pill(p.id === plan?.id)}
              aria-pressed={p.id === plan?.id}
              onClick={() => setSel(p.id)}
            >
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
              {dm(plan.events[0].start.slice(0, 10))}{" "}
              {plan.events[0].start.slice(0, 4)} – {dm(last(plan))}{" "}
              {last(plan).slice(0, 4)} · {plan.events.length} sedute
            </span>
          }
        >
          {plan.description && (
            <p className="text-[13px]" style={{ color: SOFT }}>
              {plan.description}
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <a
              className={btn}
              href={`webcal://${location.host}/api/plans/${plan.id}.ics`}
            >
              Aggiungi ad Apple Calendar
            </a>
            <a
              className={btnGhost}
              href={`/api/plans/${plan.id}.ics`}
              download={`${plan.name}.ics`}
            >
              Scarica .ics
            </a>
          </div>
          <p className="mt-2 text-xs" style={{ color: MUTED }}>
            Aggiungi = iscrizione: il calendario si aggiorna da solo quando il
            piano cambia (sul Mac scegli la posizione «Sul Mac»: iCloud non
            raggiunge la webapp). Scarica = file da importare a mano. Se avevi
            già importato il piano originale, eliminalo per non avere doppioni.
          </p>
          <PlanMonth key={plan.id} plan={plan} byDay={byDay} today={today} />
        </Panel>
      )}
    </div>
  );
}
