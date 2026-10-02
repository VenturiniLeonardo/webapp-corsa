import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ApiError } from "../api/client";
import {
  btn as btnPrimary,
  FB,
  PageHead,
  Panel,
} from "../components/ui";

type Job = {
  id: number;
  kind: string;
  status: string;
  progress_done: number | null;
  progress_total: number | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
};
type ImportResult = {
  mapped: number;
  skipped: number;
  duplicate: number;
  failed: { id: string; error: string }[];
};

function ImportPanel({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setLog([]);
    for (const f of Array.from(files)) {
      setLog((l) => [...l, `${f.name}: caricamento…`]);
      let line: string;
      try {
        const r = await api<ImportResult>("/api/imports/file", {
          method: "POST",
          body: f,
          headers: {
            "Content-Type": "application/octet-stream",
            "X-Filename": encodeURIComponent(f.name),
          },
        });
        line =
          `${f.name}: ${r.mapped} importate, ${r.duplicate} già presenti, ${r.skipped} non corsa` +
          (r.failed.length
            ? `, ${r.failed.length} errori (${r.failed[0].id}: ${r.failed[0].error})`
            : "");
      } catch (e) {
        line = `${f.name}: ${(e as ApiError).detail ?? (e as Error).message}`;
      }
      setLog((l) => [...l.slice(0, -1), line]);
    }
    setBusy(false);
    onDone();
  };
  return (
    <Panel
      title="Importa file"
      sub="Strava (export .zip o file .fit/.gpx/.tcx) · Health Auto Export (.json/.zip)"
    >
      <div className="space-y-3">
        <label
          className={`${btnPrimary} cursor-pointer ${busy ? "pointer-events-none opacity-50" : ""}`}
        >
          {busy ? "Importazione…" : "Scegli file"}
          <input
            type="file"
            multiple
            hidden
            accept=".zip,.json,.fit,.gpx,.tcx,.gz"
            disabled={busy}
            onChange={(e) => {
              upload(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
        {log.map((l, i) => (
          <p
            key={i}
            role="status"
            className={`${mono} break-words text-xs text-neutral-400`}
          >
            {l}
          </p>
        ))}
      </div>
    </Panel>
  );
}

const mono = "font-mono tabular-nums";
const th =
  "px-2 py-1.5 text-left text-[11px] font-normal tracking-[.07em] uppercase text-[#8a93a0]";
const td = "px-2 py-2 align-top";
const ts = (v: string | null | undefined) =>
  v ? new Date(v).toLocaleString("sv") : "—";

export default function SyncPage() {
  const qc = useQueryClient();
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/api/jobs?limit=20"),
    refetchInterval: (q) =>
      q.state.data?.some((j) => j.status === "queued" || j.status === "running")
        ? 3000
        : false,
  });
  const intervals = useQuery({
    queryKey: ["intervals-status"],
    queryFn: () => api<{ enabled: boolean }>("/api/intervals/status"),
  });
  const pull = useMutation({
    mutationFn: () => api("/api/intervals/sync", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  return (
    <div className="space-y-4" style={{ fontFamily: FB }}>
      <PageHead eyebrow="Import da file" title="Import" />
      <Panel
        title="intervals.icu"
        sub="Sync automatico ogni 30 min (corse non provenienti da Strava)"
      >
        <div className="space-y-3">
          {intervals.data && !intervals.data.enabled && (
            <p className="text-sm text-neutral-500">
              Imposta INTERVALS_API_KEY nel .env per abilitarlo.
            </p>
          )}
          <button
            className={btnPrimary}
            disabled={!intervals.data?.enabled || pull.isPending}
            onClick={() => pull.mutate()}
          >
            Sync ora
          </button>
          {pull.isError && (
            <p className="text-xs text-red-400">
              {String((pull.error as ApiError).detail ?? pull.error.message)}
            </p>
          )}
        </div>
      </Panel>

      <ImportPanel onDone={() => qc.invalidateQueries()} />
      <Panel title="Recent jobs">
        {jobs.isError && (
          <p className="text-sm text-red-400">Failed to load jobs.</p>
        )}
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
                  <td
                    className={`${td} ${j.status === "failed" ? "text-red-400" : ""}`}
                  >
                    {j.status}
                  </td>
                  <td className={td}>
                    {j.progress_total
                      ? `${j.progress_done ?? 0}/${j.progress_total}`
                      : "—"}
                  </td>
                  <td className={td}>{ts(j.created_at)}</td>
                  <td className={td}>{ts(j.finished_at)}</td>
                  <td className={`${td} max-w-xs break-words text-red-400`}>
                    {j.error ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
