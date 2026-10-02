import httpx
import respx
from test_file_import import _gpx, _post
from test_hae_import import client  # noqa: F401

from app.telegram import API, Bot

T = "123:abc"
B = f"{API}/bot{T}"


def test_report_endpoint(client):  # noqa: F811
    c, _ = client
    _post(c, "a.gpx", _gpx())
    r = c.get("/api/activities/1/report")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "# RUNNING ACTIVITY REPORT" in r.text and "Name: Lungo" in r.text
    assert "## KM SPLITS" in r.text and c.get("/api/activities/99/report").status_code == 404


@respx.mock
def test_bot_imports_file_and_replies_with_report(client):  # noqa: F811
    _, e = client
    respx.post(f"{B}/getFile").respond(200, json={"result": {"file_path": "docs/a.gpx"}})
    respx.get(f"{API}/file/bot{T}/docs/a.gpx").respond(200, content=_gpx())
    sent = respx.post(f"{B}/sendDocument").respond(200, json={"result": {}})
    said = respx.post(f"{B}/sendMessage").respond(200, json={"result": {}})
    bot = Bot(e, T, "42", httpx.Client())

    bot.handle({"chat": {"id": 7}, "text": "hi"})  # stranger: told its id, nothing imported
    assert b"id+7" in said.calls.last.request.content and not sent.called

    doc = {"file_id": "F", "file_name": "a.gpx", "file_size": 1000}
    bot.handle({"chat": {"id": 42}, "document": doc})
    body = sent.calls.last.request.content
    assert b"activity-1-report.txt" in body and b"Name: Lungo" in body
    bot.handle({"chat": {"id": 42}, "document": doc})  # same file again: report again
    assert sent.call_count == 2
