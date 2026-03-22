"""Unit tests for controller database bootstrap initialization."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "services" / "controller"))
from app import database as db


@pytest.mark.unit
def test_init_db_runs_create_all_without_migration_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def _fake_create_all(*_args, **_kwargs):
        calls.append("create_all")

    monkeypatch.setattr(db.Base.metadata, "create_all", _fake_create_all)
    monkeypatch.setattr(db, "_migrations_applied", False)

    db.init_db()

    assert calls == ["create_all"]
    assert db._migrations_applied is False


@pytest.mark.unit
def test_init_db_is_idempotent_for_repeated_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def _fake_create_all(*_args, **_kwargs):
        calls.append("create_all")

    monkeypatch.setattr(db.Base.metadata, "create_all", _fake_create_all)
    monkeypatch.setattr(db, "_migrations_applied", False)

    db.init_db()
    db.init_db()

    assert calls == ["create_all", "create_all"]
    assert db._migrations_applied is False
