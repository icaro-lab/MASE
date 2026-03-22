from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from threading import RLock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings

RUN_ID_PATTERN = re.compile(r"[a-zA-Z0-9._-]+")
DEFAULT_RUN_ID = "default"

DATABASE_URL = settings.DATABASE_URL
IS_SQLITE = DATABASE_URL.startswith("sqlite:///")


def _sqlite_path_from_url(url: str) -> Path:
    raw = url.replace("sqlite:///", "", 1)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    return candidate


def _sqlite_url_from_path(path: Path) -> str:
    return f"sqlite:///{path}"


DEFAULT_DB_PATH = _sqlite_path_from_url(DATABASE_URL) if IS_SQLITE else None
RUN_DB_ROOT = Path("/app/data/runs")
BASELINE_DB_PATH = RUN_DB_ROOT / "_baseline.db"

Base = declarative_base()

_lock = RLock()
_engines: dict[str, object] = {}
_session_factories: dict[str, sessionmaker] = {}
_active_run_id = DEFAULT_RUN_ID


def _ensure_sqlite_parent(path: Path) -> None:
    os.makedirs(path.parent, exist_ok=True)


def _get_or_create_engine(db_url: str):
    engine = _engines.get(db_url)
    if engine is not None:
        return engine
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, connect_args=connect_args, echo=False)
    _engines[db_url] = engine
    _session_factories[db_url] = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine


def _get_or_create_session_factory(db_url: str) -> sessionmaker:
    factory = _session_factories.get(db_url)
    if factory is not None:
        return factory
    _get_or_create_engine(db_url)
    return _session_factories[db_url]


def _validate_run_id(run_id: str) -> str:
    text = (run_id or "").strip()
    if not text:
        raise ValueError("run_id is required")
    if not RUN_ID_PATTERN.fullmatch(text):
        raise ValueError("invalid run_id")
    return text


def _run_db_path(run_id: str) -> Path:
    return RUN_DB_ROOT / run_id / "moltbook.db"


def _run_db_url(run_id: str) -> str:
    if not IS_SQLITE:
        return DATABASE_URL
    if run_id == DEFAULT_RUN_ID:
        return DATABASE_URL
    return _sqlite_url_from_path(_run_db_path(run_id))


def _list_known_runs() -> list[str]:
    if not RUN_DB_ROOT.exists():
        return []
    names = [p.name for p in RUN_DB_ROOT.iterdir() if p.is_dir() and RUN_ID_PATTERN.fullmatch(p.name)]
    return sorted(names, reverse=True)


def _ensure_baseline_snapshot() -> None:
    if not IS_SQLITE or DEFAULT_DB_PATH is None:
        return
    RUN_DB_ROOT.mkdir(parents=True, exist_ok=True)
    if BASELINE_DB_PATH.exists():
        return
    _ensure_sqlite_parent(DEFAULT_DB_PATH)
    if not DEFAULT_DB_PATH.exists():
        DEFAULT_DB_PATH.touch()
    shutil.copy2(DEFAULT_DB_PATH, BASELINE_DB_PATH)


def init_db() -> None:
    if IS_SQLITE and DEFAULT_DB_PATH is not None:
        _ensure_sqlite_parent(DEFAULT_DB_PATH)
    _get_or_create_engine(DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    _ensure_baseline_snapshot()


def activate_run_context(run_id: str) -> dict:
    if not IS_SQLITE:
        raise RuntimeError("run isolation currently requires SQLite")
    validated = _validate_run_id(run_id)
    with _lock:
        _ensure_baseline_snapshot()
        run_db_path = _run_db_path(validated)
        run_db_path.parent.mkdir(parents=True, exist_ok=True)
        if not run_db_path.exists():
            shutil.copy2(BASELINE_DB_PATH, run_db_path)
        run_db_url = _sqlite_url_from_path(run_db_path)
        run_engine = _get_or_create_engine(run_db_url)
        Base.metadata.create_all(bind=run_engine)
        global _active_run_id
        _active_run_id = validated
        return run_context()


def deactivate_run_context() -> dict:
    with _lock:
        global _active_run_id
        _active_run_id = DEFAULT_RUN_ID
        return run_context()


def run_context() -> dict:
    with _lock:
        active = _active_run_id
    if IS_SQLITE and active != DEFAULT_RUN_ID:
        db_path = str(_run_db_path(active))
    else:
        db_path = str(DEFAULT_DB_PATH) if DEFAULT_DB_PATH else DATABASE_URL
    return {
        "active_run_id": active,
        "default_run_id": DEFAULT_RUN_ID,
        "known_runs": _list_known_runs(),
        "db_path": db_path,
        "baseline_db_path": str(BASELINE_DB_PATH) if IS_SQLITE else None,
    }


def get_db() -> Session:
    with _lock:
        db_url = _run_db_url(_active_run_id)
    session_factory = _get_or_create_session_factory(db_url)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


engine = _get_or_create_engine(DATABASE_URL)
SessionLocal = _get_or_create_session_factory(DATABASE_URL)
