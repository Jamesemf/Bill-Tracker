from datetime import date, timedelta

import pytest

import app.models as models
from app.config import settings
from app.models import (
    grow_savings,
    monthly_rate,
    months_between,
    savings_estimate,
    savings_outcome,
    savings_projection,
)


def _add(client, **overrides):
    data = {"name": "Holiday fund", "monthly": "200", "rate": "4.5", "balance": "1000"}
    data.update(overrides)
    return client.post("/savings/add", data=data, follow_redirects=False)


def _rows():
    with models.db() as conn:
        return conn.execute("SELECT * FROM savings ORDER BY id").fetchall()


def _insert(balance, monthly, rate, balance_date):
    with models.db() as conn:
        cur = conn.execute(
            "INSERT INTO savings (name, category, monthly, rate, balance, balance_date) "
            "VALUES ('Pot', ?, ?, ?, ?, ?)",
            (models.CATEGORIES[0], monthly, rate, balance, balance_date.isoformat()),
        )
        return cur.lastrowid


# --- Maths -------------------------------------------------------------------

def test_monthly_rate_compounds_back_to_the_aer():
    assert (1 + monthly_rate(4.5)) ** 12 == pytest.approx(1.045)
    assert monthly_rate(0) == 0


def test_months_between_counts_whole_months():
    assert months_between(date(2026, 1, 15), date(2026, 1, 31)) == 0
    assert months_between(date(2026, 1, 15), date(2026, 2, 15)) == 1
    assert months_between(date(2026, 1, 15), date(2026, 4, 14)) == 2
    assert months_between(date(2025, 11, 1), date(2026, 2, 1)) == 3
    assert months_between(date(2026, 3, 1), date(2026, 1, 1)) == 0


def test_no_interest_is_just_what_you_paid_in():
    assert grow_savings(1000, 100, 0, 3) == [1000, 1100, 1200, 1300]


def test_growth_matches_the_closed_form():
    # Payment at the start of each month, interest at the end (annuity due)
    balance, monthly, n = 1000.0, 200.0, 60
    r = monthly_rate(4.5)
    expected = balance * (1 + r) ** n + monthly * (1 + r) * ((1 + r) ** n - 1) / r
    assert grow_savings(balance, monthly, 4.5, n)[-1] == pytest.approx(expected)


def test_outcome_splits_paid_in_and_interest():
    projection = {"now": 1000.0, "series": [round(v, 2) for v in grow_savings(1000, 200, 4.5, 60)]}
    out = savings_outcome(projection, 200, 5)
    assert out["paid_in"] == 12000
    assert out["total"] == projection["series"][60]
    assert out["interest"] == pytest.approx(out["total"] - 1000 - 12000, abs=0.01)
    assert out["interest"] > 0
    # Asking for longer than the projection covers stops at its end
    assert savings_outcome(projection, 200, 50)["total"] == projection["series"][-1]


def test_estimate_grows_from_the_date_entered(tmp_db):
    today = date(2026, 7, 8)
    sid = _insert(1000, 100, 0, date(2026, 4, 8))      # three months ago, no interest
    with models.db() as conn:
        row = conn.execute("SELECT * FROM savings WHERE id = ?", (sid,)).fetchone()
    assert savings_estimate(row, today) == 1300
    projection = savings_projection(row, today, years=1)
    assert projection["now"] == 1300
    assert len(projection["series"]) == 13
    assert projection["series"][-1] == 2500


# --- Routes ------------------------------------------------------------------

def test_add_savings(client):
    assert _add(client).status_code == 303
    (row,) = _rows()
    assert row["name"] == "Holiday fund"
    assert row["monthly"] == 200 and row["rate"] == 4.5 and row["balance"] == 1000
    assert row["balance_date"] == date.today().isoformat()
    assert row["category"] == models.CATEGORIES[0]


@pytest.mark.parametrize("field,value", [
    ("name", " "),
    ("monthly", "-1"),
    ("rate", "101"),
    ("rate", "-0.5"),
    ("balance", "-10"),
    ("category", "nobody-here"),
])
def test_add_savings_rejects_bad_input(client, field, value):
    assert _add(client, **{field: value}).status_code == 400
    assert _rows() == []


def test_edit_keeps_the_estimate_running_when_figures_are_unchanged(client):
    since = date.today() - timedelta(days=100)
    sid = _insert(1000, 200, 4.5, since)
    with models.db() as conn:
        shown = savings_estimate(conn.execute("SELECT * FROM savings").fetchone(), date.today())
    client.post(f"/savings/{sid}/edit", data={
        "name": "Renamed", "monthly": "200", "rate": "4.5", "balance": f"{shown:.2f}",
    }, follow_redirects=False)
    (row,) = _rows()
    assert row["name"] == "Renamed"
    assert row["balance"] == 1000
    assert row["balance_date"] == since.isoformat()


def test_edit_with_new_figures_starts_again_from_today(client):
    sid = _insert(1000, 200, 4.5, date.today() - timedelta(days=100))
    client.post(f"/savings/{sid}/edit", data={
        "name": "Pot", "monthly": "250", "rate": "5", "balance": "1800",
    }, follow_redirects=False)
    (row,) = _rows()
    assert (row["monthly"], row["rate"], row["balance"]) == (250, 5, 1800)
    assert row["balance_date"] == date.today().isoformat()


def test_delete_savings(client):
    sid = _insert(1000, 200, 4.5, date.today())
    client.post(f"/savings/{sid}/delete", follow_redirects=False)
    assert _rows() == []


def test_dashboard_and_api_show_savings(client):
    _add(client, monthly="100", rate="0", balance="500")
    html = client.get("/").text
    assert "Savings" in html
    assert f"{settings.currency_symbol}6,500.00" in html     # 500 + 100 × 60 months
    assert "in 5 years" in html
    assert client.get("/api/summary").json()["savings_monthly"] == 100
    (pot,) = client.get("/api/savings").json()
    assert pot["balance_now"] == 500
