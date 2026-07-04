import asyncio
from datetime import date, datetime

import app.models as models
import app.scheduler as scheduler


class _FixedDate(date):
    @classmethod
    def today(cls):
        return date(2026, 3, 15)


class _FakeDatetime:
    fixed = datetime(2026, 3, 15, 10, 0, 0)

    @classmethod
    def now(cls):
        return cls.fixed


def _fix_today(monkeypatch):
    monkeypatch.setattr(scheduler, "date", _FixedDate)


def _payments(bid):
    with models.db() as conn:
        return conn.execute(
            "SELECT paid_date FROM payment_history WHERE bill_id=? ORDER BY paid_date",
            (bid,),
        ).fetchall()


# --- _auto_log_payments ---------------------------------------------------

def test_auto_log_on_due_date(make_bill, monkeypatch):
    _fix_today(monkeypatch)
    bid = make_bill(frequency="monthly", due_day=15, auto_pay=1)
    asyncio.run(scheduler._auto_log_payments())
    rows = _payments(bid)
    assert len(rows) == 1
    assert rows[0]["paid_date"] == "2026-03-15"


def test_auto_log_idempotent(make_bill, monkeypatch):
    _fix_today(monkeypatch)
    bid = make_bill(frequency="monthly", due_day=15, auto_pay=1)
    asyncio.run(scheduler._auto_log_payments())
    asyncio.run(scheduler._auto_log_payments())
    assert len(_payments(bid)) == 1


def test_auto_log_catchup_within_window(make_bill, monkeypatch):
    _fix_today(monkeypatch)
    bid = make_bill(frequency="monthly", due_day=13, auto_pay=1)  # 2 days ago
    asyncio.run(scheduler._auto_log_payments())
    rows = _payments(bid)
    assert len(rows) == 1
    assert rows[0]["paid_date"] == "2026-03-13"  # dated on the due date


def test_auto_log_beyond_window_skipped(make_bill, monkeypatch):
    _fix_today(monkeypatch)
    bid = make_bill(frequency="monthly", due_day=10, auto_pay=1)  # 5 days ago
    asyncio.run(scheduler._auto_log_payments())
    assert len(_payments(bid)) == 0


def test_auto_log_skips_when_already_paid(make_bill, monkeypatch):
    _fix_today(monkeypatch)
    bid = make_bill(frequency="monthly", due_day=15, auto_pay=1)
    with models.db() as conn:
        conn.execute(
            "INSERT INTO payment_history (bill_id, paid_date, amount_paid) VALUES (?, '2026-03-05', 10)",
            (bid,),
        )
    asyncio.run(scheduler._auto_log_payments())
    assert len(_payments(bid)) == 1  # no new row


# --- _seconds_until_next_run ---------------------------------------------

def test_seconds_until_next_run_passed_hour(monkeypatch):
    monkeypatch.setattr(scheduler, "datetime", _FakeDatetime)
    # 10:00 now, alert hour 08:00 already passed -> next day (22h)
    assert scheduler._seconds_until_next_run(8) == 22 * 3600


def test_seconds_until_next_run_future_hour(monkeypatch):
    monkeypatch.setattr(scheduler, "datetime", _FakeDatetime)
    # 10:00 now, alert hour 14:00 later today -> 4h
    assert scheduler._seconds_until_next_run(14) == 4 * 3600
