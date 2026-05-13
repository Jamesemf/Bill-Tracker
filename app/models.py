import calendar
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "bills.db"

FREQUENCIES = ("weekly", "monthly", "quarterly", "annual", "one-off")
CATEGORIES = ("james", "chris", "sophia", "daniel", "caroline")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bills (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                amount      REAL    NOT NULL,
                currency    TEXT    NOT NULL DEFAULT 'GBP',
                due_day     INTEGER NOT NULL,
                frequency   TEXT    NOT NULL DEFAULT 'monthly',
                category    TEXT    NOT NULL DEFAULT 'james',
                active      INTEGER NOT NULL DEFAULT 1,
                auto_pay    INTEGER NOT NULL DEFAULT 0,
                notes       TEXT,
                url         TEXT,
                created_at  TEXT    NOT NULL DEFAULT (date('now'))
            );

            CREATE TABLE IF NOT EXISTS payment_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id     INTEGER NOT NULL REFERENCES bills(id),
                paid_date   TEXT    NOT NULL DEFAULT (date('now')),
                amount_paid REAL    NOT NULL
            );
        """)
        for migration in [
            "ALTER TABLE bills ADD COLUMN url TEXT",
            "ALTER TABLE bills ADD COLUMN auto_pay INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass


def _last_day_of_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def _advance_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def next_due_date(bill_row: sqlite3.Row, today: date) -> date | None:
    """Return the next due date on or after today.

    weekly:    due_day is weekday 0-6 (Mon-Sun)
    monthly:   due_day is day-of-month 1-31
    quarterly: due_day is day-of-month 1-31; advances 3 months if passed
    annual:    due_day is day-of-month 1-31; advances 12 months if passed
    one-off:   returns None
    """
    freq = bill_row["frequency"]
    dd = bill_row["due_day"]

    if freq == "one-off":
        return None

    if freq == "weekly":
        days_ahead = (dd - today.weekday()) % 7
        return today + timedelta(days=days_ahead)

    candidate_day = min(dd, _last_day_of_month(today))
    candidate = today.replace(day=candidate_day)

    if freq == "monthly":
        if candidate >= today:
            return candidate
        return _advance_months(candidate, 1)

    if freq == "quarterly":
        if candidate >= today:
            return candidate
        return _advance_months(candidate, 3)

    if freq == "annual":
        if candidate >= today:
            return candidate
        return _advance_months(candidate, 12)

    return None


def bills_due_within(conn: sqlite3.Connection, days: int) -> list:
    today = date.today()
    upcoming = []
    rows = conn.execute(
        "SELECT * FROM bills WHERE active = 1 AND frequency != 'one-off'"
    ).fetchall()
    for row in rows:
        due = next_due_date(row, today)
        if due is None:
            continue
        delta = (due - today).days
        if 0 <= delta <= days:
            upcoming.append((row, delta))
    return upcoming


def monthly_total(conn: sqlite3.Connection) -> float:
    rows = conn.execute(
        "SELECT amount, frequency FROM bills WHERE active = 1"
    ).fetchall()
    total = 0.0
    for row in rows:
        if row["frequency"] == "weekly":
            total += row["amount"] * 52 / 12
        elif row["frequency"] == "monthly":
            total += row["amount"]
        elif row["frequency"] == "quarterly":
            total += row["amount"] / 3
        elif row["frequency"] == "annual":
            total += row["amount"] / 12
    return round(total, 2)


def annual_total(conn: sqlite3.Connection) -> float:
    return round(monthly_total(conn) * 12, 2)


def overdue_bill_ids(conn: sqlite3.Connection, paid_this_month: set[int]) -> set[int]:
    """Return IDs of active monthly/weekly bills whose due date has passed this period and are unpaid."""
    today = date.today()
    overdue: set[int] = set()
    rows = conn.execute(
        "SELECT * FROM bills WHERE active = 1 AND frequency IN ('monthly', 'weekly')"
    ).fetchall()
    for row in rows:
        if row["id"] in paid_this_month:
            continue
        if row["frequency"] == "monthly":
            due = today.replace(day=min(row["due_day"], _last_day_of_month(today)))
            if due < today:
                overdue.add(row["id"])
        elif row["frequency"] == "weekly":
            days_since = (today.weekday() - row["due_day"]) % 7
            if 0 < days_since < 7:
                overdue.add(row["id"])
    return overdue


def spending_trends(conn: sqlite3.Connection) -> list[tuple[str, float]]:
    """Return (label, total) for each month that has payment history, oldest first."""
    from datetime import datetime as _dt
    rows = conn.execute(
        """SELECT strftime('%Y-%m', paid_date) AS month, SUM(amount_paid) AS total
           FROM payment_history
           GROUP BY month
           ORDER BY month ASC"""
    ).fetchall()
    result = []
    for r in rows:
        label = _dt.strptime(r["month"], "%Y-%m").strftime("%b '%y")
        result.append((label, round(r["total"], 2)))
    return result


def category_monthly_totals(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        "SELECT amount, frequency, category FROM bills WHERE active = 1"
    ).fetchall()
    totals: dict[str, float] = {}
    for row in rows:
        freq = row["frequency"]
        amt = row["amount"]
        if freq == "weekly":
            monthly = amt * 52 / 12
        elif freq == "monthly":
            monthly = amt
        elif freq == "quarterly":
            monthly = amt / 3
        elif freq == "annual":
            monthly = amt / 12
        else:
            continue
        cat = row["category"]
        totals[cat] = round(totals.get(cat, 0.0) + monthly, 2)
    return totals
