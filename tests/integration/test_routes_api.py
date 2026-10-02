from defusedxml import ElementTree
from test_hae_import import client  # noqa: F401

import app.api.routes as routes_api

H = {"X-Corsa": "1", "Content-Type": "application/json"}
# L-shape at 45°N: 500 m east (flat, 10 m), then 500 m north climbing to 30 m (4%)
E, N = 500 / (111195 * 0.7071), 500 / 111195
L = [[9.0, 45.0, 10.0], [9.0 + E, 45.0, 10.0], [9.0 + E, 45.0 + N, 30.0]]


def test_analyze_turns_grades_gap(client):  # noqa: F811
    c, _ = client
    r = c.post("/api/routes/analyze", json={"coords": L}, headers=H)
    assert r.status_code == 200, r.text
    a = r.json()
    assert abs(a["distance_m"] - 1000) < 5
    assert abs(a["elev_gain_m"] - 20) < 1 and a["elev_loss_m"] < 1
    assert (a["turns"], a["turns_sharp"], a["hairpins"]) == (1, 1, 0)
    assert abs(a["sinuosity"] - 2**0.5) < 0.02  # open route: length / straight line
    assert 450 < a["grade_bands_m"]["flat"] < 550 and 400 < a["grade_bands_m"]["gentle"] < 550
    assert a["gap_distance_m"] > a["distance_m"]  # climbing costs more than flat
    assert len(a["profile"]["lng"]) == len(a["profile"]["ele"]) == 51  # 20 m spacing


def test_hairpin_and_loop_sinuosity(client):  # noqa: F811
    c, _ = client
    back = [[9.0, 45.0 + 0.0002, 10.0], [9.0 + E, 45.0 + 0.0002, 10.0]]  # out-and-back, 22 m apart
    a = c.post("/api/routes/analyze", json={"coords": L[:2] + back[::-1]}, headers=H).json()
    assert a["hairpins"] == 1
    assert a["sinuosity"] is not None and a["sinuosity"] > 0


def test_elevation_filled_when_missing(client, monkeypatch):  # noqa: F811
    c, _ = client
    monkeypatch.setattr(
        routes_api, "fetch_elevations", lambda pts: [100.0 + i for i in range(len(pts))]
    )
    a = c.post("/api/routes/analyze", json={"coords": [p[:2] for p in L]}, headers=H).json()
    assert a["ele_min_m"] == 100 and a["ele_max_m"] == 150


def test_crud_and_gpx_export(client):  # noqa: F811
    c, _ = client
    r = c.post(
        "/api/routes", json={"name": "Giro <A&B>", "coords": L, "target_speed_ms": 3.0}, headers=H
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert c.post("/api/routes", json={"coords": L}, headers=H).status_code == 422
    full = c.get(f"/api/routes/{rid}").json()
    assert full["analysis"]["turns"] == 1 and len(full["coords"]) == 51

    x = c.get(f"/api/routes/{rid}/export-gpx")
    assert x.headers["content-type"].startswith("application/gpx+xml")
    root = ElementTree.fromstring(x.content)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    assert root.get("version") == "1.1" and root.find("g:trk/g:name", ns).text == "Giro <A&B>"
    pts = root.findall(".//g:trkpt", ns)
    assert len(pts) == 51 and pts[-1].find("g:ele", ns).text == "30.0"

    assert (
        c.patch(f"/api/routes/{rid}", json={"name": "Nuovo", "notes": "x"}, headers=H).json()[
            "name"
        ]
        == "Nuovo"
    )
    assert [x["name"] for x in c.get("/api/routes").json()] == ["Nuovo"]
    assert c.delete(f"/api/routes/{rid}", headers=H).status_code == 204
    assert c.get(f"/api/routes/{rid}").status_code == 404


def test_import_gpx_route_without_times(client):  # noqa: F811
    c, _ = client
    gpx = (
        '<gpx xmlns="http://www.topografix.com/GPX/1/1"><rte><name>Colline</name>'
        '<rtept lat="45.0" lon="9.0"><ele>5</ele></rtept><rtept lat="45.01" lon="9.0"/></rte></gpx>'
    )
    h = {"X-Corsa": "1", "Content-Type": "application/octet-stream", "X-Filename": "c.gpx"}
    r = c.post("/api/routes/import-file", content=gpx.encode(), headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "Colline", "coords": [[9.0, 45.0, 5.0], [9.0, 45.01, None]]}
    h["X-Filename"] = "c.txt"
    assert c.post("/api/routes/import-file", content=b"x", headers=h).status_code == 422
