import asyncio
from datetime import date

import app.models as models
import app.scheduler as scheduler
from app.models import loan_payoff, loan_remaining


def _make_loan(make_bill, left, **kwargs):
    bid = make_bill(**kwargs)
    with models.db() as conn:
        conn.execute(
            "UPDATE bills SET loan_balance = ?, loan_baseline = ? WHERE id = ?",
            (left, models.latest_payment_id(conn), bid),
        )
    return bid


def _pay(bid, paid_date, amount):
    with models.db() as conn:
        conn.execute(
            "INSERT INTO payment_history (bill_id, paid_date, amount_paid) VALUES (?, ?, ?)",
            (bid, paid_date, amount),
        )


def _remaining(bid):
    with models.db() as conn:
        return loan_remaining(conn, bid)


def _bill(bid):
    with models.db() as conn:
        return conn.execute("SELECT * FROM bills WHERE id = ?", (bid,)).fetchone()


def _form(**overrides):
    data = {"name": "Car loan", "amount": "20", "due_day": "10", "frequency": "monthly"}
    data.update(overrides)
    return data


# --- Balance -----------------------------------------------------------------

def test_only_payments_after_the_balance_was_set_count(make_bill):
    bid = make_bill(name="Car loan", amount=20)
    _pay(bid, "2026-01-10", 20)                 # before it was tracked as a loan
    with models.db() as conn:
        conn.execute(
            "UPDATE bills SET loan_balance = 100, loan_baseline = ? WHERE id = ?",
            (models.latest_payment_id(conn), bid),
        )
    assert _remaining(bid) == 100
    _pay(bid, "2026-02-10", 20)
    _pay(bid, "2026-03-10", 20)
    assert _remaining(bid) == 60


def test_not_a_loan_has_no_balance(make_bill):
    bid = make_bill()
    assert _remaining(bid) is None


# --- Routes ------------------------------------------------------------------

def test_add_loan_then_pay_it_off_and_undo(client):
    r = client.post("/bills/add", data=_form(is_loan="1", loan_left="20"), follow_redirects=False)
    assert r.status_code == 303
    with models.db() as conn:
        bid = conn.execute("SELECT id FROM bills WHERE name = 'Car loan'").fetchone()["id"]
    assert _remaining(bid) == 20

    client.post(f"/bills/{bid}/pay", data={"amount_paid": "20"}, follow_redirects=False)
    assert _remaining(bid) == 0
    assert _bill(bid)["active"] == 0             # repaid loans switch off

    with models.db() as conn:
        pid = conn.execute("SELECT id FROM payment_history WHERE bill_id = ?", (bid,)).fetchone()["id"]
    client.post(f"/payments/{pid}/delete", follow_redirects=False)
    assert _remaining(bid) == 20
    assert _bill(bid)["active"] == 1             # undoing the last payment reopens it


def test_add_loan_needs_an_amount(client):
    r = client.post("/bills/add", data=_form(is_loan="1", loan_left=""))
    assert r.status_code == 400
    r = client.post("/bills/add", data=_form(is_loan="1", loan_left="-5"))
    assert r.status_code == 400


def test_edit_keeps_balance_unless_the_figure_changes(client, make_bill):
    bid = _make_loan(make_bill, 100, name="Car loan", amount=20)
    _pay(bid, "2026-02-10", 20)
    assert _remaining(bid) == 80

    # Saving with the figure the form showed keeps the payment history counted
    client.post(f"/bills/{bid}/edit", data=_form(is_loan="1", loan_left="80.00"), follow_redirects=False)
    assert _remaining(bid) == 80
    assert _bill(bid)["loan_balance"] == 100

    # A corrected figure becomes the new balance
    client.post(f"/bills/{bid}/edit", data=_form(is_loan="1", loan_left="75"), follow_redirects=False)
    assert _remaining(bid) == 75

    # Unticking "This is a loan" stops tracking it
    client.post(f"/bills/{bid}/edit", data=_form(), follow_redirects=False)
    assert _remaining(bid) is None


def test_summary_and_csv_include_loans(client, make_bill):
    _make_loan(make_bill, 250, name="Car loan", amount=20)
    assert client.get("/api/summary").json()["loans_remaining"] == 250
    body = client.get("/export/csv").text
    assert "loan_left" in body.splitlines()[0]
    assert "250.00" in body


def test_dashboard_shows_loan_card(client, make_bill):
    from app.config import settings

    _make_loan(make_bill, 250, name="Car loan", amount=20)
    html = client.get("/").text
    assert "Loans" in html
    assert f"{settings.currency_symbol}250.00" in html
    assert "left to repay" in html
    assert "13 payments left" in html            # 12 × £20, then £10


# --- Auto-pay ----------------------------------------------------------------

class _FixedDate(date):
    @classmethod
    def today(cls):
        return date(2026, 3, 15)


def test_auto_pay_caps_final_payment_and_closes_loan(make_bill, monkeypatch):
    monkeypatch.setattr(scheduler, "date", _FixedDate)
    bid = _make_loan(make_bill, 15, name="Car loan", amount=20, due_day=15, auto_pay=1)
    asyncio.run(scheduler._auto_log_payments())
    with models.db() as conn:
        paid = conn.execute(
            "SELECT amount_paid FROM payment_history WHERE bill_id = ?", (bid,)
        ).fetchall()
    assert [p["amount_paid"] for p in paid] == [15]
    assert _remaining(bid) == 0
    assert _bill(bid)["active"] == 0


def test_auto_pay_reduces_balance(make_bill, monkeypatch):
    monkeypatch.setattr(scheduler, "date", _FixedDate)
    bid = _make_loan(make_bill, 100, name="Car loan", amount=20, due_day=15, auto_pay=1)
    asyncio.run(scheduler._auto_log_payments())
    assert _remaining(bid) == 80
    assert _bill(bid)["active"] == 1


# --- Projection --------------------------------------------------------------

def test_payoff_projection(make_bill):
    bid = make_bill(amount=30, due_day=10, frequency="monthly")
    row = _bill(bid)
    today = date(2026, 7, 8)
    assert loan_payoff(row, 100, today, paid_this_period=False) == (4, date(2026, 10, 10), 10.0)
    # Already paid this month: July's occurrence is covered, so it starts in August
    assert loan_payoff(row, 100, today, paid_this_period=True) == (4, date(2026, 11, 10), 10.0)
    assert loan_payoff(row, 0, today, paid_this_period=False) == (0, None, 0.0)
