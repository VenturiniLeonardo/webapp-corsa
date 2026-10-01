import gzip
import io
import json
import zipfile

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_hae_import import _workout, client  # noqa: F401

from app.domain.models import Activity, SourceRecord, Stream
from app.domain.stream_codec import decode_stream

H = {"X-Corsa": "1", "Content-Type": "application/octet-stream"}


def _gpx(sport="running", n=60, lat0=45.0):
    pts = "".join(
        f'<trkpt lat="{lat0 + i * 3e-5}" lon="12.0"><ele>{10 + i * 0.5}</ele>'
        f"<time>2026-09-20T07:00:{i:02d}Z</time><extensions><gpxtpx:TrackPointExtension>"
        f"<gpxtpx:hr>{140 + i % 5}</gpxtpx:hr><gpxtpx:cad>85</gpxtpx:cad>"
        "</gpxtpx:TrackPointExtension></extensions></trkpt>"
        for i in range(n)
    )
    return (
        '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
        '<wpt lat="1" lon="1"><name>FINISH</name><type>Waypoint</type></wpt>'
        f"<trk><name>Lungo</name><type>{sport}</type><trkseg>{pts}</trkseg></trk></gpx>"
    ).encode()


def _tcx():
    pts = "".join(
        f"<Trackpoint><Time>2026-09-21T07:00:{i:02d}Z</Time><DistanceMeters>{i * 3.0}"
        f"</DistanceMeters><HeartRateBpm><Value>150</Value></HeartRateBpm></Trackpoint>"
        for i in range(30)
    )
    return (
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">'
        f'<Activities><Activity Sport="Running"><Lap><Track>{pts}</Track></Lap></Activity>'
        "</Activities></TrainingCenterDatabase>"
    ).encode()


def _post(c, name, data):
    return c.post("/api/imports/file", content=data, headers=H | {"X-Filename": name})


def test_gpx_mapped_and_idempotent(client):  # noqa: F811
    c, e = client
    assert _post(c, "a.gpx", _gpx()).json() == {
        "mapped": 1, "skipped": 0, "duplicate": 0, "failed": []
    }  # fmt: skip
    assert _post(c, "a.gpx", _gpx()).json()["mapped"] == 1  # same sha256 -> same record
    with Session(e) as s:
        a = s.scalars(select(Activity)).one()
        assert (a.name, a.sport_type, a.elapsed_s, a.has_gps) == ("Lungo", "run", 59, True)
        assert a.avg_cadence_spm == 170 and a.max_hr == 144 and 190 < a.distance_m < 200
        ch = decode_stream(s.scalars(select(Stream)).one().data)
        assert {"time", "distance", "speed", "lat", "lng", "altitude", "hr", "cadence"} <= set(ch)


def test_tcx_gz_no_gps_is_treadmill(client):  # noqa: F811
    c, e = client
    assert _post(c, "t.tcx.gz", gzip.compress(_tcx())).json()["mapped"] == 1
    with Session(e) as s:
        a = s.scalars(select(Activity)).one()
        assert (a.sport_type, a.distance_m, a.avg_hr) == ("treadmill", 87.0, 150)
        assert s.scalars(select(SourceRecord.source)).one() == "file_tcx"


def test_strava_bulk_zip(client):  # noqa: F811
    c, e = client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "activities.csv",
            "Activity ID,Activity Name,Activity Type,Filename\n"
            "1,Mattino,Run,activities/1.gpx.gz\n2,Bici,Ride,activities/2.gpx\n",
        )
        z.writestr("activities/1.gpx.gz", gzip.compress(_gpx("9")))
        z.writestr("activities/2.gpx", _gpx("cycling", lat0=46.0))
        z.writestr("activities/3.tcx", b"<not xml")
    r = _post(c, "export_123.zip", buf.getvalue()).json()
    assert (r["mapped"], r["skipped"], r["failed"][0]["id"]) == (1, 1, "activities/3.tcx")
    with Session(e) as s:
        assert s.scalars(select(Activity.name)).one() == "Mattino"  # csv name wins


def test_hae_json_and_zip(client):  # noqa: F811
    c, e = client
    body = json.dumps({"data": {"workouts": [_workout()]}}).encode()
    assert _post(c, "hae.json", body).json()["mapped"] == 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("HealthAutoExport.json", body)
    assert _post(c, "hae.zip", buf.getvalue()).json()["mapped"] == 1
    with Session(e) as s:
        assert s.scalar(select(func.count()).select_from(Activity)) == 1


def test_rejections(client):  # noqa: F811
    c, _ = client
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><gpx>&e;</gpx>'
    )
    assert _post(c, "x.gpx", xxe).status_code == 422
    assert _post(c, "x.fit", b"0" * 20).json()["detail"] == "not a FIT file"
    assert _post(c, "x.exe", b"MZ").status_code == 422
    assert _post(c, "x.zip", b"PK nope").status_code == 422
    bomb = gzip.compress(b"\0" * (51 * 1024 * 1024))
    assert "too large" in _post(c, "x.gpx.gz", bomb).json()["detail"]
    assert c.post("/api/imports/file", content=b"x", headers={"X-Corsa": "1"}).status_code == 403
    from app.ingest.files import _hold

    assert _hold([None, 100, None, None, 110, None], [0, 1, 2, 30, 31, 32]) == [
        None,
        100,
        100,
        None,
        110,
        110,
    ]


def test_despike_removes_isolated_speed_glitches():
    from app.ingest.files import _despike

    v = [2.0, 2.0, 0.0, 2.0, 3.9, 2.0, 2.0]
    assert _despike(v) == [2.0] * 7
