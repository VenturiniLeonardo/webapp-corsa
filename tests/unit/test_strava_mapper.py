from typing import Any

import pytest

from app.domain.stream_codec import decode_stream, encode_stream
from app.ingest.strava_mapper import MAPPER_VERSION, map_strava_activity

# Realistic Strava v3 payloads (DetailedActivity + key_by_type streams), shape as in M0-08.


def detail(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": 12345678901,
        "name": "Morning Run",
        "sport_type": "Run",
        "type": "Run",
        "start_date": "2024-06-30T22:30:00Z",
        "start_date_local": "2024-07-01T00:30:00Z",
        "timezone": "(GMT+01:00) Europe/Rome",
        "distance": 10012.4,
        "moving_time": 3001,
        "elapsed_time": 3120,
        "total_elevation_gain": 42.3,
        "average_heartrate": 151.2,
        "max_heartrate": 172.0,
        "has_heartrate": True,
        "average_cadence": 86.5,
        "average_watts": 251.0,
        "calories": 712.0,
        "trainer": False,
        "workout_type": 2,
        "start_latlng": [45.46, 9.19],
        "map": {"summary_polyline": "abc"},
        "laps": [
            {
                "lap_index": 1,
                "start_date": "2024-06-30T22:30:00Z",
                "elapsed_time": 300,
                "moving_time": 298,
                "distance": 1000.0,
                "average_speed": 3.36,
                "average_heartrate": 140.1,
                "max_heartrate": 150.0,
                "average_cadence": 85.0,
                "total_elevation_gain": 3.2,
            },
            {
                "lap_index": 2,
                "start_date": "2024-06-30T22:35:00Z",
                "elapsed_time": 290,
                "moving_time": 290,
                "distance": 1000.0,
                "average_speed": 3.45,
            },
        ],
    }
    d.update(over)
    return d


STREAMS = {
    "time": {"data": [0, 1, 2], "series_type": "distance", "original_size": 3},
    "distance": {"data": [0.0, 3.1, 6.3]},
    "latlng": {"data": [[45.46, 9.19], [45.4601, 9.1901], [45.4602, 9.1902]]},
    "altitude": {"data": [120.0, 120.2, 120.1]},
    "velocity_smooth": {"data": [0.0, 3.1, 3.2]},
    "heartrate": {"data": [120, 121, None]},
    "cadence": {"data": [84, 86, 87]},
    "watts": {"data": [240, 250, 260]},
    "moving": {"data": [False, True, True]},
}


def test_outdoor_run_full_mapping() -> None:
    act, laps, stream = map_strava_activity(detail(), STREAMS)
    assert act.status == "mapped"
    assert act.external_id == "12345678901"
    assert act.mapper_version == MAPPER_VERSION
    assert act.sport_type == "run"
    assert act.is_indoor is False
    assert act.start_time_utc == "2024-06-30T22:30:00+00:00"
    assert act.timezone == "Europe/Rome"
    assert act.local_date == "2024-07-01"  # UTC 22:30 + CEST(+2) crosses midnight
    assert (act.distance_m, act.elapsed_s, act.moving_s) == (10012.4, 3120, 3001)
    assert act.avg_cadence_spm == 173.0
    assert act.calories_kcal == 712
    assert act.workout_type == "long"
    assert act.has_gps and act.has_hr and act.has_cadence

    assert [lp.kind for lp in laps] == ["device_lap", "device_lap"]
    assert [lp.idx for lp in laps] == [0, 1]
    assert laps[0].avg_cadence_spm == 170.0
    assert laps[1].start_offset_s == 300
    assert laps[1].avg_cadence_spm is None

    assert stream is not None
    ch = stream.channels
    assert ch["cadence"] == [168, 172, 174]
    assert ch["lat"] == [45.46, 45.4601, 45.4602]
    assert ch["lng"] == [9.19, 9.1901, 9.1902]
    assert ch["speed"] == [0.0, 3.1, 3.2]
    assert ch["hr"] == [120, 121, None]
    assert ch["power"] == [240, 250, 260]
    assert {"time", "distance", "altitude", "moving"} <= ch.keys()
    assert not {"latlng", "velocity_smooth", "heartrate", "watts"} & ch.keys()
    assert stream.n_points == 3
    assert decode_stream(encode_stream(ch)) == ch


@pytest.mark.parametrize(
    ("over", "sport_type"),
    [
        ({"sport_type": "VirtualRun", "trainer": False}, "treadmill"),
        ({"sport_type": "Run", "trainer": True}, "treadmill"),
    ],
)
def test_indoor(over: dict[str, Any], sport_type: str) -> None:
    act, _, stream = map_strava_activity(
        detail(**over, start_latlng=[], average_cadence=None, laps=[]), None
    )
    assert act.is_indoor is True
    assert act.sport_type == sport_type
    assert act.has_gps is False
    assert act.has_cadence is False
    assert act.avg_cadence_spm is None
    assert stream is None


def test_trail_run() -> None:
    act, _, _ = map_strava_activity(detail(sport_type="TrailRun"), None)
    assert (act.sport_type, act.is_indoor) == ("trail_run", False)


@pytest.mark.parametrize(
    ("tz", "start", "iana", "local_date"),
    [
        ("(GMT+01:00) Europe/Rome", "2024-01-15T23:30:00Z", "Europe/Rome", "2024-01-16"),
        (
            "(GMT-08:00) America/Los_Angeles",
            "2024-03-10T05:00:00Z",
            "America/Los_Angeles",
            "2024-03-09",
        ),
        ("(GMT+00:00) UTC", "2024-05-01T06:00:00Z", "UTC", "2024-05-01"),
    ],
)
def test_utc_and_timezone(tz: str, start: str, iana: str, local_date: str) -> None:
    act, _, _ = map_strava_activity(detail(timezone=tz, start_date=start), None)
    assert act.timezone == iana
    assert act.start_time_utc == start.replace("Z", "+00:00")
    assert act.local_date == local_date


def test_unknown_timezone_falls_back_to_start_date_local() -> None:
    act, _, _ = map_strava_activity(detail(timezone="(GMT+01:00) Mars/Olympus"), None)
    assert act.local_date == "2024-07-01"


@pytest.mark.parametrize(
    ("wt", "expected"), [(None, "easy"), (0, "easy"), (1, "race"), (2, "long"), (3, "workout")]
)
def test_workout_type(wt: int | None, expected: str) -> None:
    act, _, _ = map_strava_activity(detail(workout_type=wt), None)
    assert act.workout_type == expected


@pytest.mark.parametrize("sport", ["Ride", "Walk", "Hike", "Swim", "WeightTraining"])
def test_non_run_skipped(sport: str) -> None:
    act, laps, stream = map_strava_activity(detail(sport_type=sport, type=sport), STREAMS)
    assert act.status == "skipped"
    assert act.external_id == "12345678901"
    assert act.source_start_time_utc == "2024-06-30T22:30:00+00:00"
    assert (laps, stream) == ([], None)
