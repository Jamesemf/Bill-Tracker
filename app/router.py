import csv
import io
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models import (
    BILL_TYPES,
    CATEGORIES,
    FREQUENCIES,
    annual_total,
    bills_due_within,
    category_monthly_totals,
    db,
    monthly_total,
    next_due_date,
    overdue_bill_ids,
    paid_period_start,
)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Colours are assigned to members/types by position, cycling if the list is
# longer than the palette.
MEMBER_PALETTE = ["#60a5fa", "#34d399", "#a78bfa", "#fbbf24", "#fb7185", "#e879f9", "#2dd4bf", "#f97316"]
TYPE_PALETTE = ["#67c8e8", "#a78bfa", "#34d399", "#86efac", "#c084fc", "#fdba74", "#7dd3fc", "#94a3b8", "#6b7280", "#f472b6", "#fbbf24"]

MEMBER_COLORS = {c: MEMBER_PALETTE[i % len(MEMBER_PALETTE)] for i, c in enumerate(CATEGORIES)}
TYPE_COLORS = {t: TYPE_PALETTE[i % len(TYPE_PALETTE)] for i, t in enumerate(BILL_TYPES)}

DEFAULT_CATEGORY = CATEGORIES[0]
DEFAULT_BILL_TYPE = BILL_TYPES[-1]


def _fmt_due(d) -> str:
    return f"{d.day} {d.strftime('%b')}" if d else "—"


templates.env.filters["due_date"] = _fmt_due

router = APIRouter()


def _dashboard_context(today: date, conn, edit_bill_id: int | None = None) -> dict:
    bills = conn.execute(
        "SELECT * FROM bills ORDER BY active DESC, due_day ASC"
    ).fetchall()
    history = conn.execute(
        "SELECT ph.*, b.name, b.category FROM payment_history ph "
        "JOIN bills b ON b.id = ph.bill_id "
        "ORDER BY ph.paid_date DESC LIMIT 20"
    ).fetchall()
    upcoming = bills_due_within(conn, 7)
    total = monthly_total(conn)
    ann = annual_total(conn)
    cat_totals = category_monthly_totals(conn)
    next_dues = {
        b["id"]: next_due_date(b, today)
        for b in bills
        if b["frequency"] != "one-off"
    }
    paid_rows = conn.execute(
        "SELECT bill_id, MAX(paid_date) AS last FROM payment_history GROUP BY bill_id"
    ).fetchall()
    last_paid = {
        r["bill_id"]: date.fromisoformat(r["last"]) for r in paid_rows if r["last"]
    }
    paid_ids: set[int] = set()
    for b in bills:
        lp = last_paid.get(b["id"])
        if lp is None:
            continue
        if b["frequency"] == "one-off":
            paid_ids.add(b["id"])
            continue
        ps = paid_period_start(b, today)
        if ps is not None and lp >= ps:
            paid_ids.add(b["id"])
    overdue = overdue_bill_ids(conn, today, last_paid)
    days_until = {bid: (d - today).days for bid, d in next_dues.items() if d}
    due_soon_ids = {
        b["id"]
        for b in bills
        if b["active"]
        and not b["auto_pay"]
        and b["id"] not in paid_ids
        and b["id"] not in overdue
        and 0 <= days_until.get(b["id"], 999) <= settings.alert_days_before
    }
    ctx = {
        "bills": bills,
        "history": history,
        "upcoming": upcoming,
        "monthly_total": total,
        "annual_total": ann,
        "cat_totals": cat_totals,
        "next_dues": next_dues,
        "days_until": days_until,
        "due_soon_ids": due_soon_ids,
        "paid_ids": paid_ids,
        "overdue_ids": overdue,
        "bill_types": BILL_TYPES,
        "frequencies": FREQUENCIES,
        "categories": CATEGORIES,
        "weekdays": WEEKDAYS,
        "member_colors": MEMBER_COLORS,
        "type_colors": TYPE_COLORS,
        "app_title": settings.app_title,
        "currency": settings.currency_symbol,
        "today": today.isoformat(),
    }
    if edit_bill_id is not None:
        ctx["edit_bill_id"] = edit_bill_id
    return ctx


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    today = date.today()
    with db() as conn:
        ctx = _dashboard_context(today, conn)
    return templates.TemplateResponse(request, "dashboard.html", ctx)


def _validate_bill_form(
    name: str,
    amount: float,
    due_day: int,
    frequency: str,
    category: str,
    bill_type: str,
    url: str,
    due_month: str,
) -> int | None:
    """Validate a bill add/edit form. Raises HTTPException(400) on bad input.

    Returns the resolved due_month (int for quarterly/annual, else None).
    """
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if frequency not in FREQUENCIES:
        raise HTTPException(status_code=400, detail="Invalid frequency")
    if category and category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    if bill_type and bill_type not in BILL_TYPES:
        raise HTTPException(status_code=400, detail="Invalid bill type")
    if frequency == "weekly":
        if not 0 <= due_day <= 6:
            raise HTTPException(status_code=400, detail="Day of week must be 0-6")
    else:
        if not 1 <= due_day <= 31:
            raise HTTPException(status_code=400, detail="Due day must be 1-31")

    resolved_month: int | None = None
    if frequency in ("quarterly", "annual"):
        if due_month is None or str(due_month).strip() == "":
            resolved_month = date.today().month
        else:
            try:
                resolved_month = int(due_month)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid due month")
            if not 1 <= resolved_month <= 12:
                raise HTTPException(status_code=400, detail="Due month must be 1-12")

    if url and url.strip():
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise HTTPException(status_code=400, detail="URL must be http(s)")

    return resolved_month


