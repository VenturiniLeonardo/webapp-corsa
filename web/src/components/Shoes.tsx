import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { btn, field, MUTED, Panel } from './ui'
import { formatPace } from '../utils/formatters'

// app/api/shoes.py
export type Shoe = {
  id: number
  name: string
  target_distance_m: number
  initial_distance_m: number
  is_default: boolean
  retired_at: string | null
  total_distance_m: number
  run_count: number
  weighted_pace_s_per_km: number | null
  percent_worn: number
}

export const useShoes = () => useQuery({ queryKey: ['shoes'], queryFn: () => api<Shoe[]>('/api/shoes') })

const km = (m: number) => (m / 1000).toFixed(0)
const wear = (p: number) => (p > 90 ? '#ef4444' : p >= 70 ? '#f59e0b' : '#22c55e')
const today = () => new Date().toLocaleDateString('sv') // YYYY-MM-DD, local

export function ShoesPanel() {
  const qc = useQueryClient()
  const shoes = useShoes()
  const [err, setErr] = useState('')
  const [name, setName] = useState('')
  const [target, setTarget] = useState('700')
  const [initial, setInitial] = useState('0')
  const done = { onSuccess: () => (setErr(''), qc.invalidateQueries({ queryKey: ['shoes'] })), onError: (e: Error) => setErr(e.message) }
  const patch = useMutation({
    mutationFn: ([id, body]: [number, Record<string, unknown>]) => api(`/api/shoes/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    ...done,
  })
  const del = useMutation({ mutationFn: (id: number) => api(`/api/shoes/${id}`, { method: 'DELETE' }), ...done })
  const add = useMutation({
    mutationFn: () =>
      api('/api/shoes', {
        method: 'POST',
        body: JSON.stringify({ name, target_distance_m: Number(target) * 1000, initial_distance_m: Number(initial) * 1000, is_default: !shoes.data?.some((s) => s.is_default && !s.retired_at) }),
      }),
    ...done,
    onSuccess: () => (done.onSuccess(), setName(''), setInitial('0')),
  })
  const action = 'text-xs text-[#4c8dff] hover:underline'

  return (
    <Panel title="Scarpe" sub="La scarpa predefinita viene assegnata a ogni nuova corsa importata." className="space-y-3 text-sm">
      {shoes.isError && <p className="text-red-400">Failed to load shoes.</p>}
      {shoes.data?.map((s) => (
        <div key={s.id} className={`rounded-lg border border-[#262b33] p-3 ${s.retired_at ? 'opacity-60' : ''}`}>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="font-semibold text-[#eef1f4]">
              {s.name}
              {s.is_default && <span className="ml-2 rounded-full bg-[#20252c] px-2 text-xs font-normal text-neutral-300">predefinita</span>}
              {s.retired_at && <span className="ml-2 text-xs font-normal" style={{ color: MUTED }}>ritirata {s.retired_at}</span>}
            </span>
            <span className="font-mono tabular-nums text-neutral-300">
              {km(s.total_distance_m)} / {km(s.target_distance_m)} km
            </span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-[#111418]" role="meter" aria-valuenow={Math.round(s.percent_worn)} aria-valuemin={0} aria-valuemax={100} aria-label="Usura">
            <div className="h-full" style={{ width: `${Math.min(100, s.percent_worn)}%`, background: wear(s.percent_worn) }} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs tabular-nums" style={{ color: MUTED }}>
            <span>{Math.round(s.percent_worn)}%</span>
            <span>{s.run_count} uscite</span>
            <span>{formatPace(s.weighted_pace_s_per_km)}</span>
            {s.percent_worn > 90 && !s.retired_at && <span className="font-sans text-red-400">Consigliata sostituzione</span>}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            {!s.is_default && !s.retired_at && (
              <button className={action} onClick={() => patch.mutate([s.id, { is_default: true }])}>
                Imposta predefinita
              </button>
            )}
            <label className="flex items-center gap-1 text-xs" style={{ color: MUTED }}>
              target km
              <input
                key={s.target_distance_m}
                type="number"
                min={1}
                defaultValue={km(s.target_distance_m)}
                className={`${field} w-20 font-mono tabular-nums`}
                onBlur={(e) => Number(e.target.value) > 0 && Number(e.target.value) * 1000 !== s.target_distance_m && patch.mutate([s.id, { target_distance_m: Number(e.target.value) * 1000 }])}
              />
            </label>
            <button className={action} onClick={() => patch.mutate([s.id, { retired_at: s.retired_at ? null : today() }])}>
              {s.retired_at ? 'Riattiva' : 'Ritira'}
            </button>
            {s.run_count === 0 && (
              <button className="text-xs text-red-400 hover:underline" onClick={() => del.mutate(s.id)}>
                Elimina
              </button>
            )}
          </div>
        </div>
      ))}
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) add.mutate()
        }}
      >
        <label className="space-y-1">
          <span className="block text-neutral-400">Nuovo paio</span>
          <input placeholder="es. Saucony Triumph 21" className={`${field} w-52`} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="space-y-1">
          <span className="block text-neutral-400">Target km</span>
          <input type="number" min={1} className={`${field} w-20 font-mono tabular-nums`} value={target} onChange={(e) => setTarget(e.target.value)} />
        </label>
        <label className="space-y-1">
          <span className="block text-neutral-400">Km già percorsi</span>
          <input type="number" min={0} className={`${field} w-20 font-mono tabular-nums`} value={initial} onChange={(e) => setInitial(e.target.value)} />
        </label>
        <button type="submit" disabled={!name.trim() || !(Number(target) > 0) || add.isPending} className={btn}>
          Aggiungi
        </button>
      </form>
      {err && <p className="text-xs text-red-400">{err}</p>}
    </Panel>
  )
}
