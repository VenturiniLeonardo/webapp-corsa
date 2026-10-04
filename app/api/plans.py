"""Training-plan calendars (ICS): stored verbatim, events parsed on read, served to Apple Calendar."""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.activities import Db
from app.domain.models import Plan
from app.ingest.hae import TZ

router = APIRouter(prefix="/api")


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ics: str = Field(max_length=1_000_000)  # a 16-week plan is ~35 kB
    name: str | None = Field(None, max_length=100)  # used when the file has no X-WR-CALNAME


def _text(v: str) -> str:  # RFC 5545 TEXT unescape
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m[1] in "nN" else m[1], v)


def _when(params: list[str], v: str) -> str:
    """Local wall-clock "YYYY-MM-DDTHH:MM", or "YYYY-MM-DD" for all-day events."""
    if "VALUE=DATE" in params or len(v) == 8:
        return datetime.strptime(v, "%Y%m%d").replace(tzinfo=UTC).strftime("%Y-%m-%d")
    # ponytail: only UTC ("Z") is converted; TZID values are taken as home-zone wall clock
    home = ZoneInfo(TZ)
    d = datetime.strptime(v.removesuffix("Z"), "%Y%m%dT%H%M%S").replace(
        tzinfo=UTC if v.endswith("Z") else home
    )
    return d.astimezone(home).strftime("%Y-%m-%dT%H:%M")


def parse_ics(ics: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Minimal reader: X-WR-CALNAME/CALDESC + VEVENT basics; nested VALARM etc. are skipped."""
    stack: list[str] = []
    cal: dict[str, str] = {}
    events: list[dict[str, str]] = []
    for line in re.sub(r"\r?\n[ \t]", "", ics).splitlines():
        name, _, value = line.partition(":")
        key, *params = name.split(";")
        key = key.upper()
        if key == "BEGIN":
            stack.append(value.upper())
            if stack[-1] == "VEVENT":
                events.append({})
        elif key == "END":
            stack = stack[:-1]
        elif stack == ["VCALENDAR"] and key in ("X-WR-CALNAME", "X-WR-CALDESC"):
            cal[key] = _text(value)
        elif stack[-1:] == ["VEVENT"] and key in ("DTSTART", "DTEND"):
            events[-1][key] = _when(params, value)
        elif stack[-1:] == ["VEVENT"] and key in ("UID", "SUMMARY", "DESCRIPTION"):
            events[-1][key] = _text(value)
    return cal, [e for e in events if "DTSTART" in e]


def _out(p: Plan) -> dict[str, Any]:
    cal, events = parse_ics(p.ics)
    evs = [
        {
            "uid": e.get("UID"),
            "start": e["DTSTART"],
            "end": e.get("DTEND"),
            "summary": e.get("SUMMARY", ""),
            "description": e.get("DESCRIPTION", ""),
        }
        for e in sorted(events, key=lambda e: e["DTSTART"])
    ]
    desc = cal.get("X-WR-CALDESC")
    return {
        "id": p.id,
        "name": p.name,
        "description": desc,
        "updated_at": p.updated_at,
        "events": evs,
    }


@router.get("/plans")
def list_plans(s: Db) -> list[dict[str, Any]]:
    out = [_out(p) for p in s.scalars(select(Plan))]
    return sorted(out, key=lambda p: p["events"][0]["start"])


@router.post("/plans")
def upsert_plan(body: PlanIn, s: Db) -> dict[str, Any]:
    """Create, or replace the plan with the same calendar name in place (subscriptions keep their URL)."""
    try:
        cal, events = parse_ics(body.ics)
    except ValueError as e:
        raise HTTPException(422, f"invalid ICS date: {e}") from e
    name = (cal.get("X-WR-CALNAME") or body.name or "").strip()[:100]
    if "BEGIN:VCALENDAR" not in body.ics[:200] or not events or not name:
        raise HTTPException(422, "need a VCALENDAR with a name and at least one VEVENT")
    p = s.scalar(select(Plan).where(Plan.name == name))
    created = p is None
    if p is None:
        p = Plan(name=name, ics=body.ics)
        s.add(p)
    else:
        p.ics = body.ics
    s.commit()
    return {"id": p.id, "created": created}


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uid: str = Field(max_length=200)
    to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")  # target local day


def _shift(params: str, v: str, n: int) -> str:
    """Move an ICS date/date-time value by n local days, keeping its form (DATE, floating/TZID, Z)."""
    day = timedelta(days=n)
    if "VALUE=DATE" in params or len(v) == 8:
        return (date.fromisoformat(f"{v[:4]}-{v[4:6]}-{v[6:]}") + day).strftime("%Y%m%d")
    home = ZoneInfo(TZ)
    z = v.endswith("Z")
    d = datetime.strptime(v.removesuffix("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=UTC if z else None)
    if z:  # keep the local wall clock across DST changes
        d = (d.astimezone(home).replace(tzinfo=None) + day).replace(tzinfo=home).astimezone(UTC)
    else:
        d += day
    return d.strftime("%Y%m%dT%H%M%S") + ("Z" if z else "")


@router.patch("/plans/{pid}/move")
def move_event(pid: int, body: MoveIn, s: Db) -> dict[str, Any]:
    """Drag-and-drop: shift one event (and its end) to another day, editing the stored ICS in place."""
    p = s.get(Plan, pid)
    if p is None:
        raise HTTPException(404, "plan not found")
    _, events = parse_ics(p.ics)
    ev = next((e for e in events if e.get("UID") == body.uid), None)
    if ev is None:
        raise HTTPException(404, "event not found")
    try:
        n = (date.fromisoformat(body.to) - date.fromisoformat(ev["DTSTART"][:10])).days
    except ValueError as e:
        raise HTTPException(422, "invalid day") from e
    done = False

    def block(m: re.Match[str]) -> str:
        nonlocal done
        unfolded = re.sub(r"\r?\n[ \t]", "", m[0])
        u = re.search(r"^UID:(.*?)\r?$", unfolded, re.MULTILINE)
        if done or not u or _text(u[1]) != body.uid:
            return m[0]
        done = True
        return re.sub(
            r"^(DTSTART|DTEND)([^:\r\n]*):(\S+)",
            lambda x: f"{x[1]}{x[2]}:{_shift(x[2], x[3], n)}",
            m[0],
            flags=re.MULTILINE,
        )

    if n:
        p.ics = re.sub(r"BEGIN:VEVENT.*?END:VEVENT", block, p.ics, flags=re.DOTALL)
        s.commit()
    return _out(p)


@router.get("/plans/{pid}.ics")
def plan_ics(pid: int, s: Db) -> Response:
    p = s.get(Plan, pid)
    if p is None:
        raise HTTPException(404, "plan not found")
    fname = re.sub(r"[^A-Za-z0-9-]+", "_", p.name).strip("_")[:60] or "plan"
    return Response(
        p.ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f'inline; filename="{fname}.ics"'},
    )
