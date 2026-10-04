"""Route planner: analyze/snap/import (no save needed), CRUD, GPX export.

Network (server-side, no keys): Open-Meteo elevation for points without ele, OSRM foot routing
(routing.openstreetmap.de) for snapping. Only route coordinates leave the box."""

import math
from typing import Annotated, Any
from urllib.parse import unquote

import httpx
from defusedxml import ElementTree  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.activities import Db
from app.domain.models import Route
from app.ingest.files import MAX_FILE, Rejected, _ln, _parse_fit, _parse_xml, _ungz
from app.metrics.routes import SPACING_M, Coord, analyze, resample, to_gpx

router = APIRouter(prefix="/api")
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
OSRM_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/driving/"
ELE_BATCH = 100  # Open-Meteo limit per request
MAX_COORDS = 50_000
_ele_cache: dict[
    tuple[float, float], float
] = {}  # ponytail: unbounded, per process; LRU if it grows

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Coords = Annotated[
    list[Annotated[list[float | None], Field(min_length=2, max_length=3)]],
    Field(min_length=2, max_length=MAX_COORDS),
]


class AnalyzeIn(BaseModel):
    coords: Coords


class SnapIn(BaseModel):
    coords: Annotated[
        list[Annotated[list[float], Field(min_length=2, max_length=2)]],
        Field(min_length=2, max_length=2),
    ]


class RouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    notes: str | None = None
    coords: Coords | None = None
    target_speed_ms: float | None = Field(None, gt=0, le=10)
    surface: dict[str, Any] | None = None  # as returned by /routes/surface
    waypoints: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        None, max_length=500
    )


def fetch_elevations(pts: list[tuple[float, float]]) -> list[float]:
    """Elevation (m) per (lng, lat), DEM ~90 m. Rounded to 1e-4° so redraws hit the cache."""
    keys = [(round(lng, 4), round(lat, 4)) for lng, lat in pts]
    miss = list(dict.fromkeys(k for k in keys if k not in _ele_cache))
    with httpx.Client(timeout=15) as c:
        for i in range(0, len(miss), ELE_BATCH):
            chunk = miss[i : i + ELE_BATCH]
            r = c.get(
                ELEVATION_URL,
                params={
                    "latitude": ",".join(str(k[1]) for k in chunk),
                    "longitude": ",".join(str(k[0]) for k in chunk),
                },
            )
            r.raise_for_status()
            _ele_cache.update(zip(chunk, r.json()["elevation"], strict=True))
    return [float(_ele_cache[k]) for k in keys]


def _prepare(coords: list[Coord]) -> list[Coord]:
    """Resample; fill elevation from Open-Meteo unless every point carries one."""
    for p in coords:
        if p[0] is None or p[1] is None or not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
            raise HTTPException(422, "coords must be [lng, lat, ele?] in WGS84")
    pts = resample(coords)
    if len(pts) < 2:
        raise HTTPException(422, "route has zero length")
    if any(p[2] is None for p in pts):
        try:
            ele = fetch_elevations([(p[0], p[1]) for p in pts])  # type: ignore[misc]
        except httpx.HTTPError as e:
            raise HTTPException(502, f"elevation service: {type(e).__name__}") from e
        pts = [[p[0], p[1], e] for p, e in zip(pts, ele, strict=True)]
    return pts


def _analysis(pts: list[Coord]) -> dict[str, Any]:
    out = analyze(pts)
    out["profile"] |= {"lng": [round(p[0], 6) for p in pts], "lat": [round(p[1], 6) for p in pts]}  # type: ignore[arg-type]
    return out


def _get(s: Session, rid: int) -> Route:
    r = s.get(Route, rid)
    if r is None:
        raise HTTPException(404, "route not found")
    return r


def _out(r: Route, full: bool = False) -> dict[str, Any]:
    out = {
        "id": r.id,
        "name": r.name,
        "notes": r.notes,
        "distance_m": r.distance_m,
        "elev_gain_m": r.elev_gain_m,
        "elev_loss_m": r.elev_loss_m,
        "target_speed_ms": r.target_speed_ms,
        "surface": r.surface,
        "waypoints": r.waypoints,
        "updated_at": r.updated_at,
    }
    if full:
        out |= {"coords": r.coords, "analysis": _analysis(r.coords)}
    return out


