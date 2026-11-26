"""Kira Agent hosted service: API, scheduler and persistent storage."""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("KIRA_DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ["KIRA_DATA_DIR"] = str(DATA_DIR)

import agent  # noqa: E402  (agent reads KIRA_DATA_DIR during import)

SETTINGS_PATH = DATA_DIR / "settings.json"
RESULTS_PATH = DATA_DIR / "results.json"
DB_PATH = DATA_DIR / "kira-agent.sqlite3"
DOCS_DIR = ROOT / "docs"
scan_lock = threading.Lock()
scheduler = BackgroundScheduler(timezone="UTC")


def read_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    temp.replace(path)


def ensure_data_files() -> None:
    # Seed the persistent volume once; never overwrite user data on restart.
    for target, source in (
        (SETTINGS_PATH, DOCS_DIR / "settings.json"),
        (RESULTS_PATH, DOCS_DIR / "results.json"),
    ):
        if not target.exists() and source.exists():
            target.write_bytes(source.read_bytes())


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with db_connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                message TEXT
            )"""
        )


def get_frequency() -> int:
    settings = read_json(SETTINGS_PATH, {})
    try:
        value = int(settings.get("frequency_minutes", 60))
    except (TypeError, ValueError):
        value = 60
    return max(15, min(value, 1440))


def schedule_interval() -> None:
    if scheduler.running:
        scheduler.add_job(
            run_scan,
            trigger=IntervalTrigger(minutes=get_frequency()),
            id="periodic_scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )


def run_scan() -> None:
    if not scan_lock.acquire(blocking=False):
        return
    started = datetime.now(timezone.utc).isoformat()
    run_id = None
    try:
        with db_connect() as db:
            cursor = db.execute(
                "INSERT INTO scan_runs(started_at,status) VALUES (?,?)",
                (started, "running"),
            )
            run_id = cursor.lastrowid
        os.environ["FORCE"] = "1"
        agent.main()
        result = read_json(RESULTS_PATH, {})
        errors = result.get("errors", []) if isinstance(result, dict) else []
        status = "completed_with_source_errors" if errors else "completed"
        message = "; ".join(str(error) for error in errors[:10]) or "Tarama tamamlandı."
        with db_connect() as db:
            db.execute(
                "UPDATE scan_runs SET finished_at=?,status=?,message=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), status, message, run_id),
            )
    except Exception as exc:  # persist failures so the dashboard can show them
        if run_id is not None:
            with db_connect() as db:
                db.execute(
                    "UPDATE scan_runs SET finished_at=?,status=?,message=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), "failed", str(exc)[:2000], run_id),
                )
    finally:
        os.environ.pop("FORCE", None)
        scan_lock.release()


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_data_files()
    init_db()
    scheduler.start()
    schedule_interval()
    # First scan shortly after boot; subsequent scans follow the saved interval.
    from apscheduler.triggers.date import DateTrigger
    from datetime import timedelta
    scheduler.add_job(
        run_scan,
        trigger=DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(seconds=10)),
        id="startup_scan",
        max_instances=1,
    )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Kira Agent", version="2.0.0", lifespan=lifespan)


def valid_basic_auth(header: str | None) -> bool:
    username = os.environ.get("KIRA_ADMIN_USER", "")
    password = os.environ.get("KIRA_ADMIN_PASSWORD", "")
    if not username or not password or not header:
        return False
    try:
        scheme, encoded = header.split(" ", 1)
        if scheme.lower() != "basic":
            return False
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        supplied_user, supplied_password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return False
    return hmac.compare_digest(supplied_user, username) and hmac.compare_digest(supplied_password, password)


@app.middleware("http")
async def require_dashboard_login(request, call_next):
    if request.url.path == "/api/health":
        return await call_next(request)
    if not valid_basic_auth(request.headers.get("authorization")):
        return PlainTextResponse(
            "Kimlik doğrulama gerekli. KIRA_ADMIN_USER ve KIRA_ADMIN_PASSWORD ayarlayın.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Kira Agent"'},
        )
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "kira-agent"}


@app.get("/api/results")
def results() -> dict[str, Any]:
    return read_json(RESULTS_PATH, {"items": [], "errors": [], "updated": None})


@app.get("/api/settings")
def settings() -> dict[str, Any]:
    return read_json(SETTINGS_PATH, {})


@app.put("/api/settings")
def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    frequency = payload.get("frequency_minutes", 60)
    try:
        frequency = int(frequency)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="frequency_minutes sayı olmalı") from exc
    if not 15 <= frequency <= 1440:
        raise HTTPException(status_code=422, detail="Tarama aralığı 15-1440 dakika olmalı")
    payload["frequency_minutes"] = frequency
    write_json(SETTINGS_PATH, payload)
    schedule_interval()
    return {"saved": True, "frequency_minutes": frequency}


@app.post("/api/scan", status_code=202)
def trigger_scan() -> dict[str, str]:
    if scan_lock.locked():
        raise HTTPException(status_code=409, detail="Tarama zaten çalışıyor")
    from apscheduler.triggers.date import DateTrigger
    scheduler.add_job(
        run_scan,
        trigger=DateTrigger(run_date=datetime.now(timezone.utc)),
        id="manual_scan",
        replace_existing=True,
        max_instances=1,
    )
    return {"queued": True}


@app.get("/api/status")
def status() -> dict[str, Any]:
    with db_connect() as db:
        row = db.execute(
            "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    job = scheduler.get_job("periodic_scan") if scheduler.running else None
    return {
        "scheduler_running": scheduler.running,
        "frequency_minutes": get_frequency(),
        "next_scan_at": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "scan_running": scan_lock.locked(),
        "last_run": dict(row) if row else None,
    }


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(DOCS_DIR / "index.html")


app.mount("/static", StaticFiles(directory=DOCS_DIR), name="static")
