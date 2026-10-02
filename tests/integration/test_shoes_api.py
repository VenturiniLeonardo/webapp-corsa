from sqlalchemy import select
from sqlalchemy.orm import Session
from test_file_import import _gpx, _post
from test_hae_import import client  # noqa: F401

from app.domain.models import Activity

H = {"X-Corsa": "1", "Content-Type": "application/json"}


def test_shoe_crud_default_and_mileage(client):  # noqa: F811
    c, e = client
    r = c.post("/api/shoes", json={"name": "Old", "initial_distance_m": 100000}, headers=H)
    assert r.status_code == 201, r.text
    old = r.json()["id"]
    new = c.post("/api/shoes", json={"name": "New", "is_default": True}, headers=H).json()["id"]
    c.patch(f"/api/shoes/{old}", json={"is_default": True}, headers=H)  # steals default
    c.patch(f"/api/shoes/{new}", json={"is_default": True}, headers=H)
    assert [x["is_default"] for x in c.get("/api/shoes").json()] == [True, False]

    _post(c, "a.gpx", _gpx())  # ingest picks the default shoe
    with Session(e) as s:
        a = s.scalars(select(Activity)).one()
        assert a.shoe_id == new
        aid, dist, mov = a.id, a.distance_m, a.moving_s
    sh = {x["id"]: x for x in c.get("/api/shoes").json()}
    assert sh[new]["run_count"] == 1 and sh[new]["total_distance_m"] == dist
    assert sh[new]["weighted_pace_s_per_km"] == mov / (dist / 1000)
    assert sh[old]["percent_worn"] == 100000 / 700000 * 100

    assert c.delete(f"/api/shoes/{new}", headers=H).status_code == 409  # linked
    r = c.patch(f"/api/activities/{aid}", json={"shoe_id": old}, headers=H)
    assert r.json()["shoe_id"] == old
    assert c.patch(f"/api/activities/{aid}", json={"shoe_id": 999}, headers=H).status_code == 422
    assert c.get("/api/activities", params={"shoe_id": old}).json()["total"] == 1
    assert c.get("/api/activities", params={"shoe_id": new}).json()["total"] == 0

    c.patch(f"/api/shoes/{new}", json={"retired_at": "2026-10-01"}, headers=H)
    assert not {x["id"]: x for x in c.get("/api/shoes").json()}[new]["is_default"]
    assert c.delete(f"/api/shoes/{new}", headers=H).status_code == 204
    assert c.delete(f"/api/shoes/{old}", headers=H).status_code == 409
    assert c.post("/api/shoes", json={"brand": "x"}, headers=H).status_code == 422
