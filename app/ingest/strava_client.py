"""Strava API client: self rate-limiting + atomic refresh-token rotation (PLAN §8.5, §9)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.strava_auth import PROVIDER, TOKEN_URL
from app.core.config import get_settings
from app.domain.models import ProviderAccount

REVOKE_URL = "https://www.strava.com/oauth/revoke"
LIMIT_15M = 90
LIMIT_DAY = 900
REFRESH_MARGIN_S = 300


class RateLimitWaitException(Exception):
    def __init__(self, resume_at: datetime) -> None:
        super().__init__(f"rate limited until {resume_at.isoformat()}")
        self.resume_at = resume_at


class StravaAuthError(Exception):
    """Token revoked / refresh rejected: account needs re-authorization."""


def next_quarter(now: datetime) -> datetime:
    floor = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    return floor + timedelta(minutes=15)


def next_midnight(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _pair(v: str | None) -> tuple[int, int] | None:
    try:
        a, b = (v or "").split(",")[:2]
        return int(a), int(b)
    except ValueError:
        return None


class StravaClient:
    def __init__(
        self,
        engine: Engine,
        http: httpx.Client | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        s = get_settings()
        self.engine = engine
        self.base = s.STRAVA_API_BASE.rstrip("/")
        self.client_id = s.STRAVA_CLIENT_ID
        self.client_secret = s.STRAVA_CLIENT_SECRET
        self.http = http or httpx.Client(timeout=30)
        self.now = now
        # ponytail: in-memory counters, reset on restart; the first response's headers resync them.
        self._quarter: datetime | None = None
        self._day: datetime | None = None
        self.used_15m = 0
        self.used_day = 0
        self.limit_15m = LIMIT_15M
        self.limit_day = LIMIT_DAY

    # --- rate limiting -------------------------------------------------
    def _roll(self, now: datetime) -> None:
        q, d = next_quarter(now), next_midnight(now)
        if q != self._quarter:
            self._quarter, self.used_15m = q, 0
        if d != self._day:
            self._day, self.used_day = d, 0

    def _check_budget(self, now: datetime) -> None:
        self._roll(now)
        if self.used_day >= self.limit_day:
            raise RateLimitWaitException(next_midnight(now))
        if self.used_15m >= self.limit_15m:
            raise RateLimitWaitException(next_quarter(now))

    def _absorb_headers(self, h: httpx.Headers) -> None:
        # Read-specific headers when present, else the overall ones.
        usage = _pair(h.get("X-ReadRateLimit-Usage")) or _pair(h.get("X-RateLimit-Usage"))
        limit = _pair(h.get("X-ReadRateLimit-Limit")) or _pair(h.get("X-RateLimit-Limit"))
        if usage:
            self.used_15m = max(self.used_15m, usage[0])
            self.used_day = max(self.used_day, usage[1])
        if limit:
            self.limit_15m = min(LIMIT_15M, limit[0])
            self.limit_day = min(LIMIT_DAY, limit[1])

    # --- tokens --------------------------------------------------------
    def _access_token(self) -> str:
        with Session(self.engine) as s:
            acc = s.get(ProviderAccount, PROVIDER)
            if acc is None or not acc.refresh_token:
                raise StravaAuthError("strava not connected")
            if (
                acc.access_token
                and (acc.expires_at or 0) - self.now().timestamp() > REFRESH_MARGIN_S
            ):
                return acc.access_token
            old_refresh = acc.refresh_token
        r = self.http.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": old_refresh,
            },
        )
        if r.status_code in (400, 401):
            self._mark_reauth()
            raise StravaAuthError(f"refresh rejected: {r.status_code}")
        r.raise_for_status()
        tok = r.json()
        # The old refresh token is dead now: persist both tokens in one transaction, immediately.
        with Session(self.engine) as s, s.begin():
            acc = s.get(ProviderAccount, PROVIDER)
            assert acc is not None
            acc.access_token = tok["access_token"]
            acc.refresh_token = tok["refresh_token"]
            acc.expires_at = int(tok["expires_at"])
        return str(tok["access_token"])

    def _mark_reauth(self) -> None:
        with Session(self.engine) as s, s.begin():
            if acc := s.get(ProviderAccount, PROVIDER):
                acc.status = "reauth_required"

    # --- core request --------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        now = self.now()
        self._check_budget(now)
        token = self._access_token()
        self.used_15m += 1
        self.used_day += 1
        r = self.http.get(
            f"{self.base}{path}", params=params, headers={"Authorization": f"Bearer {token}"}
        )
        self._absorb_headers(r.headers)
        if r.status_code == 429:
            daily_out = self.used_day >= self.limit_day
            raise RateLimitWaitException(next_midnight(now) if daily_out else next_quarter(now))
        if r.status_code == 401:
            self._mark_reauth()
            raise StravaAuthError("access token rejected")
        if r.is_error:  # keep Strava's error body: a bare 403 is undiagnosable
            raise httpx.HTTPStatusError(
                f"{r.status_code} {r.text[:300]}", request=r.request, response=r
            )
        return r.json()

    # --- API -----------------------------------------------------------
    def get_athlete_activities(
        self,
        after: int | None = None,
        before: int | None = None,
        page: int = 1,
        per_page: int = 200,
    ) -> list[dict[str, Any]]:
        params = {"after": after, "before": before, "page": page, "per_page": per_page}
        return list(
            self._get("/athlete/activities", {k: v for k, v in params.items() if v is not None})
        )

    def get_activity_detail(self, activity_id: int) -> dict[str, Any]:
        return dict(self._get(f"/activities/{activity_id}"))

    def get_activity_streams(self, activity_id: int, keys: list[str]) -> dict[str, Any]:
        return dict(
            self._get(
                f"/activities/{activity_id}/streams",
                {"keys": ",".join(keys), "key_by_type": "true"},
            )
        )

    def revoke_token(self, token: str) -> bool:
        # [NON VERIFICATO] body shape of /oauth/revoke (RFC 7009 style); confirm against docs.
        r = self.http.post(
            REVOKE_URL,
            data={"token": token, "client_id": self.client_id, "client_secret": self.client_secret},
        )
        return r.status_code == 200
