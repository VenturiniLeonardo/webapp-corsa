import json
import logging
from datetime import date

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import openrouter, prompts
from app.ai.openrouter import AiError, NotConfigured, OpenRouter, ProviderAuth, QuotaExceeded
from app.api import ai as ai_api
from app.api import stats
from app.core.config import get_settings
from app.core.db import get_session, make_engine
from app.domain.models import Activity, ActivityMetrics, AiAnalysis, Base

URL = "https://openrouter.ai/api/v1/chat/completions"
KEY = "sk-or-SECRET-do-not-leak-123"
VALID = {
    "summary": "Steady week.",
    "insights": [{"kind": "observation", "text": "4 runs, 32 km.", "evidence": "runs=4"}],
    "caveats": [],
    "data_sufficiency": "sufficient",
}
MSGS = [{"role": "user", "content": "x"}]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {
        "ALLOWED_LOGINS": "me",
        "DATABASE_URL": "sqlite://",
        "ENV": "dev",
        "AUTH_DEV_LOGIN": "me",
        "OPENROUTER_API_KEY": KEY,
        "AI_RETRY_BACKOFF_S": "0.01",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(stats, "_today", lambda: date(2026, 9, 30))
    openrouter._minute.clear()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def models():
    return get_settings().ai_models


@pytest.fixture
def engine(tmp_path):
    e = make_engine(f"sqlite:///{tmp_path / 'ai.db'}")
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def client(engine):
    def override():
        with Session(engine) as s:
            yield s

    from app.main import app

    app.dependency_overrides[get_session] = override
    yield TestClient(app, headers={"X-Corsa": "1"})
    app.dependency_overrides.clear()


def reply(content: str | None = None, status: int = 200, **kw) -> httpx.Response:
    if content is None:
        content = json.dumps(VALID)
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}]}, **kw)


def router(plan: dict[str, list[httpx.Response | Exception]]):
    """respx side effect: per-model queue of responses; records the model of each call."""
    calls: list[str] = []

    def fx(request: httpx.Request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        r = plan[model].pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    return fx, calls


def run(engine, plan):
    fx, calls = router(plan)
    with respx.mock:
        respx.post(URL).mock(side_effect=fx)
        sleeps: list[float] = []
        out = OpenRouter(engine, sleep=sleeps.append).analyse(MSGS)
    return out, calls, sleeps


# --- client --------------------------------------------------------------------------
def test_missing_key(engine, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    with respx.mock(assert_all_called=False) as m:
        route = m.post(URL)
        with pytest.raises(NotConfigured):
            OpenRouter(engine).analyse(MSGS)
        assert not route.called


def test_valid_response_primary(engine, models):
    fx, calls = router({models[0]: [reply()]})
    with respx.mock:
        route = respx.post(URL).mock(side_effect=fx)
        model, a = OpenRouter(engine).analyse(MSGS)
    req = route.calls[0].request
    body = json.loads(req.content)
    assert model == models[0] and calls == [models[0]]
    assert a.insights[0].kind == "observation"
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    assert body["messages"] == MSGS and body["max_tokens"] > 0 and "temperature" in body


def test_model_order_is_fixed(models):
    assert models == [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "qwen/qwen3.8-27b:free",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        reply(""),  # empty
        reply("I think you ran well."),  # no JSON
        reply('{"summary": "x", "insights": [], "data_sufficiency": "sufficient"}'),  # schema
        httpx.Response(200, json={"error": {"code": 502, "message": "upstream"}}),  # provider
        httpx.Response(200, text="<html>"),  # non-JSON body
        httpx.Response(404, json={"error": {"message": "no such model"}}),  # other 4xx
        httpx.ReadTimeout("slow"),  # timeout
        httpx.ConnectError("down"),  # network
    ],
)
def test_ultra_falls_back_to_super(engine, models, bad):
    (model, _), calls, _ = run(engine, {models[0]: [bad], models[1]: [reply()]})
    assert model == models[1] and calls == models[:2]


def test_super_falls_back_to_qwen(engine, models):
    (model, _), calls, _ = run(
        engine, {models[0]: [reply("")], models[1]: [httpx.ReadTimeout("t")], models[2]: [reply()]}
    )
    assert model == models[2] and calls == models


def test_all_models_fail(engine, models, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(AiError) as ei:
        run(engine, {m: [reply("nope")] for m in models})
    assert ei.value.code == "unavailable"
    assert caplog.text.count("falling back") == 3


def test_429_retries_with_backoff_then_falls_back(engine, models):
    r429 = httpx.Response(429, json={"error": {"message": "rate"}})
    (model, _), calls, sleeps = run(
        engine, {models[0]: [r429, httpx.Response(429)], models[1]: [reply()]}
    )
    assert calls == [models[0], models[0], models[1]] and model == models[1]
    assert sleeps == [0.01]  # AI_MAX_RETRIES=1: one bounded wait, not a loop


def test_429_retry_succeeds_on_same_model(engine, models):
    (model, _), calls, _ = run(engine, {models[0]: [httpx.Response(503), reply()]})
    assert model == models[0] and calls == [models[0]] * 2


def test_long_retry_after_skips_waiting(engine, models):
    r = httpx.Response(429, headers={"Retry-After": "3600"})
    _, calls, sleeps = run(engine, {models[0]: [r], models[1]: [reply()]})
    assert sleeps == [] and calls == models[:2]


def test_bad_key_stops_chain(engine, models):
    with pytest.raises(ProviderAuth):
        run(engine, {models[0]: [httpx.Response(401)]})


def test_daily_quota_persists_and_blocks(engine, models, monkeypatch):
    monkeypatch.setenv("AI_DAILY_LIMIT", "2")
    get_settings.cache_clear()
    run(engine, {models[0]: [reply()]})
    run(engine, {models[0]: [reply()]})
    openrouter._minute.clear()  # a new process: only the persisted day counter remains
    with respx.mock(assert_all_called=False) as m:
        route = m.post(URL)
        with pytest.raises(QuotaExceeded):
            OpenRouter(engine).analyse(MSGS)
        assert not route.called
    assert openrouter.usage_today(engine) == 2


def test_minute_quota(engine, models, monkeypatch):
    monkeypatch.setenv("AI_MINUTE_LIMIT", "1")
    get_settings.cache_clear()
    run(engine, {models[0]: [reply()]})
    with pytest.raises(QuotaExceeded):
        run(engine, {})


def test_quota_counts_every_attempt(engine, models):
    run(engine, {models[0]: [httpx.Response(503), httpx.Response(503)], models[1]: [reply()]})
    assert openrouter.usage_today(engine) == 3


def test_key_never_leaks(engine, models, caplog):
    caplog.set_level(logging.DEBUG)
    plan = {m: [httpx.Response(500, text=KEY), httpx.ReadTimeout(KEY)] for m in models}
    with pytest.raises(AiError) as ei:
        run(engine, plan)
    assert KEY not in caplog.text and KEY not in str(ei.value) and KEY not in ei.value.message
    with pytest.raises(ProviderAuth) as ei2:
        run(engine, {models[0]: [httpx.Response(401, text=KEY)]})
    assert KEY not in str(ei2.value)
    assert KEY not in json.dumps(prompts.build_messages("activity", {"a": 1}))


# --- parsing -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(VALID),
        f"<think>let me see {{</think>{json.dumps(VALID)}",
        f"```json\n{json.dumps(VALID)}\n```",
        f"Here is the analysis:\n{json.dumps(VALID)}\nHope it helps.",
        json.dumps(VALID | {"extra": 1}),
    ],
)
def test_parse_recovers(raw):
    assert prompts.parse_analysis(raw).summary == "Steady week."


