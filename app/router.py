import csv
import io
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

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
    type_monthly_totals,
    type_monthly_totals_by_category,
)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


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
    month_start = today.replace(day=1).isoformat()
    paid_rows = conn.execute(
        "SELECT DISTINCT bill_id FROM payment_history WHERE paid_date >= ?",
        (month_start,),
    ).fetchall()
    paid_this_month = {r["bill_id"] for r in paid_rows}
    overdue = overdue_bill_ids(conn, paid_this_month)
    type_totals = type_monthly_totals(conn)
    type_totals_by_cat = type_monthly_totals_by_category(conn)
    ctx = {
        "bills": bills,
        "history": history,
        "upcoming": upcoming,
        "monthly_total": total,
        "annual_total": ann,
        "cat_totals": cat_totals,
        "next_dues": next_dues,
        "paid_this_month": paid_this_month,
        "overdue_ids": overdue,
        "type_totals": type_totals,
        "type_totals_by_cat": type_totals_by_cat,
        "bill_types": BILL_TYPES,
        "frequencies": FREQUENCIES,
        "categories": CATEGORIES,
        "weekdays": WEEKDAYS,
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


@router.post("/bills/add")
async def add_bill(
    name: str = Form(...),
    amount: float = Form(...),
    due_day: int = Form(...),
    frequency: str = Form("monthly"),
    category: str = Form("james"),
    bill_type: str = Form("other"),
    auto_pay: str = Form(default=""),
    notes: str = Form(""),
    url: str = Form(""),
):
    with db() as conn:
        conn.execute(
            "INSERT INTO bills (name, amount, currency, due_day, frequency, category, bill_type, auto_pay, notes, url) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, amount, "GBP", due_day, frequency, category, bill_type, 1 if auto_pay else 0, notes or None, url or None),
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
    category: str = Form("james"),
    bill_type: str = Form("other"),
    auto_pay: str = Form(default=""),
    notes: str = Form(""),
    url: str = Form(""),
):
    with db() as conn:
        bill = conn.execute("SELECT id FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(status_code=404)
        conn.execute(
            "UPDATE bills SET name=?, amount=?, due_day=?, frequency=?, category=?, bill_type=?, auto_pay=?, notes=?, url=? WHERE id=?",
            (name, amount, due_day, frequency, category, bill_type, 1 if auto_pay else 0, notes or None, url or None, bill_id),
        )
    return RedirectResponse("/", status_code=303)


@router.post("/bills/{bill_id}/delete")
async def delete_bill(bill_id: int):
    with db() as conn:
        conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
    return RedirectResponse("/", status_code=303)


@router.post("/bills/{bill_id}/toggle")
async def toggle_bill(bill_id: int):
    with db() as conn:
        conn.execute("UPDATE bills SET active = 1 - active WHERE id = ?", (bill_id,))
    return RedirectResponse("/", status_code=303)


@router.post("/bills/{bill_id}/pay")
async def mark_paid(bill_id: int, amount_paid: float = Form(None)):
    with db() as conn:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(status_code=404)
        conn.execute(
            "INSERT INTO payment_history (bill_id, amount_paid) VALUES (?, ?)",
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
            "SELECT name, amount, due_day, frequency, category, active, notes "
            "FROM bills ORDER BY id ASC"
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["name", "amount", "due_day", "frequency", "category", "active", "notes"])
    for row in rows:
        writer.writerow([
            row["name"],
            f"{row['amount']:.2f}",
            row["due_day"],
            row["frequency"],
            row["category"],
            "yes" if row["active"] else "no",
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
    await send_alert("Bill Tracker test", "Alerts are working correctly.", priority="default")
    return {"status": "sent", "topic": __import__("app.config", fromlist=["settings"]).settings.ntfy_topic}


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
