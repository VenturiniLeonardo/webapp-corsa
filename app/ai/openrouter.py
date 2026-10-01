"""OpenRouter chat client: fixed model fallback chain, bounded retries, local quota (PLAN §26).

Fallback (next model) on: timeout, network error, 429/5xx after retries, other 4xx, provider
error body, empty or schema-invalid output. The chain stops early, without fallback, on a bad
API key (every model would fail the same way) or when the local quota is spent.
The API key is only ever put in the Authorization header: never logged, never in errors.
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.ai.prompts import Analysis, parse_analysis
from app.core.config import get_settings
from app.domain.models import Setting

log = logging.getLogger(__name__)
USAGE_KEY = (
    "ai_usage"  # settings row {"day": "YYYY-MM-DD" (UTC), "n": int}; not exposed by /api/settings
)
# ponytail: per-minute window is per process (the API is the only caller); persist it if that changes
_minute: deque[float] = deque()


class AiError(Exception):
    code = "unavailable"
    message = "AI analysis is temporarily unavailable. Try again later."


class NotConfigured(AiError):
    code = "not_configured"
    message = "AI is not configured (OPENROUTER_API_KEY is empty)."


class QuotaExceeded(AiError):
    code = "rate_limited"
    message = "AI request limit reached. Try again later."


class ProviderAuth(AiError):
    code = "provider_auth"
    message = "The AI provider rejected the API key."


class _Fallback(Exception):
    """This model is unusable for this request; try the next one."""


def usage_today(engine: Engine, now: datetime | None = None) -> int:
    day = (now or datetime.now(UTC)).date().isoformat()
    with Session(engine) as s:
        row = s.get(Setting, USAGE_KEY)
        return int(row.value["n"]) if row and row.value.get("day") == day else 0


class OpenRouter:
    def __init__(
        self,
        engine: Engine,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.cfg = get_settings()
        self.engine = engine
        self.http = http or httpx.Client()
        self.sleep = sleep
        self.now = now

    def analyse(self, messages: list[dict[str, str]]) -> tuple[str, Analysis]:
        """(model that answered, validated analysis). Raises AiError subclasses."""
        if not self.cfg.OPENROUTER_API_KEY:
            raise NotConfigured
        first = self.cfg.ai_models[0]
        for model in self.cfg.ai_models:
            try:
                out = self._model(model, messages)
            except _Fallback as e:
                log.warning("ai: model %s failed (%s), falling back", model, e)
                continue
            if model != first:
                log.info("ai: answered by fallback %s (primary %s)", model, first)
            return model, out
        log.error("ai: all models failed")
        raise AiError

    # --- one model -----------------------------------------------------------
    def _model(self, model: str, messages: list[dict[str, str]]) -> Analysis:
        cfg = self.cfg
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": cfg.AI_MAX_TOKENS,
            "temperature": cfg.AI_TEMPERATURE,
            # keep reasoning short and out of `content`; ignored by models without reasoning
            "reasoning": {"effort": "low", "exclude": True},
        }
        headers = {"Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}", "X-Title": "corsa"}
        for attempt in range(cfg.AI_MAX_RETRIES + 1):
            self._take_quota()
            t0 = time.monotonic()
            try:
                r = self.http.post(
                    f"{cfg.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=cfg.AI_TIMEOUT_S,
                )
            except httpx.TimeoutException:
                raise _Fallback("timeout") from None
            except httpx.TransportError as e:
                raise _Fallback(f"network: {type(e).__name__}") from None
            log.info("ai: %s -> %s in %.1fs", model, r.status_code, time.monotonic() - t0)
            if r.status_code == 401:
                raise ProviderAuth
            if r.status_code == 429 or r.status_code >= 500:
                wait = _retry_after(r) or cfg.AI_RETRY_BACKOFF_S * 2**attempt
                if attempt < cfg.AI_MAX_RETRIES and wait <= cfg.AI_MAX_WAIT_S:
                    self.sleep(wait)
                    continue
                raise _Fallback(f"http {r.status_code}")
            if r.is_error:
                raise _Fallback(f"http {r.status_code}")
            return _content(r)
        raise AssertionError("unreachable")

    def _take_quota(self) -> None:
        """Count one outbound request against the local minute/day budget, or refuse."""
        cfg, now = self.cfg, self.now()
        t = now.timestamp()
        while _minute and t - _minute[0] >= 60:
            _minute.popleft()
        if len(_minute) >= cfg.AI_MINUTE_LIMIT:
            raise QuotaExceeded
        day = now.date().isoformat()
        with Session(self.engine) as s, s.begin():
            row = s.get(Setting, USAGE_KEY)
            n = int(row.value["n"]) if row and row.value.get("day") == day else 0
            if n >= cfg.AI_DAILY_LIMIT:
                raise QuotaExceeded
            s.merge(Setting(key=USAGE_KEY, value={"day": day, "n": n + 1}))
        _minute.append(t)


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return float(r.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _content(r: httpx.Response) -> Analysis:
    try:
        body = r.json()
    except ValueError:
        raise _Fallback("non-JSON body") from None
    if not isinstance(body, dict) or body.get("error"):  # OpenRouter can send errors with 200
        raise _Fallback("provider error")
    try:
        content = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise _Fallback("malformed body") from None
    if not content.strip():
        raise _Fallback("empty response")
    try:
        return parse_analysis(content)
    except ValueError as e:
        raise _Fallback(f"invalid output: {type(e).__name__}") from None
