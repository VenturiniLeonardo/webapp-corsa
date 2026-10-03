from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.types import Scope

from app.api.activities import router as activities_router
from app.api.ai import router as ai_router
from app.api.imports import router as imports_router
from app.api.plans import router as plans_router
from app.api.report import router as report_router
from app.api.routes import router as routes_router
from app.api.settings import router as settings_router
from app.api.shoes import router as shoes_router
from app.api.stats import router as stats_router
from app.api.sync import router as sync_router
from app.core.config import get_settings

Next = Callable[[Request], Awaitable[Response]]

AUTH_EXEMPT = {"/healthz"}
BINARY_UPLOAD = {
    "/api/imports/file",
    "/api/routes/import-file",
}  # raw file body; still needs X-Corsa
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
CSP = (
    "default-src 'self'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://tiles.openfreemap.org https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https://tiles.openfreemap.org; "
    "connect-src 'self' https://tiles.openfreemap.org; worker-src 'self' blob:"
)

app = FastAPI()


@app.middleware("http")
async def csrf(request: Request, call_next: Next) -> Response:
    if request.method in MUTATING:
        ct = request.headers.get("content-type", "").split(";")[0].strip().lower()
        ok_ct = ct == "application/json" or (
            request.url.path in BINARY_UPLOAD and ct == "application/octet-stream"
        )
        if not ok_ct or request.headers.get("x-corsa") != "1":
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def auth(request: Request, call_next: Next) -> Response:
    if request.url.path not in AUTH_EXEMPT:
        s = get_settings()
        login = request.headers.get("tailscale-user-login")
        if login is None and s.ENV == "dev":
            login = s.AUTH_DEV_LOGIN
        if login not in s.ALLOWED_LOGINS and not (s.ENV == "dev" and login == s.AUTH_DEV_LOGIN):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next: Next) -> Response:
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


app.include_router(activities_router)
app.include_router(stats_router)
app.include_router(sync_router)
app.include_router(settings_router)
app.include_router(ai_router)
app.include_router(imports_router)
app.include_router(report_router)
app.include_router(shoes_router)
app.include_router(routes_router)
app.include_router(plans_router)


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {"status": "ok", "db": True, "last_sync_age_s": None, "failed_jobs_24h": 0}


class SPA(StaticFiles):
    async def check_config(self) -> None:  # static dir may be absent in dev
        pass

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404 or scope["path"].startswith("/api/"):
                raise
            return await super().get_response("index.html", scope)


app.mount("/", SPA(directory=Path(__file__).parent / "static", html=True, check_dir=False))
