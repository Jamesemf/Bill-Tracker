import sqlite3
from datetime import date

import pytest

import app.models as models
from app.models import (
    annual_total,
    category_monthly_totals,
    init_db,
    monthly_total,
    next_due_date,
    overdue_bill_ids,
    paid_period_start,
    previous_due_date,
)


def row(**kwargs):
    base = {"frequency": "monthly", "due_day": 1, "due_month": None}
    base.update(kwargs)
    return base


# --- next_due_date --------------------------------------------------------

def test_next_due_weekly():
    r = row(frequency="weekly", due_day=2)  # Wednesday
    today = date(2026, 3, 15)
    nd = next_due_date(r, today)
    assert nd.weekday() == 2
    assert nd >= today
    assert 0 <= (nd - today).days < 7


def test_next_due_monthly_future_and_past():
    today = date(2026, 3, 15)
    assert next_due_date(row(due_day=20), today) == date(2026, 3, 20)
    assert next_due_date(row(due_day=10), today) == date(2026, 4, 10)


def test_next_due_monthly_feb_clamp():
    # due_day 30 in February clamps to 28 (2026 is not a leap year)
    assert next_due_date(row(due_day=30), date(2026, 2, 1)) == date(2026, 2, 28)


def test_next_due_quarterly_anchor():
    r = row(frequency="quarterly", due_day=10, due_month=1)  # Jan/Apr/Jul/Oct
    assert next_due_date(r, date(2026, 1, 5)) == date(2026, 1, 10)
    assert next_due_date(r, date(2026, 1, 15)) == date(2026, 4, 10)
    assert next_due_date(r, date(2026, 3, 15)) == date(2026, 4, 10)


def test_next_due_quarterly_year_rollover():
    r = row(frequency="quarterly", due_day=10, due_month=1)
    assert next_due_date(r, date(2026, 11, 15)) == date(2027, 1, 10)


def test_next_due_annual_anchor_and_rollover():
    r = row(frequency="annual", due_day=15, due_month=6)
    assert next_due_date(r, date(2026, 3, 1)) == date(2026, 6, 15)
    assert next_due_date(r, date(2026, 7, 1)) == date(2027, 6, 15)


def test_next_due_annual_feb_clamp_across_years():
    r = row(frequency="annual", due_day=29, due_month=2)
    assert next_due_date(r, date(2026, 3, 1)) == date(2027, 2, 28)


def test_next_due_one_off_is_none():
    assert next_due_date(row(frequency="one-off"), date(2026, 3, 1)) is None


def test_next_due_quarterly_null_anchor_uses_today():
    # NULL due_month falls back to the current month as the anchor
    r = row(frequency="quarterly", due_day=10, due_month=None)
    assert next_due_date(r, date(2026, 5, 5)) == date(2026, 5, 10)


# --- previous_due_date ----------------------------------------------------

def test_previous_due_weekly():
    r = row(frequency="weekly", due_day=2)
    today = date(2026, 3, 15)
    pd = previous_due_date(r, today)
    assert pd.weekday() == 2
    assert pd <= today
    assert 0 <= (today - pd).days < 7


def test_previous_due_monthly():
    today = date(2026, 3, 15)
    assert previous_due_date(row(due_day=10), today) == date(2026, 3, 10)
    assert previous_due_date(row(due_day=20), today) == date(2026, 2, 20)


def test_previous_due_quarterly():
    r = row(frequency="quarterly", due_day=10, due_month=1)
    assert previous_due_date(r, date(2026, 3, 15)) == date(2026, 1, 10)


def test_previous_due_annual():
    r = row(frequency="annual", due_day=15, due_month=6)
    assert previous_due_date(r, date(2026, 3, 1)) == date(2025, 6, 15)


def test_previous_due_one_off_is_none():
    assert previous_due_date(row(frequency="one-off"), date(2026, 3, 1)) is None


# --- paid_period_start ----------------------------------------------------

def test_paid_period_start_weekly_matches_previous():
    r = row(frequency="weekly", due_day=2)
    today = date(2026, 3, 15)
    assert paid_period_start(r, today) == previous_due_date(r, today)


def test_paid_period_start_monthly():
    assert paid_period_start(row(due_day=20), date(2026, 3, 15)) == date(2026, 3, 1)


def test_paid_period_start_quarterly():
    # anchor Jan, today March -> most recent occurrence month is Jan
    r1 = row(frequency="quarterly", due_day=10, due_month=1)
    assert paid_period_start(r1, date(2026, 3, 15)) == date(2026, 1, 1)
    # anchor March, paying early in the due month still counts
    r2 = row(frequency="quarterly", due_day=25, due_month=3)
    assert paid_period_start(r2, date(2026, 3, 5)) == date(2026, 3, 1)


def test_paid_period_start_annual():
    r = row(frequency="annual", due_day=15, due_month=6)
    assert paid_period_start(r, date(2026, 7, 10)) == date(2026, 6, 1)
    assert paid_period_start(r, date(2026, 3, 10)) == date(2025, 6, 1)


def test_paid_period_start_one_off_is_none():
    assert paid_period_start(row(frequency="one-off"), date(2026, 3, 1)) is None


# --- totals ---------------------------------------------------------------

def test_totals_normalization(make_bill):
    make_bill(frequency="weekly", amount=10)      # 10*52/12 = 43.33/mo
    make_bill(frequency="monthly", amount=20)     # 20/mo
    make_bill(frequency="quarterly", amount=30, due_month=1)   # 10/mo
    make_bill(frequency="annual", amount=120, due_month=1)     # 10/mo
    make_bill(frequency="one-off", amount=999)    # ignored
    with models.db() as conn:
        m = monthly_total(conn)
        a = annual_total(conn)
    expected = round(10 * 52 / 12 + 20 + 10 + 10, 2)
    assert m == expected
    assert a == round(expected * 12, 2)