@pytest.mark.parametrize(
    "obj",
    [
        VALID | {"insights": []},
        VALID | {"insights": VALID["insights"] * 7},
        VALID | {"data_sufficiency": "great"},
        VALID | {"insights": [{"kind": "prediction", "text": "sub-20 5K next month"}]},
        {k: v for k, v in VALID.items() if k != "summary"},
    ],
)
def test_parse_rejects(obj):
    with pytest.raises(ValueError):
        prompts.parse_analysis(json.dumps(obj))


# --- API: cache, sufficiency, anomalies ------------------------------------------------
def add_run(s, day: str, dist=8000.0, mov=2700, hr=150.0, **kw) -> Activity:
    a = Activity(
        sport_type="run",
        name="Run near home",
        notes="private note",
        start_time_utc=f"{day}T07:00:00+00:00",
        timezone="Europe/Rome",
        local_date=day,
        distance_m=dist,
        moving_s=mov,
        elapsed_s=mov,
        avg_hr=hr,
        is_indoor=False,
        summary_polyline="secret_polyline",
        **kw,
    )
    s.add(a)
    s.flush()
    return a


class Sent(list):
    """Requests seen by the fake OpenRouter (respx resets its own stats on context exit)."""

    @property
    def call_count(self) -> int:
        return len(self)

    @property
    def called(self) -> bool:
        return bool(self)


def mocked(models, responses=None):
    m, sent = respx.mock(assert_all_called=False), Sent()

    def fx(request):
        sent.append(request)
        return responses.pop(0) if responses else reply()

    m.post(URL).mock(side_effect=fx)
    return m, sent


def test_api_cache_and_invalidation(client, engine, models):
    with Session(engine) as s:
        aid = add_run(s, "2026-09-20").id
        s.commit()
    assert client.get(f"/api/ai/activity/{aid}").json()["result"] is None
    m, route = mocked(models)
    with m:
        r1 = client.post(f"/api/ai/activity/{aid}", json={})
        r2 = client.post(f"/api/ai/activity/{aid}", json={})  # same data: served from cache
        g = client.get(f"/api/ai/activity/{aid}").json()
    assert r1.status_code == r2.status_code == 200 and route.call_count == 1
    assert g["result"]["model"] == models[0] and g["stale"] is False and g["used_today"] == 1
    sent = route[0].content.decode()
    for private in ("Run near home", "private note", "secret_polyline", KEY):
        assert private not in sent

    with Session(engine) as s:  # data changed -> stale, and a new request is made
        s.get(Activity, aid).avg_hr = 160.0
        s.commit()
    assert client.get(f"/api/ai/activity/{aid}").json()["stale"] is True
    with m:
        client.post(f"/api/ai/activity/{aid}", json={})
    assert route.call_count == 2
    assert client.get(f"/api/ai/activity/{aid}").json()["stale"] is False


