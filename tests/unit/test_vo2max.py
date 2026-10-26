import math

import pytest

from app.metrics.vo2max import hr_estimate, stream_estimate


def channels(seconds=1800, step=5, speed=3.0, hr=150.0):
    time = list(range(0, seconds + 1, step))
    return {"time": time, "distance": [speed * t for t in time], "hr": [hr] * len(time)}


def test_level_running_reference_and_hr_reserve():
    # ACSM: 3.5 + .2 * 180 = 39.5; HR reserve = (150-60)/(190-60).
    assert hr_estimate(3, 150, 190, 60) == pytest.approx(55.5)
    assert stream_estimate(channels(), 190, 60) == pytest.approx(55.5)


def test_long_run_uses_regular_blocks_despite_whole_run_pace_changes():
    ch = channels(8100)
    ch["distance"] = [2.3 * min(t, 3900) + 2.75 * max(0, t - 3900) for t in ch["time"]]
    value = stream_estimate(ch, 190, 60)
    assert hr_estimate(2.3, 150, 190, 60) < value < hr_estimate(2.75, 150, 190, 60)


def test_warmup_and_partial_tail_do_not_bias_estimate():
    ch = channels(1600)
    ch["hr"] = [80 if t < 300 or t >= 1500 else 150 for t in ch["time"]]
    assert stream_estimate(ch, 190, 60) == pytest.approx(55.5)


def test_interval_clipping_and_sampling_do_not_change_estimate():
    for step in (1, 3, 7, 10):
        assert stream_estimate(channels(step=step), 190, 60) == pytest.approx(55.5)


def test_intervals_are_not_treated_as_regular_running():
    ch = channels()
    distance = 0
    ch["distance"] = [0]
    for t in ch["time"][1:]:
        distance += (1.5 if (t - 5) // 60 % 2 else 4) * 5
        ch["distance"].append(distance)
    assert stream_estimate(ch, 190, 60) is None


@pytest.mark.parametrize("case", ["missing_hr", "gaps", "short", "backwards", "invalid"])
def test_unusable_stream_is_not_filled(case):
    ch = channels()
    if case == "missing_hr":
        ch["hr"] = [None] * len(ch["time"])
    elif case == "gaps":
        ch = channels(step=15)
    elif case == "short":
        ch = channels(seconds=800)
    elif case == "backwards":
        ch["time"][10] = -1
    else:
        ch["distance"] = [math.inf] * len(ch["time"])
    assert stream_estimate(ch, 190, 60) is None


@pytest.mark.parametrize(
    "args",
    [
        (3, 150, 60, 60),
        (3, 150, 50, 60),
        (3, 200, 190, 60),
        (3, 70, 190, 60),
        (0, 150, 190, 60),
        (math.nan, 150, 190, 60),
    ],
)
def test_invalid_inputs_do_not_produce_estimates(args):
    assert hr_estimate(*args) is None
