"""Pure cross-source dedup decision (PLAN §8.4). No I/O."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

MAX_START_DELTA_S = 120
MIN_OVERLAP = 0.8
MAX_DIST_DELTA = 0.10


class DedupAction(StrEnum):
    NEW_ACTIVITY = "new_activity"
    ATTACH_TO_EXISTING = "attach_to_existing"
    FLAG_DUPLICATE = "flag_duplicate"


@dataclass(frozen=True)
class ActivitySummary:
    id: int
    start_utc: datetime
    elapsed_s: int
    distance_m: float | None
    sources: frozenset[str]  # sources already attached to this activity


def _overlap_ratio(s1: datetime, e1_s: int, s2: datetime, e2_s: int) -> float:
    """Overlap as a fraction of the LONGER interval: both must be mostly covered."""
    end1, end2 = s1 + timedelta(seconds=e1_s), s2 + timedelta(seconds=e2_s)
    overlap = (min(end1, end2) - max(s1, s2)).total_seconds()
    longest = max(e1_s, e2_s)
    if longest <= 0:
        return 1.0 if s1 == s2 else 0.0
    return max(overlap, 0.0) / longest


def _matches(start: datetime, elapsed_s: int, dist_m: float | None, a: ActivitySummary) -> bool:
    if abs((start - a.start_utc).total_seconds()) > MAX_START_DELTA_S:
        return False
    if _overlap_ratio(start, elapsed_s, a.start_utc, a.elapsed_s) < MIN_OVERLAP:
        return False
    if dist_m and a.distance_m:
        return abs(dist_m - a.distance_m) <= MAX_DIST_DELTA * max(dist_m, a.distance_m)
    return True


def find_duplicate_candidate(
    new_start_utc: datetime,
    new_elapsed_s: int,
    new_dist_m: float | None,
    existing_activities: list[ActivitySummary],
    new_source: str,
) -> tuple[DedupAction, int | None]:
    """Returns the action and the target activity id (nearest start if several match)."""
    cands = sorted(
        (a for a in existing_activities if _matches(new_start_utc, new_elapsed_s, new_dist_m, a)),
        key=lambda a: (abs((new_start_utc - a.start_utc).total_seconds()), a.id),
    )
    if not cands:
        return DedupAction.NEW_ACTIVITY, None
    if len(cands) == 1 and new_source not in cands[0].sources:
        return DedupAction.ATTACH_TO_EXISTING, cands[0].id
    return DedupAction.FLAG_DUPLICATE, cands[0].id