def _apply(r: Route, body: RouteIn) -> None:
    for f in body.model_fields_set - {"coords"}:
        if f != "name" or body.name is not None:
            setattr(r, f, getattr(body, f))
    if body.coords is not None:
        if "surface" not in body.model_fields_set:
            r.surface = None  # geometry changed: saved surface is stale
        pts = _prepare(body.coords)
        a = analyze(pts)
        r.coords = [[round(p[0], 6), round(p[1], 6), round(p[2], 1)] for p in pts]  # type: ignore[arg-type]
        r.distance_m, r.elev_gain_m, r.elev_loss_m = (
            a["distance_m"],
            a["elev_gain_m"],
            a["elev_loss_m"],
        )


@router.post("/routes/analyze")
def analyze_route(body: AnalyzeIn) -> dict[str, Any]:
    return _analysis(_prepare(body.coords))


@router.post("/routes/snap")
def snap(body: SnapIn) -> dict[str, Any]:
    """Foot route between two [lng, lat] points along roads/paths."""
    path = ";".join(f"{lng},{lat}" for lng, lat in body.coords)
    try:
        r = httpx.get(
            OSRM_URL + path, params={"overview": "full", "geometries": "geojson"}, timeout=15
        )
        r.raise_for_status()
        coords = r.json()["routes"][0]["geometry"]["coordinates"]
    except (httpx.HTTPError, KeyError, IndexError) as e:
        raise HTTPException(502, f"routing service: {type(e).__name__}") from e
    return {"coords": coords}


SURFACE = {
    **dict.fromkeys(("asphalt", "paved", "concrete", "concrete:plates", "concrete:lanes"), "asphalt"),
    **dict.fromkeys(
        ("sett", "paving_stones", "cobblestone", "unhewn_cobblestone", "bricks", "metal"), "stone"
    ),
    **dict.fromkeys(
        (
            "unpaved", "compacted", "fine_gravel", "gravel", "dirt", "earth", "ground",
            "grass", "sand", "mud", "pebblestone", "woodchips", "wood",
        ),
        "unpaved",
    ),
}  # fmt: skip
ROAD_HW = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "service", "living_street", "pedestrian", "cycleway", "road",
    *(f"{h}_link" for h in ("motorway", "trunk", "primary", "secondary", "tertiary")),
}  # fmt: skip
SURF_SAMPLE_M = 100
SURF_RADIUS_M = 25
OVERPASS_URLS = (  # first is often overloaded (504): fall through to the mirror
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
UA = {"User-Agent": "corsa-webapp/1.0"}  # Overpass answers 406 to generic client UAs


def _way_surface(tags: dict[str, str]) -> str:
    s = SURFACE.get(tags.get("surface", ""))
    if s:
        return s
    return "asphalt" if tags.get("highway") in ROAD_HW else "unknown"  # est.: no surface tag


@router.post("/routes/surface")
def surface(body: AnalyzeIn) -> dict[str, Any]:
    """Metres per surface class (asphalt/stone/unpaved/unknown) from OSM `surface` tags (Overpass).
    Roads without the tag count as asphalt, paths/tracks without it as unknown."""
    for p in body.coords:
        if p[0] is None or p[1] is None:
            raise HTTPException(422, "coords must be [lng, lat, ele?]")
    pts = resample(body.coords)
    if len(pts) < 2:
        raise HTTPException(422, "route has zero length")
    k = max(
        round(SURF_SAMPLE_M / SPACING_M), len(pts) // 250 + 1
    )  # ponytail: cap polyline ~250 pts
    samples = pts[::k]
    line = ",".join(f"{p[1]:.5f},{p[0]:.5f}" for p in samples)
    q = f"[out:json][timeout:25];way(around:{SURF_RADIUS_M},{line})[highway];out tags geom;"
    ways = None
    for url in OVERPASS_URLS * 2:  # ponytail: public Overpass often 504s; 2 rounds, no backoff
        try:
            r = httpx.post(url, data={"data": q}, headers=UA, timeout=35)
            r.raise_for_status()
            ways = r.json()["elements"]
            break
        except (httpx.HTTPError, KeyError, ValueError) as e:
            err = e
    if ways is None:
        raise HTTPException(502, f"overpass: {type(err).__name__}") from err
    cos = math.cos(math.radians(samples[0][1]))  # type: ignore[arg-type]

    def xy(lng: float, lat: float) -> tuple[float, float]:
        return lng * 111320 * cos, lat * 110574

    segs = [
        (xy(a["lon"], a["lat"]), xy(b["lon"], b["lat"]), _way_surface(w.get("tags", {})))
        for w in ways
        for a, b in zip(w.get("geometry", []), w.get("geometry", [])[1:], strict=False)
    ]

    def dist(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = (
            0.0
            if dx == dy == 0
            else max(0, min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy)))
        )
        return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)

    out = dict.fromkeys(("asphalt", "stone", "unpaved", "unknown"), 0.0)
    sectors: list[dict[str, Any]] = []  # consecutive same-class samples merged, with geometry
    for i, p in enumerate(samples):
        px, py = xy(p[0], p[1])  # type: ignore[arg-type]
        best = min(((dist(px, py, a, b), s) for a, b, s in segs), default=(1e9, "unknown"))
        cls = best[1] if best[0] <= SURF_RADIUS_M else "unknown"
        out[cls] += k * SPACING_M
        line = [[round(q[0], 5), round(q[1], 5)] for q in pts[i * k : (i + 1) * k + 1]]  # type: ignore[arg-type,misc]
        if sectors and sectors[-1]["k"] == cls:
            sectors[-1]["coords"] += line[1:]
        else:
            sectors.append({"k": cls, "coords": line})
    return {"surface_m": out, "sectors": [s for s in sectors if len(s["coords"]) >= 2]}


