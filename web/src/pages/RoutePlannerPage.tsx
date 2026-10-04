import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as echarts from 'echarts'
import type { FeatureCollection } from 'geojson'
import { ArrowDown, ArrowUp, X } from 'lucide-react'
import {
  type GeoJSONSource,
  LngLatBounds,
  Map as MlMap,
  type MapLayerMouseEvent,
  type MapLayerTouchEvent,
  type MapMouseEvent,
  type MapTouchEvent,
  Marker,
  setWorkerUrl,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import mlWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { btn, btnGhost, field, FM, MUTED, PageHead, Panel, pill } from '../components/ui'
import { formatDuration, formatPace } from '../utils/formatters'

setWorkerUrl(mlWorkerUrl)

type Pt = (number | null)[] // [lng, lat, ele?]
// app/metrics/routes.py analyze + app/api/routes.py
type Analysis = {
  distance_m: number
  elev_gain_m: number
  elev_loss_m: number
  ele_min_m: number
  ele_max_m: number
  climb_grade_avg: number | null
  grade_max: number | null
  grade_bands_m: Record<'flat' | 'gentle' | 'steep' | 'down', number>
  turns: number
  turns_sharp: number
  hairpins: number
  sinuosity: number | null
  gap_distance_m: number
  profile: {
    d: number[]
    ele: number[]
    grade: (number | null)[]
    lng: number[]
    lat: number[]
  }
}
type SurfaceData = {
  surface_m: Record<keyof typeof SURF, number>
  sectors: { k: keyof typeof SURF; coords: number[][] }[]
}
type RouteRow = {
  id: number
  name: string
  notes: string | null
  distance_m: number
  elev_gain_m: number
  elev_loss_m: number
  target_speed_ms: number | null
  surface: SurfaceData | null
  waypoints: number[][] | null
  updated_at: string
}
type RouteFull = RouteRow & { coords: Pt[] }

const ELEV = '#6b7280'
const BANDS: [keyof Analysis['grade_bands_m'], string, string][] = [
  ['flat', 'Piano', '#4a515c'],
  ['gentle', 'Salita 2–5%', '#f59e0b'],
  ['steep', 'Salita >5%', '#ef4444'],
  ['down', 'Discesa', '#4c8dff'],
]
const SURF = {
  asphalt: ['Asfalto', '#8a93a0'],
  stone: ['Pietra / sanpietrini', '#a78bfa'],
  unpaved: ['Sterrato', '#b45309'],
  unknown: ['Non mappato', '#5b6572'],
} as const
const mono = 'font-mono tabular-nums'
const km = (m: number) => `${(m / 1000).toFixed(2)} km`
const pct = (g: number | null) => (g == null ? '—' : `${(g * 100).toFixed(1)}%`)
const post = (body: unknown) => ({
  method: 'POST',
  body: JSON.stringify(body),
})
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }
const wpsFc = (wps: Pt[]): FeatureCollection => ({
  type: 'FeatureCollection',
  features: wps.map((p, i) => ({
    type: 'Feature',
    properties: { i, k: i ? 'wp' : 'start', n: String(i + 1) },
    geometry: { type: 'Point', coordinates: [p[0]!, p[1]!] },
  })),
})
const pairKey = (a: Pt, b: Pt) => `${a[0]},${a[1]};${b[0]},${b[1]}`
const hav = (a: Pt, b: Pt) => {
  const r = Math.PI / 180
  const x = Math.sin(((b[1]! - a[1]!) * r) / 2) ** 2 + Math.cos(a[1]! * r) * Math.cos(b[1]! * r) * Math.sin(((b[0]! - a[0]!) * r) / 2) ** 2
  return 12742000 * Math.asin(Math.sqrt(x))
}

