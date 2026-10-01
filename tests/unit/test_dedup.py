from datetime import UTC, datetime, timedelta

import pytest

from app.domain.dedup import ActivitySummary, DedupAction, find_duplicate_candidate

T0 = datetime(2024, 6, 1, 7, 0, tzinfo=UTC)
STRAVA = frozenset({"strava"})


def act(
    id: int, start: datetime = T0, elapsed: int = 3600, dist: float | None = 10000.0
) -> ActivitySummary:
    return ActivitySummary(id, start, elapsed, dist, STRAVA)


def test_same_start_different_source_attaches() -> None:
    got = find_duplicate_candidate(T0, 3590, 10050.0, [act(1)], "file_fit")
    assert got == (DedupAction.ATTACH_TO_EXISTING, 1)


def test_overlap_same_source_flags() -> None:
    got = find_duplicate_candidate(T0 + timedelta(seconds=30), 3600, 9900.0, [act(1)], "strava")
    assert got == (DedupAction.FLAG_DUPLICATE, 1)


def test_multiple_candidates_flag_nearest() -> None:
    existing = [act(1, T0 + timedelta(seconds=90)), act(2, T0 + timedelta(seconds=10))]
    got = find_duplicate_candidate(T0, 3600, 10000.0, existing, "file_fit")
    assert got == (DedupAction.FLAG_DUPLICATE, 2)


def test_morning_and_evening_same_day_are_new() -> None:
    evening = T0.replace(hour=18)
    got = find_duplicate_candidate(evening, 3600, 10000.0, [act(1)], "strava")
    assert got == (DedupAction.NEW_ACTIVITY, None)


@pytest.mark.parametrize(("delta", "expected"), [(119, True), (120, True), (121, False)])
def test_start_delta_boundary(delta: int, expected: bool) -> None:
    action, _ = find_duplicate_candidate(
        T0 + timedelta(seconds=delta), 3600, 10000.0, [act(1)], "file_fit"
    )
    assert (action == DedupAction.ATTACH_TO_EXISTING) is expected


def test_low_overlap_is_new() -> None:
    # same start, but a 20 min run vs 60 min: overlap 33%
    got = find_duplicate_candidate(T0, 1200, None, [act(1, dist=None)], "file_fit")
    assert got == (DedupAction.NEW_ACTIVITY, None)


@pytest.mark.parametrize(("dist", "expected"), [(9000.0, True), (8900.0, False), (None, True)])
def test_distance_tolerance(dist: float | None, expected: bool) -> None:
    action, _ = find_duplicate_candidate(T0, 3600, dist, [act(1)], "file_fit")
    assert (action == DedupAction.ATTACH_TO_EXISTING) is expected


def test_no_existing() -> None:
    assert find_duplicate_candidate(T0, 3600, 10000.0, [], "strava") == (
        DedupAction.NEW_ACTIVITY,
        None,
    )
