import app.models as models
from app.models import BILL_TYPES, CATEGORIES


def _form(**overrides):
    data = {
        "name": "Netflix",
        "amount": "9.99",
        "due_day": "1",
        "frequency": "monthly",
        "category": CATEGORIES[0],
        "bill_type": BILL_TYPES[-1],
    }
    data.update(overrides)
    return data


# --- add validation -------------------------------------------------------

def test_add_valid(client):
    r = client.post("/bills/add", data=_form(), follow_redirects=False)
    assert r.status_code == 303
    with models.db() as conn:
        rows = conn.execute("SELECT * FROM bills WHERE name='Netflix'").fetchall()
    assert len(rows) == 1


def test_add_bad_frequency(client):
    r = client.post("/bills/add", data=_form(frequency="fortnightly"), follow_redirects=False)
    assert r.status_code == 400


def test_add_bad_due_day(client):
    r = client.post("/bills/add", data=_form(due_day="45"), follow_redirects=False)
    assert r.status_code == 400


def test_add_negative_amount(client):
    r = client.post("/bills/add", data=_form(amount="-5"), follow_redirects=False)
    assert r.status_code == 400


def test_add_javascript_url_rejected(client):
    r = client.post("/bills/add", data=_form(url="javascript:alert(1)"), follow_redirects=False)
    assert r.status_code == 400


def test_add_http_url_ok(client):
    r = client.post("/bills/add", data=_form(url="https://example.com"), follow_redirects=False)
    assert r.status_code == 303


def test_add_quarterly_persists_due_month(client):
    r = client.post(
        "/bills/add",
        data=_form(frequency="quarterly", due_day="10", due_month="4"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    with models.db() as conn:
        b = conn.execute("SELECT due_month FROM bills WHERE name='Netflix'").fetchone()
    assert b["due_month"] == 4


def test_add_quarterly_defaults_due_month(client):
    from datetime import date
    r = client.post(
        "/bills/add",
        data=_form(frequency="quarterly", due_day="10"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    with models.db() as conn:
        b = conn.execute("SELECT due_month FROM bills WHERE name='Netflix'").fetchone()
    assert b["due_month"] == date.today().month


# --- edit -----------------------------------------------------------------

def test_edit_updates_row(client, make_bill):
    bid = make_bill(name="Old")
    r = client.post(
        f"/bills/{bid}/edit",
        data=_form(name="New", amount="12.50"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    with models.db() as conn:
        b = conn.execute("SELECT * FROM bills WHERE id=?", (bid,)).fetchone()
    assert b["name"] == "New"
    assert b["amount"] == 12.5


# --- pay ------------------------------------------------------------------

def test_pay_logs_payment_and_dedupes(client, make_bill):
    bid = make_bill()
    r1 = client.post(f"/bills/{bid}/pay", data={"amount_paid": "10"}, follow_redirects=False)
    r2 = client.post(f"/bills/{bid}/pay", data={"amount_paid": "10"}, follow_redirects=False)
    assert r1.status_code == 303 and r2.status_code == 303
    with models.db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM payment_history WHERE bill_id=?", (bid,)
        ).fetchone()[0]
    assert n == 1  # same-day duplicate ignored


def test_pay_negative_rejected(client, make_bill):
    bid = make_bill()
    r = client.post(f"/bills/{bid}/pay", data={"amount_paid": "-3"}, follow_redirects=False)
    assert r.status_code == 400


# --- delete ---------------------------------------------------------------

def test_delete_removes_payments(client, make_bill):
    bid = make_bill()
    client.post(f"/bills/{bid}/pay", data={"amount_paid": "10"}, follow_redirects=False)
    r = client.post(f"/bills/{bid}/delete", follow_redirects=False)
    assert r.status_code == 303
    with models.db() as conn:
        bills = conn.execute("SELECT COUNT(*) FROM bills WHERE id=?", (bid,)).fetchone()[0]
        orphans = conn.execute(
            "SELECT COUNT(*) FROM payment_history WHERE bill_id NOT IN (SELECT id FROM bills)"
        ).fetchone()[0]
    assert bills == 0
    assert orphans == 0


# --- CSV ------------------------------------------------------------------

def test_csv_has_new_columns(client, make_bill):
    make_bill(name="Water", url="https://pay.example.com", auto_pay=1,
              frequency="quarterly", due_month=6)
    r = client.get("/export/csv")
    assert r.status_code == 200
    body = r.text
    header = body.splitlines()[0]
    for col in ("bill_type", "auto_pay", "url", "due_month"):
        assert col in header
    assert "https://pay.example.com" in body


# --- API ------------------------------------------------------------------

def test_api_bills_and_summary(client, make_bill):
    make_bill(name="A", amount=10, frequency="monthly")
    bills = client.get("/api/bills").json()
    assert any(b["name"] == "A" for b in bills)
    summary = client.get("/api/summary").json()
    assert summary["active_bills"] >= 1
    assert "monthly_total" in summary and "annual_total" in summary


# --- XSS ------------------------------------------------------------------

def test_dashboard_escapes_bill_name(client, make_bill):
    make_bill(name="x'); alert(1);('")
    html = client.get("/").text
    # the raw JS break-out sequence must never appear unescaped
    assert "'); alert(1);('" not in html
    # the bill name must not be interpolated into any inline JS string; it is
    # carried via data-* attributes and read through the DOM instead
    assert "submitPay(this, '" not in html   # old vulnerable pay handler
    assert "Delete x" not in html            # old: confirm('Delete <name>?')
    # but the bill did render (name HTML-escaped inside a data attribute)
    assert "alert(1)" in html
    assert 'data-name="x' in html