def test_prompt_version_invalidates(client, engine, models, monkeypatch):
    with Session(engine) as s:
        aid = add_run(s, "2026-09-20").id
        s.commit()
    m, _ = mocked(models)
    with m:
        client.post(f"/api/ai/activity/{aid}", json={})
    monkeypatch.setattr(ai_api, "PROMPT_VERSION", prompts.PROMPT_VERSION + 1)
    assert client.get(f"/api/ai/activity/{aid}").json()["stale"] is True


def test_api_insufficient_period_makes_no_request(client, engine, models):
    with Session(engine) as s:
        add_run(s, "2026-09-20")
        s.commit()
    m, route = mocked(models)
    with m:
        r = client.post("/api/ai/period", json={"from_date": "2026-09-01"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "insufficient_data"
    assert not route.called
    assert client.get("/api/ai/period?from_date=2026-09-01").json()["insufficient"]


def test_api_anomalous_activity(client, engine, models):
    with Session(engine) as s:
        zero = add_run(s, "2026-09-20", dist=0.0).id
        odd = add_run(s, "2026-09-21", hr=None, mov=60).id
        s.add(ActivityMetrics(activity_id=odd, algo_version=1, gps_suspect=True))
        s.commit()
    m, route = mocked(models)
    with m:
        assert client.post(f"/api/ai/activity/{zero}", json={}).status_code == 422
        assert client.post(f"/api/ai/activity/{odd}", json={}).status_code == 200
    ctx = json.loads(json.loads(route[0].content)["messages"][1]["content"].split("DATA:\n")[1])
    assert ctx["metrics"]["gps_suspect"] is True and "avg_hr_bpm" not in ctx


def test_api_period_ok_and_errors(client, engine, models, monkeypatch):
    with Session(engine) as s:
        for d in ("2026-09-02", "2026-09-09", "2026-09-16", "2026-09-23"):
            add_run(s, d)
        s.commit()
    m, route = mocked(models)
    with m:
        r = client.post("/api/ai/period", json={"from_date": "2026-09-01", "to_date": "2026-09-30"})
    assert r.status_code == 200 and r.json()["result"]["analysis"]["summary"]
    ctx = route[0].content.decode()
    assert "2026-09-01" in ctx and "Run near home" not in ctx

    m, route = mocked(models, [httpx.Response(500)] * 6)  # every model down -> controlled 502
    with Session(engine) as s:
        add_run(s, "2026-09-24")
        s.commit()
    with m:
        r = client.post("/api/ai/period", json={"from_date": "2026-09-01", "to_date": "2026-09-30"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "unavailable"
    assert KEY not in r.text

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    r = client.post("/api/ai/period", json={"from_date": "2026-09-01", "to_date": "2026-09-29"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "not_configured"
    assert client.get("/api/ai/period").json()["enabled"] is False


def test_post_requires_csrf_header(client):
    r = client.post("/api/ai/period", json={}, headers={"X-Corsa": ""})
    assert r.status_code == 403


def test_auto_sweep_analyses_only_new_recent_runs(engine, models):
    from app.worker.queue import JobQueue

    with Session(engine) as s:
        new = add_run(s, "2026-09-29").id
        add_run(s, "2026-08-01")  # too old: no backfill spend
        add_run(s, "2026-09-28", dist=0.0)  # insufficient: never asked
        s.commit()
    ai_api.queue_auto_analysis(engine)
    ai_api.queue_auto_analysis(engine)  # deduped
    job = JobQueue(engine).claim_job()
    assert job and job.kind == "ai_sweep" and JobQueue(engine).claim_job() is None
    m, sent = mocked(models)
    with m:
        assert ai_api.run_sweep(engine) is None
        assert ai_api.run_sweep(engine) is None  # already analysed: no second request
    assert len(sent) == 1
    with Session(engine) as s:
        assert s.scalar(select(AiAnalysis.subject)) == str(new)
        assert ai_api.pending_activity_ids(s) == []


def test_auto_sweep_stops_on_provider_error(engine, models):
    with Session(engine) as s:
        add_run(s, "2026-09-29")
        add_run(s, "2026-09-30")
        s.commit()
    with respx.mock:
        route = respx.post(URL).mock(return_value=httpx.Response(401))
        assert "provider_auth" in (ai_api.run_sweep(engine) or "")
    assert route.call_count == 1  # no hammering the rest of the batch


def test_force_regenerates_same_input(client, engine, models):
    with Session(engine) as s:
        aid = add_run(s, "2026-09-29").id
        s.commit()
    m, sent = mocked(models)
    with m:
        client.post(f"/api/ai/activity/{aid}", json={})
        client.post(f"/api/ai/activity/{aid}", json={})  # cached
        assert len(sent) == 1
        assert client.post(f"/api/ai/activity/{aid}?force=true", json={}).status_code == 200
    assert len(sent) == 2
