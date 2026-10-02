"""Planned-route analysis: geometry, elevation, grades, turns, sinuosity, Minetti GAP distance.

Input: [lng, lat, ele|None] polyline. Resampled at a fixed spacing first, so turn and grade
math sees evenly spaced points whatever the source (OSRM, hand-drawn, GPS file)."""

from itertools import pairwise
from math import atan2, cos, degrees, hypot, radians
from typing import Any
from xml.sax.saxutils import escape

from app.ingest.hae import _gain, _haversine
from app.metrics.engine import compute_gap_distance, compute_grades

SPACING_M = 20.0
MAX_POINTS = 2000  # long routes: spacing grows instead
TURN_DEG, SHARP_DEG, HAIRPIN_DEG = 45.0, 75.0, 120.0
TURN_ARM = 2  # bearing over 2 samples (40 m) on each side of the turn
LOOP_GAP_M = 200.0  # start/end closer than this = loop
BANDS = (
    ("flat", -0.02, 0.02),
    ("gentle", 0.02, 0.05),
    ("steep", 0.05, 1e9),
    ("down", -1e9, -0.02),
)  # first match

Coord = list[float | None]


def cumdist(pts: list[Coord]) -> list[float]:
    d = [0.0]
    for a, b in pairwise(pts):
        d.append(d[-1] + _haversine((a[1], a[0]), (b[1], b[0])))  # type: ignore[arg-type]
    return d


def resample(pts: list[Coord]) -> list[Coord]:
    """Points every SPACING_M (or total/MAX_POINTS) along the line; ele interpolated, None kept."""
    d = cumdist(pts)
    if d[-1] == 0:
        return [list(pts[0])]
    step = max(SPACING_M, d[-1] / MAX_POINTS)
    n = max(1, round(d[-1] / step))
    out, j = [], 0
    for k in range(n + 1):
        x = d[-1] * k / n
        while j < len(d) - 2 and d[j + 1] < x:
            j += 1
        seg = d[j + 1] - d[j]
        f = (x - d[j]) / seg if seg > 0 else 0.0
        a, b = pts[j], pts[j + 1]
        e0, e1 = a[2] if len(a) > 2 else None, b[2] if len(b) > 2 else None
        ele = e0 + (e1 - e0) * f if e0 is not None and e1 is not None else None
        out.append([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, ele])  # type: ignore[operator]
    return out


def _xy(pts: list[Coord]) -> list[tuple[float, float]]:
    """Local equirectangular metres: fine for turn angles and a hull over a run-sized area."""
    lat0 = radians(pts[0][1])  # type: ignore[arg-type]
    return [(radians(p[0]) * cos(lat0) * 6371000, radians(p[1]) * 6371000) for p in pts]  # type: ignore[arg-type]


def turns(pts: list[Coord]) -> tuple[int, int, int]:
    """(turns >= 45°, sharp >= 75°, hairpins >= 120°): consecutive samples above 45° = one turn."""
    xy, k = _xy(pts), TURN_ARM
    out = [0, 0, 0]
    peak = 0.0
    for i in range(k + 1, len(xy) - k - 1):
        (ax, ay), (bx, by) = xy[i - k - 1], xy[i - 1]
        (cx, cy), (dx, dy) = xy[i + 1], xy[i + k + 1]
        dev = abs(degrees(atan2(dy - cy, dx - cx) - atan2(by - ay, bx - ax))) % 360
        dev = 360 - dev if dev > 180 else dev
        if dev >= TURN_DEG:
            peak = max(peak, dev)
            continue
        if peak:
            for n, lim in enumerate((TURN_DEG, SHARP_DEG, HAIRPIN_DEG)):
                out[n] += peak >= lim
            peak = 0.0
    return out[0], out[1], out[2]


def _hull_perimeter(xy: list[tuple[float, float]]) -> float:
    p = sorted(set(xy))
    if len(p) < 3:
        return 0.0

    def half(seq: list[tuple[float, float]]) -> list[tuple[float, float]]:
        h: list[tuple[float, float]] = []
        for q in seq:
            while (
                len(h) >= 2
                and (h[-1][0] - h[-2][0]) * (q[1] - h[-2][1])
                - (h[-1][1] - h[-2][1]) * (q[0] - h[-2][0])
                <= 0
            ):
                h.pop()
            h.append(q)
        return h[:-1]

    hull = half(p) + half(p[::-1])
    return sum(hypot(a[0] - b[0], a[1] - b[1]) for a, b in pairwise(hull + hull[:1]))


def sinuosity(pts: list[Coord], length: float) -> float | None:
    gap = _haversine((pts[0][1], pts[0][0]), (pts[-1][1], pts[-1][0]))  # type: ignore[arg-type]
    ref = _hull_perimeter(_xy(pts)) if gap < LOOP_GAP_M else gap
    return length / ref if ref > 0 else None


def analyze(pts: list[Coord]) -> dict[str, Any]:
    """`pts` already resampled, every ele filled."""
    d = cumdist(pts)
    ele = [float(p[2]) for p in pts]  # type: ignore[arg-type]
    grades = compute_grades(d, ele)
    bands = dict.fromkeys((b[0] for b in BANDS), 0.0)
    climb_d = climb_rise = 0.0
    for i in range(1, len(d)):
        g, dd = grades[i] or 0.0, d[i] - d[i - 1]
        bands[next(n for n, lo, hi in BANDS if lo <= g <= hi)] += dd
        if g > 0.02:
            climb_d, climb_rise = climb_d + dd, climb_rise + g * dd
    t, sharp, hairpins = turns(pts)
    return {
        "distance_m": d[-1],
        "elev_gain_m": _gain(ele),
        "elev_loss_m": _gain(ele[::-1]),
        "ele_min_m": min(ele),
        "ele_max_m": max(ele),
        "climb_grade_avg": climb_rise / climb_d if climb_d else None,
        "grade_max": max((g for g in grades if g is not None), default=None),
        "grade_bands_m": bands,
        "turns": t,
        "turns_sharp": sharp,
        "hairpins": hairpins,
        "sinuosity": sinuosity(pts, d[-1]),
        "gap_distance_m": compute_gap_distance(d, grades)[-1],
        "profile": {
            "d": [round(x, 1) for x in d],
            "ele": [round(x, 1) for x in ele],
            "grade": [round(g, 4) if g is not None else None for g in grades],
        },
    }


def to_gpx(name: str, pts: list[Coord]) -> str:
    """GPX 1.1 track (WGS84, ele) for watches / Garmin / Coros."""
    rows = "".join(
        f'<trkpt lat="{p[1]:.7f}" lon="{p[0]:.7f}">'
        + (f"<ele>{p[2]:.1f}</ele>" if p[2] is not None else "")
        + "</trkpt>"
        for p in pts
    )
    n = escape(name)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="Corsa" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<metadata><name>{n}</name></metadata><trk><name>{n}</name><trkseg>{rows}</trkseg></trk></gpx>\n"
    )
