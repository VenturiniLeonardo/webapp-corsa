"""Read-only adapter for the HR VO2max model, including dated calibration baselines."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.domain.models import Activity as A
from app.domain.models import ActivityMetrics, Stream
from app.domain.stream_codec import decode_stream
from app.metrics.vo2max import HALF_LIFE_DAYS, hr_estimate, stream_estimate

MODEL_VERSION = 1
CONFIG_KEYS = ("hr_max", "hr_rest", "steady_cv_threshold")
CalibrationStatus = Literal[
    "none", "applied", "settings_changed", "no_current_runs", "out_of_range"
]


def model_config(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        k: settings.get(k) or (0.08 if k == "steady_cv_threshold" else None) for k in CONFIG_KEYS
    }


@dataclass(frozen=True)
class HrEstimate:
    value: float | None
    n: int
    last_date: str | None
    activity_id: int | None
    configured: bool


def estimate_on(
    db: Session,
    end: date,
    settings: dict[str, Any],
    filters: Sequence[ColumnElement[bool]] = (),
) -> HrEstimate:
    cfg = model_config(settings)
    configured = bool(cfg["hr_max"] and cfg["hr_rest"] and cfg["hr_max"] > cfg["hr_rest"])
    if not configured:
        return HrEstimate(None, 0, None, None, False)
    observations: list[tuple[float, float, str, int]] = []
    for a, m, stream in db.execute(
        select(A, ActivityMetrics, Stream)
        .select_from(A)
        .join(ActivityMetrics, ActivityMetrics.activity_id == A.id)
        .outerjoin(
            Stream,
            Stream.source_record_id == func.coalesce(A.stream_source_id, A.primary_source_id),
        )
        .where(
            *filters,
            A.local_date >= (end - timedelta(days=27)).isoformat(),
            A.local_date <= end.isoformat(),
            A.excluded_from_stats.is_(False),
            A.duplicate_of_id.is_(None),
            A.sport_type == "run",
            A.is_indoor.is_not(True),
            A.upstream_deleted_at.is_(None),
            or_(A.workout_type.is_(None), A.workout_type.not_in(("workout", "race"))),
            ActivityMetrics.gps_suspect.is_not(True),
            A.moving_s > 0,
            A.distance_m > 0,
        )
        .order_by(A.local_date, A.start_time_utc, A.id)
    ):
        value = None
        if stream is not None:
            try:
                value = stream_estimate(
                    decode_stream(stream.data),
                    cfg["hr_max"],
                    cfg["hr_rest"],
                    cfg["steady_cv_threshold"],
                )
            except (ValueError, OSError, EOFError):
                pass
        elif m.is_steady and a.avg_hr is not None and (a.moving_s or 0) >= 1200:
            value = hr_estimate(
                float(a.distance_m or 0) / float(a.moving_s or 1),
                a.avg_hr,
                cfg["hr_max"],
                cfg["hr_rest"],
            )
        if value is not None:
            age = (end - date.fromisoformat(a.local_date)).days
            observations.append((value, 2 ** (-age / HALF_LIFE_DAYS), a.local_date, a.id))
    if not observations:
        return HrEstimate(None, 0, None, None, True)
    return HrEstimate(
        sum(v * w for v, w, _, _ in observations) / sum(w for _, w, _, _ in observations),
        len(observations),
        observations[-1][2],
        observations[-1][3],
        True,
    )


def calibration_result(
    raw: float | None, reference: dict[str, Any] | None, settings: dict[str, Any]
) -> tuple[float | None, CalibrationStatus]:
    if reference is None:
        return raw, "none"
    if reference.get("model_version") != MODEL_VERSION or reference.get("config") != model_config(
        settings
    ):
        return raw, "settings_changed"
    if raw is None:
        return None, "no_current_runs"
    value = raw + reference["value"] - reference["baseline"]
    if value <= 0:
        return raw, "out_of_range"
    return value, "applied"
