import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.activities import Db
from app.core.config import get_settings
from app.domain.models import ProviderAccount

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
PROVIDER = "strava"
SCOPE = "activity:read_all"
STATE_TTL_S = 600

router = APIRouter(prefix="/api/strava")
# ponytail: in-memory state store, lost on restart/multi-process; move to `settings` table in M2
_states: dict[str, float] = {}


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("strava_callback"))


@router.get("/connect")
def connect(request: Request) -> RedirectResponse:
    now = time.time()
    for s in [s for s, exp in _states.items() if exp < now]:
        del _states[s]
    state = secrets.token_urlsafe(32)
    _states[state] = now + STATE_TTL_S
    q = urlencode(
        {
            "client_id": get_settings().STRAVA_CLIENT_ID,
            "redirect_uri": _redirect_uri(request),
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": SCOPE,
            "state": state,
        }
    )
    return RedirectResponse(f"{AUTHORIZE_URL}?{q}")


@router.get("/callback")
def strava_callback(
    db: Db, state: str = "", code: str = "", error: str = "", scope: str = ""
) -> RedirectResponse:
    exp = _states.pop(state, 0.0)  # single use
    if exp < time.time():
        raise HTTPException(400, "invalid or expired state")
    if error or not code:
        raise HTTPException(400, f"authorization denied: {error or 'missing code'}")
    s = get_settings()
    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": s.STRAVA_CLIENT_ID,
            "client_secret": s.STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise HTTPException(502, "token exchange failed")
    tok = r.json()
    granted = {x for x in scope.split(",") if x}
    if SCOPE not in granted:
        raise HTTPException(400, f"scope {SCOPE} not granted")
    acc = db.get(ProviderAccount, PROVIDER) or ProviderAccount(provider=PROVIDER)
    acc.athlete_id = str((tok.get("athlete") or {}).get("id") or acc.athlete_id or "")
    acc.access_token, acc.refresh_token = tok["access_token"], tok["refresh_token"]
    acc.expires_at, acc.scopes, acc.status = (
        int(tok["expires_at"]),
        ",".join(sorted(granted)),
        "active",
    )
    db.add(acc)
    db.commit()
    return RedirectResponse("/sync", status_code=302)
