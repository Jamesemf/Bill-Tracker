"""Shared pytest fixtures. All tests run against a throwaway temp DB — the real
bills.db is never touched (DB_PATH is monkeypatched before init_db)."""

import pytest
from fastapi.testclient import TestClient

import app.main
import app.models as models
from app.models import BILL_TYPES, CATEGORIES, db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point app.models.DB_PATH at a fresh temp DB and initialise the schema."""
    dbfile = tmp_path / "test.db"
    monkeypatch.setattr(models, "DB_PATH", dbfile)
    models.init_db()
    return dbfile


@pytest.fixture
def client(tmp_db):
    """TestClient bound to the temp DB (lifespan re-runs init_db harmlessly)."""
    with TestClient(app.main.app) as c:
        yield c


@pytest.fixture
def make_bill(tmp_db):
    """Factory inserting a bill directly and returning its new id."""

    def _make(**kwargs):
        fields = {
            "name": "Test Bill",
            "amount": 10.0,
            "due_day": 1,
            "frequency": "monthly",
            "category": CATEGORIES[0],
            "bill_type": BILL_TYPES[-1],
            "active": 1,
            "auto_pay": 0,
            "notes": None,
            "url": None,
            "due_month": None,
            # Fixed old date so tests evaluating past "todays" aren't affected
            # by the created-before-occurrence overdue guard.
            "created_at": "2020-01-01",
        }
        fields.update(kwargs)
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO bills "
                "(name, amount, due_day, frequency, category, bill_type, "
                " active, auto_pay, notes, url, due_month, created_at) "
                "VALUES (:name,:amount,:due_day,:frequency,:category,:bill_type,"
                ":active,:auto_pay,:notes,:url,:due_month,:created_at)",
                fields,
            )
            return cur.lastrowid

    return _make