def test_category_monthly_totals(make_bill):
    cat = models.CATEGORIES[0]
    make_bill(frequency="monthly", amount=20, category=cat)
    make_bill(frequency="monthly", amount=30, category=cat)
    with models.db() as conn:
        totals = category_monthly_totals(conn)
    assert totals[cat] == 50.0


# --- overdue_bill_ids -----------------------------------------------------

def test_overdue_unpaid_monthly(make_bill):
    bid = make_bill(frequency="monthly", due_day=1)
    today = date(2026, 3, 15)
    with models.db() as conn:
        assert bid in overdue_bill_ids(conn, today, {})


def test_overdue_cleared_by_payment_in_period(make_bill):
    bid = make_bill(frequency="monthly", due_day=1)
    today = date(2026, 3, 15)
    last_paid = {bid: date(2026, 3, 5)}
    with models.db() as conn:
        assert bid not in overdue_bill_ids(conn, today, last_paid)


def test_overdue_excludes_auto_pay(make_bill):
    bid = make_bill(frequency="monthly", due_day=1, auto_pay=1)
    today = date(2026, 3, 15)
    with models.db() as conn:
        assert bid not in overdue_bill_ids(conn, today, {})


def test_overdue_excludes_inactive(make_bill):
    bid = make_bill(frequency="monthly", due_day=1, active=0)
    today = date(2026, 3, 15)
    with models.db() as conn:
        assert bid not in overdue_bill_ids(conn, today, {})


def test_overdue_not_due_today(make_bill):
    # due today (prev == today) is not overdue
    bid = make_bill(frequency="monthly", due_day=15)
    today = date(2026, 3, 15)
    with models.db() as conn:
        assert bid not in overdue_bill_ids(conn, today, {})


def test_overdue_not_flagged_before_next_due_when_last_period_paid(make_bill):
    # due the 6th, paid 6 June: on 4 July (before the July due date) the bill
    # must not be overdue — June's occurrence was covered
    bid = make_bill(frequency="monthly", due_day=6)
    today = date(2026, 7, 4)
    last_paid = {bid: date(2026, 6, 6)}
    with models.db() as conn:
        assert bid not in overdue_bill_ids(conn, today, last_paid)
        # and once July's due date passes unpaid, it is overdue
        assert bid in overdue_bill_ids(conn, date(2026, 7, 7), last_paid)


def test_overdue_skips_occurrences_before_creation(make_bill):
    # quarterly anchored to January: previous occurrence (10 Jan) predates the
    # bill's creation, so a bill added mid-cycle is not overdue for it
    bid = make_bill(
        frequency="quarterly", due_day=10, due_month=1, created_at="2026-03-14"
    )
    today = date(2026, 3, 15)
    with models.db() as conn:
        assert bid not in overdue_bill_ids(conn, today, {})


# --- migration / backfill -------------------------------------------------

def test_migration_backfills_due_month(tmp_path, monkeypatch):
    dbfile = tmp_path / "old.db"
    monkeypatch.setattr(models, "DB_PATH", dbfile)
    conn = sqlite3.connect(dbfile)
    conn.executescript(
        """
        CREATE TABLE bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT '',
            due_day INTEGER NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'monthly',
            category TEXT NOT NULL DEFAULT 'family',
            active INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (date('now'))
        );
        CREATE TABLE payment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL REFERENCES bills(id),
            paid_date TEXT NOT NULL DEFAULT (date('now')),
            amount_paid REAL NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO bills (id, name, amount, due_day, frequency) "
        "VALUES (1, 'Water', 90, 10, 'quarterly')"
    )
    conn.execute(
        "INSERT INTO bills (id, name, amount, due_day, frequency) "
        "VALUES (2, 'NoPay', 90, 10, 'annual')"
    )
    conn.execute(
        "INSERT INTO payment_history (bill_id, paid_date, amount_paid) "
        "VALUES (1, '2025-08-20', 90)"
    )
    conn.commit()
    conn.close()

    init_db()

    with models.db() as c:
        r1 = c.execute("SELECT due_month FROM bills WHERE id=1").fetchone()
        r2 = c.execute("SELECT due_month FROM bills WHERE id=2").fetchone()
    assert r1["due_month"] == 8  # backfilled from the August payment
    assert r2["due_month"] == date.today().month  # no payment -> current month


def test_migration_dedupes_and_indexes(tmp_path, monkeypatch):
    dbfile = tmp_path / "dupes.db"
    monkeypatch.setattr(models, "DB_PATH", dbfile)
    conn = sqlite3.connect(dbfile)
    conn.executescript(
        """
        CREATE TABLE bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, amount REAL NOT NULL, due_day INTEGER NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'monthly'
        );
        CREATE TABLE payment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL REFERENCES bills(id),
            paid_date TEXT NOT NULL DEFAULT (date('now')),
            amount_paid REAL NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO bills (id, name, amount, due_day) VALUES (1, 'X', 5, 1)")
    conn.execute("INSERT INTO payment_history (bill_id, paid_date, amount_paid) VALUES (1, '2026-01-01', 5)")
    conn.execute("INSERT INTO payment_history (bill_id, paid_date, amount_paid) VALUES (1, '2026-01-01', 5)")
    conn.commit()
    conn.close()

    init_db()

    with models.db() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM payment_history WHERE bill_id=1 AND paid_date='2026-01-01'"
        ).fetchone()[0]
        # unique index now rejects a same-day duplicate
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO payment_history (bill_id, paid_date, amount_paid) VALUES (1, '2026-01-01', 5)")
    assert n == 1
