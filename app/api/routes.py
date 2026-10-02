"""Route planner: analyze/snap/import (no save needed), CRUD, GPX export.

Network (server-side, no keys): Open-Meteo elevation for points without ele, OSRM foot routing
(routing.openstreetmap.de) for snapping. Only route coordinates leave the box."""

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
from app.metrics.routes import Coord, analyze, resample, to_gpx

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