@router.post("/bills/add")
async def add_bill(
    name: str = Form(...),
    amount: float = Form(...),
    due_day: int = Form(...),
    frequency: str = Form("monthly"),
    category: str = Form(""),
    bill_type: str = Form(""),
    auto_pay: str = Form(default=""),
    notes: str = Form(""),
    url: str = Form(""),
    due_month: str = Form(""),
):
    resolved_month = _validate_bill_form(
        name, amount, due_day, frequency, category, bill_type, url, due_month
    )
    with db() as conn:
        conn.execute(
            "INSERT INTO bills (name, amount, due_day, frequency, category, bill_type, auto_pay, notes, url, due_month) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                name,
                amount,
                due_day,
                frequency,
                category or DEFAULT_CATEGORY,
                bill_type or DEFAULT_BILL_TYPE,
                1 if auto_pay else 0,
                notes or None,
                url or None,
                resolved_month,
            ),
        )
    return RedirectResponse("/", status_code=303)


@router.get("/bills/{bill_id}/edit", response_class=HTMLResponse)
async def edit_bill_form(bill_id: int, request: Request):
    today = date.today()
    with db() as conn:
        bill = conn.execute("SELECT id FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(status_code=404)
        ctx = _dashboard_context(today, conn, edit_bill_id=bill_id)
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.post("/bills/{bill_id}/edit")
async def edit_bill(
    bill_id: int,
    name: str = Form(...),
    amount: float = Form(...),
    due_day: int = Form(...),
    frequency: str = Form("monthly"),
    category: str = Form(""),
    bill_type: str = Form(""),
    auto_pay: str = Form(default=""),
    notes: str = Form(""),
    url: str = Form(""),
    due_month: str = Form(""),
):
    resolved_month = _validate_bill_form(
        name, amount, due_day, frequency, category, bill_type, url, due_month
    )
    with db() as conn:
        bill = conn.execute("SELECT id FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(status_code=404)
        conn.execute(
            "UPDATE bills SET name=?, amount=?, due_day=?, frequency=?, category=?, bill_type=?, auto_pay=?, notes=?, url=?, due_month=? WHERE id=?",
            (
                name,
                amount,
                due_day,
                frequency,
                category or DEFAULT_CATEGORY,
                bill_type or DEFAULT_BILL_TYPE,
                1 if auto_pay else 0,
                notes or None,
                url or None,
                resolved_month,
                bill_id,
            ),
        )
    return RedirectResponse("/", status_code=303)


@router.post("/bills/{bill_id}/delete")
async def delete_bill(bill_id: int):
    with db() as conn:
        conn.execute("DELETE FROM payment_history WHERE bill_id = ?", (bill_id,))
        conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
    return RedirectResponse("/", status_code=303)


@router.post("/bills/{bill_id}/toggle")
async def toggle_bill(bill_id: int):
    with db() as conn:
        conn.execute("UPDATE bills SET active = 1 - active WHERE id = ?", (bill_id,))
    return RedirectResponse("/", status_code=303)


@router.post("/bills/{bill_id}/pay")
async def mark_paid(bill_id: int, amount_paid: float = Form(None)):
    if amount_paid is not None and amount_paid < 0:
        raise HTTPException(status_code=400, detail="Amount paid cannot be negative")
    with db() as conn:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(status_code=404)
        conn.execute(
            "INSERT OR IGNORE INTO payment_history (bill_id, amount_paid) VALUES (?, ?)",
            (bill_id, amount_paid if amount_paid is not None else bill["amount"]),
        )
    return RedirectResponse("/", status_code=303)


@router.post("/payments/{payment_id}/delete")
async def delete_payment(payment_id: int):
    with db() as conn:
        conn.execute("DELETE FROM payment_history WHERE id = ?", (payment_id,))
    return RedirectResponse("/", status_code=303)


@router.get("/export/csv")
async def export_csv():
    with db() as conn:
        rows = conn.execute(
            "SELECT name, amount, due_day, frequency, category, bill_type, "
            "auto_pay, active, url, due_month, notes "
            "FROM bills ORDER BY id ASC"
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "name", "amount", "due_day", "frequency", "category", "bill_type",
        "auto_pay", "active", "url", "due_month", "notes",
    ])
    for row in rows:
        writer.writerow([
            row["name"],
            f"{row['amount']:.2f}",
            row["due_day"],
            row["frequency"],
            row["category"],
            row["bill_type"],
            "yes" if row["auto_pay"] else "no",
            "yes" if row["active"] else "no",
            row["url"] or "",
            row["due_month"] if row["due_month"] is not None else "",
            row["notes"] or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bills.csv"},
    )


@router.get("/test-alert")
async def test_alert():
    from app.alerts import send_alert

    await send_alert(f"{settings.app_title} test", "Alerts are working correctly.", priority="default")
    return {"status": "sent", "topic": settings.ntfy_topic}


@router.get("/api/bills")
async def api_bills():
    with db() as conn:
        rows = conn.execute("SELECT * FROM bills ORDER BY due_day ASC").fetchall()
    return [dict(r) for r in rows]


@router.get("/api/summary")
async def api_summary():
    with db() as conn:
        total = monthly_total(conn)
        ann = annual_total(conn)
        count = conn.execute("SELECT COUNT(*) FROM bills WHERE active = 1").fetchone()[0]
    return {"monthly_total": total, "annual_total": ann, "active_bills": count}
