from test_hae_import import client  # noqa: F401

H = {"X-Corsa": "1", "Content-Type": "application/json"}
ICS = r"""BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:Piano test
BEGIN:VTIMEZONE
TZID:Europe/Rome
BEGIN:STANDARD
DTSTART:19701025T030000
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:b
DTSTART;TZID=Europe/Rome:20261008T190000
DTEND;TZID=Europe/Rome:20261008T195000
SUMMARY:Easy\, Z2
DESCRIPTION:Riga 1\nRiga 2 piegata a 75 ottett
 i.
BEGIN:VALARM
DESCRIPTION:promemoria
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:a
DTSTART:20261006T170000Z
SUMMARY:Ripetute
END:VEVENT
BEGIN:VEVENT
UID:c
DTSTART;VALUE=DATE:20261011
SUMMARY:Gara
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")


def test_plan_upsert_parse_and_serve(client):  # noqa: F811
    c, _ = client
    r = c.post("/api/plans", json={"ics": ICS}, headers=H)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    [p] = c.get("/api/plans").json()
    assert p["name"] == "Piano test"
    a, b, g = p["events"]  # sorted by start
    assert (a["uid"], a["start"], a["end"]) == ("a", "2026-10-06T19:00", None)  # UTC -> Rome (CEST)
    assert (b["start"], b["end"], b["summary"]) == (
        "2026-10-08T19:00",
        "2026-10-08T19:50",
        "Easy, Z2",
    )
    assert b["description"] == "Riga 1\nRiga 2 piegata a 75 ottetti."  # unfolded; VALARM ignored
    assert g["start"] == "2026-10-11"  # all-day

    new = ICS.replace("SUMMARY:Ripetute", "SUMMARY:Ripetute 6x800")
    assert c.post("/api/plans", json={"ics": new}, headers=H).json() == {
        "id": pid,
        "created": False,
    }
    r = c.get(f"/api/plans/{pid}.ics")
    assert r.headers["content-type"] == "text/calendar; charset=utf-8"
    assert r.text == new  # served verbatim, same id
    assert c.get("/api/plans/999.ics").status_code == 404

    nameless = ICS.replace("X-WR-CALNAME:Piano test\r\n", "")
    r = c.post("/api/plans", json={"ics": nameless, "name": "Da file"}, headers=H)
    assert r.json()["created"]
    for bad in (
        "hello",
        nameless,
        "BEGIN:VCALENDAR\r\nX-WR-CALNAME:x\r\nEND:VCALENDAR",
        ICS.replace("20261008T190000", "2026-10-08"),
    ):
        assert c.post("/api/plans", json={"ics": bad}, headers=H).status_code == 422, bad