export default function RoutePlannerPage() {
  const qc = useQueryClient()
  const [wps, setWps] = useState<Pt[]>([])
  const [segs, setSegs] = useState<Pt[][]>([]) // segs[i]: wps[i] → wps[i+1]
  const cache = useRef(new Map<string, Pt[]>()) // per waypoint pair, so reorder/remove only routes new pairs
  const [snapMode, setSnapMode] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [editId, setEditId] = useState<number | null>(null)
  const [saved, setSaved] = useState(false)
  const [name, setName] = useState('')
  const [pace, setPace] = useState<number | null>(null) // s/km, UI only (stored as speed)

  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: () => api<{ ref_pace_s_per_km: number | null }>('/api/settings'),
  })
  const routes = useQuery({
    queryKey: ['routes'],
    queryFn: () => api<RouteRow[]>('/api/routes'),
  })
  const recent = useQuery({
    queryKey: ['activities', 'route-copy'],
    queryFn: () =>
      api<{
        items: {
          id: number
          name: string | null
          start_time_utc: string
          distance_m: number | null
        }[]
      }>('/api/activities?page_size=50'),
  })
  const flatPace = pace ?? settings.data?.ref_pace_s_per_km ?? 330

  const flat = useMemo(() => (wps.length ? [wps[0], ...segs.flatMap((s) => s.slice(1))] : []), [wps, segs])
  const legKm = useMemo(() => segs.map((s) => s.reduce((t, p, i) => (i ? t + hav(s[i - 1], p) : 0), 0) / 1000), [segs])
  const an = useQuery({
    queryKey: ['route-analyze', flat],
    queryFn: () => api<Analysis>('/api/routes/analyze', post({ coords: flat })),
    enabled: flat.length >= 2,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    retry: false,
  })
  const a = flat.length >= 2 ? an.data : undefined
  const surf = useQuery({
    queryKey: ['route-surface', flat],
    queryFn: () => api<SurfaceData>('/api/routes/surface', post({ coords: flat })),
    enabled: flat.length >= 2,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    retry: false,
  })
  const sm = flat.length >= 2 ? surf.data?.surface_m : undefined
  const surfFresh = flat.length >= 2 && surf.data && !surf.isPlaceholderData && !surf.isFetching ? surf.data : undefined // saved with the route
  const smTot = sm ? Object.values(sm).reduce((t, v) => t + v, 0) : 0

  const remember = (a: Pt, b: Pt, s: Pt[]) => (cache.current.set(pairKey(a, b), s), cache.current.set(pairKey(b, a), [...s].reverse()))
  /** Set the waypoint list; segments come from the cache or are routed (foot profile) now. */
  const route = async (next: Pt[]) => {
    if (busy) return
    const out: Pt[][] = []
    let failed = false
    setBusy(true)
    try {
      for (let i = 1; i < next.length; i++) {
        const [a, b] = [next[i - 1], next[i]]
        let s = cache.current.get(pairKey(a, b))
        if (!s) {
          s = [a, b]
          if (!snapMode) remember(a, b, s)
          else
            try {
              s = (await api<{ coords: Pt[] }>('/api/routes/snap', post({ coords: [a.slice(0, 2), b.slice(0, 2)] }))).coords
              remember(a, b, s)
            } catch {
              failed = true // straight line, not cached: retried on the next edit
            }
        }
        out.push(s)
      }
    } finally {
      setBusy(false)
    }
    setWps(next)
    setSegs(out)
    setSaved(false)
    setErr(failed ? 'Routing non disponibile: alcuni tratti sono in linea retta.' : '')
  }
  const addPoint = (p: Pt) => void route([...wps, p])
  const move = (i: number, j: number) => {
    const n = [...wps]
    ;[n[i], n[j]] = [n[j], n[i]]
    void route(n)
  }
  /** Out-and-back: mirror waypoints and segments, no routing needed. */
  const retrace = () => {
    setWps([...wps, ...wps.slice(0, -1).reverse()])
    setSegs([...segs, ...[...segs].reverse().map((s) => [...s].reverse())])
    setSaved(false)
  }
  const live = useRef({ wps, busy, route, addPoint }) // latest state for map handlers bound once
  useEffect(() => {
    live.current = { wps, busy, route, addPoint }
  })

  // --- map ---
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<MlMap | null>(null)
  const marker = useRef<Marker | null>(null)
  const [ready, setReady] = useState(false)
  useEffect(() => {
    const m = new MlMap({
      container: el.current!,
      style: 'https://tiles.openfreemap.org/styles/dark',
      center: [9.19, 45.46],
      zoom: 12,
      attributionControl: { compact: true },
    })
    map.current = m
    marker.current = new Marker({
      element: Object.assign(document.createElement('div'), {
        className: 'size-3 rounded-full border-2 border-neutral-100 bg-[#4c8dff]',
      }),
    })
    m.getCanvas().style.cursor = 'crosshair'
    m.on('click', (e) => {
      if (!m.queryRenderedFeatures(e.point, { layers: ['wps', 'line-hit'] }).length) live.current.addPoint([e.lngLat.lng, e.lngLat.lat])
    })
    // drag a waypoint to move it; press on the line to insert one there (drag or just tap)
    const drag = (e: MapLayerMouseEvent | MapLayerTouchEvent, insert: boolean) => {
      const f = e.features?.[0]
      if (!f || live.current.busy || ('points' in e && e.points.length > 1)) return
      e.preventDefault() // no map pan while dragging
      const at = (ev: MapMouseEvent | MapTouchEvent): Pt => [ev.lngLat.lng, ev.lngLat.lat]
      const w = live.current.wps
      const i = insert ? Number(f.properties.s) + 1 : Number(f.properties.i)
      const pts = insert ? [...w.slice(0, i), at(e), ...w.slice(i)] : [...w]
      let moved = false
      const ev = e.type === 'touchstart' ? (['touchmove', 'touchend'] as const) : (['mousemove', 'mouseup'] as const)
      const onMove = (me: MapMouseEvent | MapTouchEvent) => {
        moved = true
        pts[i] = at(me)
        ;(m.getSource('wps') as GeoJSONSource).setData(wpsFc(pts))
        const near = [pts[i - 1], pts[i], pts[i + 1]].filter(Boolean).map((p) => [p[0]!, p[1]!])
        ;(m.getSource('ghost') as GeoJSONSource).setData({
          type: 'Feature',
          properties: {},
          geometry: { type: 'LineString', coordinates: near },
        })
      }
      m.on(ev[0], onMove)
      m.once(ev[1], () => {
        m.off(ev[0], onMove)
        ;(m.getSource('ghost') as GeoJSONSource).setData(EMPTY)
        if (moved || insert) void live.current.route(pts)
      })
    }
    for (const [layer, insert] of [
      ['wps', false],
      ['line-hit', true],
    ] as const) {
      m.on('mousedown', layer, (e) => drag(e, insert))
      m.on('touchstart', layer, (e) => drag(e, insert))
      m.on('mouseenter', layer, () => (m.getCanvas().style.cursor = insert ? 'copy' : 'grab'))
      m.on('mouseleave', layer, () => (m.getCanvas().style.cursor = 'crosshair'))
    }
    m.on('load', () => {
      m.addSource('line', { type: 'geojson', data: EMPTY })
      m.addLayer({
        id: 'line',
        type: 'line',
        source: 'line',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-width': 4, 'line-color': '#4c8dff' },
      })
      m.addSource('surf', { type: 'geojson', data: EMPTY })
      m.addLayer({
        id: 'surf',
        type: 'line',
        source: 'surf',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-width': 4,
          'line-color': ['match', ['get', 'k'], ...Object.entries(SURF).flatMap(([k, v]) => [k, v[1]]), '#4c8dff'] as unknown as string,
        },
      })
      m.addLayer({
        id: 'line-hit',
        type: 'line',
        source: 'line',
        paint: { 'line-width': 16, 'line-opacity': 0 },
      }) // wider grab target
      m.addSource('ghost', { type: 'geojson', data: EMPTY })
      m.addLayer({
        id: 'ghost',
        type: 'line',
        source: 'ghost',
        paint: {
          'line-width': 2,
          'line-color': '#eef1f4',
          'line-dasharray': [2, 2],
        },
      })
      m.addSource('wps', { type: 'geojson', data: EMPTY })
      m.addLayer({
        id: 'wps',
        type: 'circle',
        source: 'wps',
        paint: {
          'circle-radius': 9,
          'circle-color': ['match', ['get', 'k'], 'start', '#22c55e', '#111418'],
          'circle-stroke-color': '#eef1f4',
          'circle-stroke-width': 2,
        },
      })
      m.addLayer({
        id: 'wps-n',
        type: 'symbol',
        source: 'wps',
        layout: {
          'text-field': ['get', 'n'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'text-allow-overlap': true,
        },
        paint: {
          'text-color': ['match', ['get', 'k'], 'start', '#111418', '#eef1f4'],
        },
      })
      setReady(true)
    })
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(el.current!)
    return () => {
      ro.disconnect()
      m.remove()
      map.current = null
    }
  }, [])
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    ;(m.getSource('line') as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: segs.map((s, i) => ({
        type: 'Feature',
        properties: { s: i },
        geometry: {
          type: 'LineString',
          coordinates: s.map((p) => [p[0]!, p[1]!]),
        },
      })),
    })
    ;(m.getSource('wps') as GeoJSONSource).setData(wpsFc(wps))
  }, [ready, segs, wps])
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    ;(m.getSource('surf') as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: (flat.length >= 2 ? (sm ? surf.data!.sectors : []) : []).map((s) => ({
        type: 'Feature',
        properties: { k: s.k },
        geometry: { type: 'LineString', coordinates: s.coords },
      })),
    })
  }, [ready, surf.data, sm, flat])

  const clear = () => (setWps([]), setSegs([]), setSaved(false), setErr(''))
  const load = (
    coords: Pt[],
    n: string | null,
    id: number | null = null,
    speed: number | null = null,
    surface: SurfaceData | null = null,
    waypoints: number[][] | null = null,
  ) => {
    // saved waypoints: split the stored line at the point nearest each one (searching forward, so out-and-back routes work)
    let w: Pt[] = [coords[0], coords[coords.length - 1]]
    let sg: Pt[][] = [coords]
    if (waypoints && waypoints.length >= 2) {
      const idx = [0]
      for (let i = 1; i < waypoints.length; i++) {
        let j = coords.length - 1
        if (i < waypoints.length - 1) {
          let best = Infinity
          for (let k = Math.min(idx[i - 1] + 1, coords.length - 1); k < coords.length; k++) {
            const d = hav(coords[k], waypoints[i])
            if (d < best) ((best = d), (j = k))
          }
        }
        idx.push(j)
      }
      w = [coords[0], ...(waypoints.slice(1) as Pt[])]
      sg = idx.slice(1).map((e, i) => coords.slice(idx[i], e + 1))
      sg.forEach((x, i) => remember(w[i], w[i + 1], x))
    } else remember(w[0], w[1], coords)
    // seed under the exact key the planner will query with (flat), so no Overpass call
    if (surface) qc.setQueryData(['route-surface', [w[0], ...sg.flatMap((x) => x.slice(1))]], surface)
    setWps(w)
    setSegs(sg)
    setErr('')
    setName(n ?? '')
    setEditId(id)
    setSaved(id != null)
    setPace(speed ? 1000 / speed : null)
    const b = new LngLatBounds()
    for (const p of coords) b.extend([p[0]!, p[1]!])
    map.current?.fitBounds(b, { padding: 32, duration: 0 })
  }

  // --- actions ---
  const fail = (e: Error) => setErr(e.message)
  const importFile = useMutation({
    mutationFn: (f: File) =>
      api<{ name: string | null; coords: Pt[] }>('/api/routes/import-file', {
        method: 'POST',
        body: f,
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Filename': encodeURIComponent(f.name),
        },
      }),
    onSuccess: (r) => load(r.coords, r.name),
    onError: fail,
  })
  const copyRun = useMutation({
    mutationFn: async (id: number) => {
      const st = (
        await api<{
          channels: {
            lat?: (number | null)[]
            lng?: (number | null)[]
            altitude?: (number | null)[]
          }
        }>(`/api/activities/${id}/streams?channels=lat,lng,altitude`)
      ).channels
      const coords = (st.lat ?? []).map((lat, i) => [st.lng?.[i] ?? null, lat, st.altitude?.[i] ?? null]).filter((p) => p[0] != null && p[1] != null)
      if (coords.length < 2) throw new Error('La corsa non ha traccia GPS.')
      return {
        coords,
        name: recent.data?.items.find((x) => x.id === id)?.name ?? null,
      }
    },
    onSuccess: (r) => load(r.coords, r.name),
    onError: fail,
  })
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name,
        coords: flat,
        target_speed_ms: 1000 / flatPace,
        surface: surfFresh,
        waypoints: wps.map((p) => p.slice(0, 2)),
      }
      return editId
        ? api<RouteRow>(`/api/routes/${editId}`, {
            method: 'PATCH',
            body: JSON.stringify(body),
          })
        : api<RouteRow>('/api/routes', post(body))
    },
    onSuccess: (r) => (setEditId(r.id), setSaved(true), setErr(''), qc.invalidateQueries({ queryKey: ['routes'] })),
    onError: fail,
  })
  const open = useMutation({
    mutationFn: (id: number) => api<RouteFull>(`/api/routes/${id}`),
    onSuccess: (r) => load(r.coords, r.name, r.id, r.target_speed_ms, r.surface, r.waypoints),
    onError: fail,
  })
  const del = useMutation({
    mutationFn: (id: number) => api(`/api/routes/${id}`, { method: 'DELETE' }),
    onSuccess: (_, id) => (id === editId && setEditId(null), qc.invalidateQueries({ queryKey: ['routes'] })),
    onError: fail,
  })

  const estS = a ? (a.gap_distance_m / 1000) * flatPace : 0
  return (
    <div className="space-y-5">
      <PageHead eyebrow="Route planner" title="Percorsi">
        <div className="flex flex-wrap gap-2">
          <label className={`${btnGhost} cursor-pointer`}>
            Importa GPX/TCX/FIT
            <input
              type="file"
              accept=".gpx,.tcx,.fit,.gz"
              className="hidden"
              onChange={(e) => (e.target.files?.[0] && importFile.mutate(e.target.files[0]), (e.target.value = ''))}
            />
          </label>
          <select className={field} value="" aria-label="Copia da corsa" onChange={(e) => e.target.value && copyRun.mutate(Number(e.target.value))}>
            <option value="">Copia da corsa…</option>
            {recent.data?.items.map((r) => (
              <option key={r.id} value={r.id}>
                {r.start_time_utc.slice(0, 10)} · {r.name ?? 'Corsa'} · {((r.distance_m ?? 0) / 1000).toFixed(1)} km
              </option>
            ))}
          </select>
        </div>
      </PageHead>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <button className={pill(snapMode)} title="Routing pedonale OSM: marciapiedi, sentieri, parchi; evita superstrade" onClick={() => setSnapMode(true)}>
              A piedi su strade e sentieri
            </button>
            <button className={pill(!snapMode)} onClick={() => setSnapMode(false)}>
              Linea retta
            </button>
            <span className="mx-1 hidden h-6 w-px bg-[#262b33] sm:block" />
            <button className={btnGhost} disabled={!wps.length || busy} onClick={() => void route(wps.slice(0, -1))}>
              Annulla
            </button>
            <button className={btnGhost} disabled={wps.length < 2 || busy} onClick={() => void route([...wps].reverse())}>
              Inverti
            </button>
            <button className={btnGhost} disabled={wps.length < 2 || busy} onClick={() => addPoint(wps[0])}>
              Chiudi anello
            </button>
            <button className={btnGhost} disabled={wps.length < 2 || busy} title="Torna al via per lo stesso percorso, a ritroso" onClick={retrace}>
              Ritorno identico
            </button>
            <button className={btnGhost} disabled={!wps.length || busy} onClick={() => (clear(), setEditId(null), setName(''))}>
              Nuovo
            </button>
            {busy && (
              <span className="text-xs" style={{ color: MUTED }}>
                routing…
              </span>
            )}
          </div>
          <div ref={el} className="h-[55vh] min-h-72 w-full overflow-hidden rounded-[14px]" />
          {!wps.length && (
            <p className="text-sm" style={{ color: MUTED }}>
              Clicca sulla mappa per posizionare la partenza, poi i waypoint.
            </p>
          )}
          {err && <p className="text-sm text-red-400">{err}</p>}
          {an.isError && flat.length >= 2 && <p className="text-sm text-red-400">Analisi non disponibile ({(an.error as Error).message}).</p>}
          {a && (
            <Profile
              a={a}
              onHover={(i) =>
                i == null ? marker.current?.remove() : map.current && marker.current?.setLngLat([a.profile.lng[i], a.profile.lat[i]]).addTo(map.current)
              }
            />
          )}
        </div>

        <div className="space-y-5">
          {wps.length > 0 && (
            <Panel title="Punti" sub="Clic sulla mappa: aggiungi in coda. Trascina un punto per spostarlo, trascina o tocca la linea per inserirne uno.">
              <ol className="max-h-72 space-y-1 overflow-y-auto text-sm">
                {wps.map((_, i) => (
                  <li key={i} className="flex items-center gap-2 rounded-md px-1 py-0.5 hover:bg-[#20252c]">
                    <span
                      className={`grid size-6 shrink-0 place-items-center rounded-full border-2 border-[#eef1f4] text-[11px] ${mono} ${i ? 'bg-[#111418] text-[#eef1f4]' : 'bg-[#22c55e] text-[#111418]'}`}
                    >
                      {i + 1}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[#eef1f4]">
                      {i === 0 ? 'Partenza' : i === wps.length - 1 ? 'Arrivo' : `Punto ${i + 1}`}
                      {i > 0 && (
                        <span className={`${mono} ml-2 text-xs`} style={{ color: MUTED }}>
                          +{legKm[i - 1]?.toFixed(2)} km
                        </span>
                      )}
                    </span>
                    <button
                      className="p-1 text-[#8a93a0] hover:text-[#eef1f4] disabled:opacity-30"
                      aria-label={`Sposta su il punto ${i + 1}`}
                      disabled={!i || busy}
                      onClick={() => move(i, i - 1)}
                    >
                      <ArrowUp size={14} />
                    </button>
                    <button
                      className="p-1 text-[#8a93a0] hover:text-[#eef1f4] disabled:opacity-30"
                      aria-label={`Sposta giù il punto ${i + 1}`}
                      disabled={i === wps.length - 1 || busy}
                      onClick={() => move(i, i + 1)}
                    >
                      <ArrowDown size={14} />
                    </button>
                    <button
                      className="p-1 text-[#8a93a0] hover:text-red-400 disabled:opacity-30"
                      aria-label={`Rimuovi il punto ${i + 1}`}
                      disabled={busy}
                      onClick={() => void route(wps.filter((_, j) => j !== i))}
                    >
                      <X size={14} />
                    </button>
                  </li>
                ))}
              </ol>
            </Panel>
          )}

          <Panel title="Analisi" className={an.isFetching ? 'opacity-70' : ''}>
            {!a ? (
              <p className="text-sm" style={{ color: MUTED }}>
                Servono almeno due punti.
              </p>
            ) : (
              <div className="space-y-4 text-sm">
                <dl className="grid grid-cols-2 gap-3">
                  <Stat label="Distanza" value={km(a.distance_m)} />
                  <Stat label="D+ / D−" value={`${Math.round(a.elev_gain_m)} / ${Math.round(a.elev_loss_m)} m`} />
                  <Stat label="Quota min–max" value={`${Math.round(a.ele_min_m)}–${Math.round(a.ele_max_m)} m`} />
                  <Stat label="Pend. salita (max)" value={`${pct(a.climb_grade_avg)} (${pct(a.grade_max)})`} />
                  <Stat label="Curve" value={`${a.turns}`} sub={`${a.turns_sharp} a gomito · ${a.hairpins} tornanti`} />
                  <Stat label="Sinuosità" value={a.sinuosity?.toFixed(2) ?? '—'} />
                </dl>
                <div>
                  <div className="flex h-2 overflow-hidden rounded-full bg-[#111418]">
                    {BANDS.map(([k, , c]) => (
                      <div
                        key={k}
                        style={{
                          width: `${(a.grade_bands_m[k] / a.distance_m) * 100}%`,
                          background: c,
                        }}
                      />
                    ))}
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs" style={{ color: MUTED }}>
                    {BANDS.map(([k, label, c]) => (
                      <span key={k} className="flex items-center gap-1.5">
                        <span className="size-2 rounded-full" style={{ background: c }} />
                        {label} <span className={mono}>{Math.round((a.grade_bands_m[k] / a.distance_m) * 100)}%</span>
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </Panel>

          {a && (
            <Panel title="Fondo" sub="Da tag OSM surface; strade senza tag = asfalto (stima).">
              {sm && smTot > 0 ? (
                <div className="space-y-2">
                  <div className="flex h-2 overflow-hidden rounded-full bg-[#111418]">
                    {(Object.keys(SURF) as (keyof typeof SURF)[]).map((k) => (
                      <div
                        key={k}
                        style={{
                          width: `${(sm[k] / smTot) * 100}%`,
                          background: SURF[k][1],
                        }}
                      />
                    ))}
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs" style={{ color: MUTED }}>
                    {(Object.keys(SURF) as (keyof typeof SURF)[]).map((k) => (
                      <span key={k} className="flex items-center gap-1.5">
                        <span className="size-2 rounded-full" style={{ background: SURF[k][1] }} />
                        {SURF[k][0]} <span className={mono}>{Math.round((sm[k] / smTot) * 100)}%</span>
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-sm" style={{ color: MUTED }}>
                  {surf.isError ? 'Dati fondo non disponibili (server OSM occupato). ' : 'Calcolo…'}
                  {surf.isError && (
                    <button className="text-[#4c8dff] hover:underline" onClick={() => void surf.refetch()}>
                      Riprova
                    </button>
                  )}
                </p>
              )}
            </Panel>
          )}

          {a && (
            <Panel title="Simulatore" sub="Passo in piano → tempo con il dislivello (Minetti GAP).">
              <label className="block text-sm" style={{ color: MUTED }}>
                Passo in piano <span className={`${mono} text-[#eef1f4]`}>{formatPace(flatPace)}</span>
                <input
                  type="range"
                  min={180}
                  max={480}
                  step={5}
                  value={Math.round(flatPace)}
                  onChange={(e) => (setPace(Number(e.target.value)), setSaved(false))}
                  className="mt-2 w-full accent-[#4c8dff]"
                />
              </label>
              <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                <Stat label="Tempo stimato" value={formatDuration(estS)} />
                <Stat label="Passo medio" value={formatPace(estS / (a.distance_m / 1000))} />
              </dl>
            </Panel>
          )}

          {a && (
            <Panel title="Salva">
              <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => (e.preventDefault(), name.trim() && save.mutate())}>
                <input
                  placeholder="Nome percorso"
                  className={`${field} min-w-0 flex-1`}
                  value={name}
                  onChange={(e) => (setName(e.target.value), setSaved(false))}
                />
                <button type="submit" className={btn} disabled={!name.trim() || save.isPending}>
                  {editId ? 'Aggiorna' : 'Salva'}
                </button>
                {editId && saved && (
                  <a className={btnGhost} href={`/api/routes/${editId}/export-gpx`} download>
                    Esporta GPX
                  </a>
                )}
              </form>
              {editId && !saved && (
                <p className="mt-2 text-xs" style={{ color: MUTED }}>
                  Modifiche non salvate: salva per esportare.
                </p>
              )}
            </Panel>
          )}
        </div>
      </div>

      <Panel title="Percorsi salvati">
        {routes.isError && <p className="text-sm text-red-400">Failed to load routes.</p>}
        {routes.data?.length === 0 && (
          <p className="text-sm" style={{ color: MUTED }}>
            Nessun percorso salvato.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {routes.data?.map((r) => (
            <div key={r.id} className={`rounded-lg border p-3 ${r.id === editId ? 'border-[#4c8dff]' : 'border-[#262b33]'}`}>
              <div className="truncate font-semibold text-[#eef1f4]">{r.name}</div>
              <div className={`${mono} mt-1 text-xs`} style={{ color: MUTED }}>
                {km(r.distance_m)} · {Math.round(r.elev_gain_m)} m D+ · {r.updated_at.slice(0, 10)}
              </div>
              <div className="mt-2 flex gap-3 text-xs">
                <button className="text-[#4c8dff] hover:underline" onClick={() => open.mutate(r.id)}>
                  Modifica
                </button>
                <a className="text-[#4c8dff] hover:underline" href={`/api/routes/${r.id}/export-gpx`} download>
                  Esporta GPX
                </a>
                <button className="ml-auto text-red-400 hover:underline" onClick={() => confirm(`Eliminare "${r.name}"?`) && del.mutate(r.id)}>
                  Elimina
                </button>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  )
}

function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] tracking-[.07em] uppercase" style={{ fontFamily: FM, color: MUTED }}>
        {label}
      </dt>
      <dd className={`${mono} text-[15px] text-[#eef1f4]`}>{value}</dd>
      {sub && (
        <dd className="text-xs" style={{ color: MUTED }}>
          {sub}
        </dd>
      )}
    </div>
  )
}

/** Elevation profile; hovering reports the sample index so the map marker follows. */
function Profile({ a, onHover }: { a: Analysis; onHover: (i: number | null) => void }) {
  const el = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)
  const hover = useRef(onHover)
  const p = a.profile
  const dist = useRef(p.d)
  useEffect(() => {
    hover.current = onHover
    dist.current = p.d
  })

  useEffect(() => {
    const c = echarts.init(el.current!)
    inst.current = c
    c.on('updateAxisPointer', (e: unknown) => {
      const v = (e as { axesInfo?: { value: number }[] }).axesInfo?.[0]?.value
      if (typeof v !== 'number') return
      const xs = dist.current
      let lo = 0
      for (let hi = xs.length - 1; lo < hi;) {
        const m = (lo + hi) >> 1
        if (xs[m] < v * 1000) lo = m + 1
        else hi = m
      }
      hover.current(lo)
    })
    c.getZr().on('globalout', () => hover.current(null))
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(el.current!)
    return () => {
      ro.disconnect()
      c.dispose()
      inst.current = null
    }
  }, [])

  useEffect(() => {
    inst.current?.setOption(
      {
        grid: { left: 44, right: 12, top: 12, bottom: 28 },
        tooltip: {
          trigger: 'axis',
          backgroundColor: '#191d23',
          borderColor: '#262b33',
          textStyle: { color: '#eef1f4', fontFamily: FM },
          formatter: (ps: unknown) => {
            const [x, y, g] = (ps as { data: (number | null)[] }[])[0].data
            return `${x!.toFixed(2)} km<br/>${Math.round(y!)} m · ${pct(g)}`
          },
        },
        xAxis: {
          type: 'value',
          max: 'dataMax',
          axisLabel: { color: MUTED, formatter: '{value} km' },
          splitLine: { show: false },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { color: MUTED, formatter: '{value} m' },
          splitLine: { lineStyle: { color: '#22222a' } },
        },
        series: [
          {
            type: 'line',
            showSymbol: false,
            data: p.d.map((d, i) => [d / 1000, p.ele[i], p.grade[i]]),
            lineStyle: { color: ELEV, width: 1.5 },
            areaStyle: { color: ELEV, opacity: 0.25 },
          },
        ],
      },
      true,
    )
  }, [p])

  return <div ref={el} className="h-44 w-full rounded-[14px] bg-[#191d23]" />
}
