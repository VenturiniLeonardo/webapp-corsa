"""Shoe tracker: CRUD + mileage aggregated from activities (SI units; pace derived)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.api.activities import Db
from app.domain.models import Activity, Shoe

router = APIRouter(prefix="/api")
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ShoeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    brand: str | None = None
    model: str | None = None
    target_distance_m: float | None = Field(None, gt=0)
    initial_distance_m: float | None = Field(None, ge=0)
    is_default: bool | None = None
    retired_at: date | None = None


class ShoeOut(BaseModel):
    id: int
    name: str
    brand: str | None
    model: str | None
    target_distance_m: float
    initial_distance_m: float
    is_default: bool
    retired_at: str | None
    total_distance_m: float
    run_count: int
    weighted_pace_s_per_km: float | None
    percent_worn: float


def _get(s: Session, sid: int) -> Shoe:
    shoe = s.get(Shoe, sid)
    if shoe is None:
        raise HTTPException(404, "shoe not found")
    return shoe


def _apply(s: Session, shoe: Shoe, body: ShoeIn) -> None:
    for f in body.model_fields_set:
        v = getattr(body, f)
        if f == "retired_at":
            shoe.retired_at = v.isoformat() if v else None
        elif v is not None:
            setattr(shoe, f, v)
    if shoe.retired_at:
        shoe.is_default = False
    if shoe.is_default:  # single default
        s.execute(update(Shoe).where(Shoe.id != shoe.id).values(is_default=False))


@router.get("/shoes")
def list_shoes(s: Db) -> list[ShoeOut]:
    A = Activity
    timed = and_(A.distance_m > 0, A.moving_s > 0)
    agg = {
        sid: (n, float(d or 0), int(pm or 0), float(pd or 0))
        for sid, n, d, pm, pd in s.execute(
            select(
                A.shoe_id,
                func.count(),
                func.sum(A.distance_m),
                # pace only over runs with both fields, so gaps don't skew it
                func.sum(A.moving_s).filter(timed),
                func.sum(A.distance_m).filter(timed),
            )
            .where(A.shoe_id.is_not(None), A.duplicate_of_id.is_(None))
            .group_by(A.shoe_id)
        )
    }
    out = []
    for sh in s.scalars(select(Shoe).order_by(Shoe.retired_at.is_not(None), Shoe.id.desc())):
        n, dist, pmov, pdist = agg.get(sh.id, (0, 0.0, 0, 0.0))
        total = sh.initial_distance_m + dist
        out.append(
            ShoeOut(
                id=sh.id,
                name=sh.name,
                brand=sh.brand,
                model=sh.model,
                target_distance_m=sh.target_distance_m,
                initial_distance_m=sh.initial_distance_m,
                is_default=sh.is_default,
                retired_at=sh.retired_at,
                total_distance_m=total,
                run_count=n,
                weighted_pace_s_per_km=pmov / (pdist / 1000) if pdist else None,
                percent_worn=total / sh.target_distance_m * 100,
            )
        )
    return out


@router.post("/shoes", status_code=201)
def create_shoe(body: ShoeIn, s: Db) -> dict[str, int]:
    if body.name is None:
        raise HTTPException(422, "name is required")
    shoe = Shoe(name=body.name)
    s.add(shoe)
    s.flush()
    _apply(s, shoe, body)
    s.commit()
    return {"id": shoe.id}


@router.patch("/shoes/{sid}")
def patch_shoe(sid: int, body: ShoeIn, s: Db) -> dict[str, int]:
    _apply(s, _get(s, sid), body)
    s.commit()
    return {"id": sid}


@router.delete("/shoes/{sid}", status_code=204)
def delete_shoe(sid: int, s: Db) -> None:
    shoe = _get(s, sid)
    if s.scalar(select(func.count()).where(Activity.shoe_id == sid)):
        raise HTTPException(409, "shoe has linked activities: retire it instead")
    s.delete(shoe)
    s.commit()