@router.post("/routes/import-file")
async def import_file(request: Request) -> dict[str, Any]:
    """Raw body (octet-stream) + X-Filename: GPX (trk/rte), TCX, FIT[.gz] -> [lng, lat, ele]."""
    data = await request.body()
    if len(data) > MAX_FILE:
        raise HTTPException(413, "file too large")
    try:
        name, data = _ungz(unquote(request.headers.get("x-filename", "")), data)
        ext = name.lower().rsplit(".", 1)[-1]
        title = name.rsplit(".", 1)[0] or None
        if ext == "gpx":  # routes have no timestamps: not _parse_xml, which needs <time>
            root = ElementTree.fromstring(data)
            coords: list[Coord] = []
            for el in root.iter():
                if _ln(el.tag) in ("trkpt", "rtept"):
                    ele = next((c.text for c in el if _ln(c.tag) == "ele" and c.text), None)
                    coords.append(
                        [float(el.get("lon")), float(el.get("lat")), float(ele) if ele else None]
                    )
            gname = root.find(".//{*}name")
            title = (gname.text or "").strip() or title if gname is not None else title
        elif ext in ("tcx", "fit"):
            pts, meta = _parse_fit(data) if ext == "fit" else _parse_xml(data, "Trackpoint")
            coords = [
                [p["lng"], p["lat"], p.get("alt")]
                for p in pts
                if p.get("lat") is not None and p.get("lng") is not None
            ]
            title = meta.get("name") or title
        else:
            raise Rejected("expected .gpx, .tcx or .fit")
    except Rejected as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # malformed XML / numbers
        raise HTTPException(422, f"unreadable file: {type(e).__name__}") from e
    if len(coords) < 2:
        raise HTTPException(422, "no GPS points in file")
    return {"name": title, "coords": coords}


@router.get("/routes")
def list_routes(s: Db) -> list[dict[str, Any]]:
    return [
        _out(r) for r in s.scalars(select(Route).order_by(Route.updated_at.desc(), Route.id.desc()))
    ]


@router.get("/routes/{rid}")
def get_route(rid: int, s: Db) -> dict[str, Any]:
    return _out(_get(s, rid), full=True)


@router.post("/routes", status_code=201)
def create_route(body: RouteIn, s: Db) -> dict[str, Any]:
    if body.name is None or body.coords is None:
        raise HTTPException(422, "name and coords are required")
    r = Route()
    _apply(r, body)
    s.add(r)
    s.commit()
    return _out(r)


@router.patch("/routes/{rid}")
def patch_route(rid: int, body: RouteIn, s: Db) -> dict[str, Any]:
    r = _get(s, rid)
    _apply(r, body)
    s.commit()
    return _out(r)


@router.delete("/routes/{rid}", status_code=204)
def delete_route(rid: int, s: Db) -> None:
    s.delete(_get(s, rid))
    s.commit()


@router.get("/routes/{rid}/export-gpx")
def export_gpx(rid: int, s: Db) -> Response:
    r = _get(s, rid)
    fname = "".join(c if c.isalnum() or c in "-_" else "_" for c in r.name)[:60] or "route"
    return Response(
        to_gpx(r.name, r.coords),
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}.gpx"'},
    )
