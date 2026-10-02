"""Telegram bot: send an activity file, get back the TXT report of every run it imported.

Long polling (the app is not reachable from the internet, so no webhook). Runs as a thread in the
worker. Only TELEGRAM_CHAT_ID may upload; any other chat is told its own id (setup aid).
"""

import contextlib
import logging
import threading
from typing import Any

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.api.ai import queue_auto_analysis
from app.api.report import build_report
from app.core.config import get_settings
from app.domain.models import SourceRecord, utcnow_iso
from app.ingest.files import Rejected, import_upload

log = logging.getLogger(__name__)
API = "https://api.telegram.org"
POLL_S = 50
MAX_DOWNLOAD = 20 * 1024 * 1024  # Bot API getFile limit; bigger files go through the web UI
HELP = "Mandami un file attività (.fit, .gpx, .tcx, .gz, .json o .zip): ti rispondo col report."


def _chunks(text: str, size: int = 4096) -> list[str]:
    """Split on line boundaries into Telegram-sized messages (sendMessage max 4096 chars)."""
    out = [""]
    for line in text.splitlines():
        while len(line) >= size:  # a single overlong line: hard cut
            out.append(line[:size])
            line = line[size:]
        if len(out[-1]) + len(line) + 1 > size:
            out.append("")
        out[-1] += line + "\n"
    return [c for c in out if c.strip()]


class Bot:
    def __init__(self, engine: Engine, token: str, chat_id: str, http: httpx.Client) -> None:
        self.engine, self.token, self.chat_id, self.http = engine, token, chat_id, http
        self.base = f"{API}/bot{token}"

    def _call(self, method: str, **kw: Any) -> Any:
        r = self.http.post(f"{self.base}/{method}", **kw)
        r.raise_for_status()
        return r.json()["result"]

    def say(self, chat: int, text: str) -> None:
        self._call("sendMessage", data={"chat_id": chat, "text": text})

    def handle(self, msg: dict[str, Any]) -> None:
        chat = msg["chat"]["id"]
        if str(chat) != self.chat_id:
            self.say(chat, f"Chat non autorizzata (id {chat}).")
            return
        doc = msg.get("document")
        if not doc:
            self.say(chat, HELP)
            return
        if (doc.get("file_size") or 0) > MAX_DOWNLOAD:
            self.say(chat, "File oltre 20 MB (limite Telegram): caricalo dall'interfaccia web.")
            return
        path = self._call("getFile", data={"file_id": doc["file_id"]})["file_path"]
        r = self.http.get(f"{API}/file/bot{self.token}/{path}")
        r.raise_for_status()
        # ponytail: "touched since t0" also picks up runs a concurrent intervals sync stores in
        # the same seconds; track ids through import_upload if that ever matters
        t0 = utcnow_iso()
        try:
            out = import_upload(self.engine, doc.get("file_name") or path, r.content)
        except Rejected as e:
            self.say(chat, f"File rifiutato: {e}")
            return
        queue_auto_analysis(self.engine)
        with Session(self.engine) as s:
            touched = s.scalars(
                select(SourceRecord.activity_id).where(SourceRecord.fetched_at >= t0)
            )
            ids = sorted({i for i in touched if i is not None})
            reports = [(aid, build_report(s, aid)) for aid in ids]
        failed = "".join(f"\n- {f['id']}: {f['error']}" for f in out["failed"][:10])
        if not reports or failed:
            self.say(chat, f"Nessuna corsa importata. {out}" if not reports else failed.strip())
        for _, text in reports:
            for part in _chunks(text):
                self.say(chat, part)

    def run(self, stop: threading.Event) -> None:
        offset = 0
        while not stop.is_set():
            try:
                updates = self._call(
                    "getUpdates",
                    data={"offset": offset, "timeout": POLL_S, "allowed_updates": '["message"]'},
                )
            except Exception as e:  # noqa: BLE001
                log.warning("telegram: poll failed: %s", str(e).replace(self.token, "***"))
                stop.wait(30)
                continue
            for u in updates:
                offset = u["update_id"] + 1  # never redeliver a message that crashes handle()
                if msg := u.get("message"):
                    try:
                        self.handle(msg)
                    except Exception as e:  # noqa: BLE001
                        err = f"{type(e).__name__}: {e}".replace(self.token, "***")
                        log.warning("telegram: %s", err)
                        with contextlib.suppress(Exception):
                            self.say(msg["chat"]["id"], f"Errore: {err[:300]}")


def start(engine: Engine, stop: threading.Event) -> None:
    """Background thread when TELEGRAM_BOT_TOKEN is set; no-op otherwise."""
    cfg = get_settings()
    if not cfg.TELEGRAM_BOT_TOKEN:
        return
    http = httpx.Client(timeout=POLL_S + 15)
    bot = Bot(engine, cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID, http)
    threading.Thread(target=bot.run, args=(stop,), daemon=True, name="telegram").start()
