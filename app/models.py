import calendar
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

from app.config import settings

DB_PATH = Path(os.environ.get("BILLS_DB", Path(__file__).parent.parent / "bills.db"))

FREQUENCIES = ("weekly", "monthly", "quarterly", "annual", "one-off")
CATEGORIES = settings.family_members
BILL_TYPES = settings.bill_types


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
                currency    TEXT    NOT NULL DEFAULT '',
                due_day     INTEGER NOT NULL,
                frequency   TEXT    NOT NULL DEFAULT 'monthly',
                category    TEXT    NOT NULL DEFAULT 'family',
                active      INTEGER NOT NULL DEFAULT 1,
                auto_pay    INTEGER NOT NULL DEFAULT 0,
                bill_type   TEXT    NOT NULL DEFAULT 'other',
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
            "ALTER TABLE bills ADD COLUMN bill_type TEXT NOT NULL DEFAULT 'other'",
            "ALTER TABLE bills ADD COLUMN due_month INTEGER",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass

        # D1 — backfill the recurrence anchor for quarterly/annual bills that
        # predate the due_month column. Anchor = month of the most recent logged
        # payment, else the current month. Idempotent (guarded by IS NULL).
        current_month = date.today().month
        conn.execute(
            """
            UPDATE bills SET due_month = COALESCE(
                (SELECT CAST(strftime('%m', MAX(ph.paid_date)) AS INTEGER)
                 FROM payment_history ph WHERE ph.bill_id = bills.id),
                ?
            )
            WHERE frequency IN ('quarterly', 'annual') AND due_month IS NULL
            """,
            (current_month,),
        )

        # D4 — dedupe same-day payment rows (keep the lowest id) before adding
        # the unique index, then create it. Idempotent.
        conn.execute(
            """
            DELETE FROM payment_history
            WHERE id NOT IN (
                SELECT MIN(id) FROM payment_history GROUP BY bill_id, paid_date
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_bill_date "
            "ON payment_history(bill_id, paid_date)"
        )

        # Remove orphaned payment rows whose bill no longer exists.
        conn.execute(
            "DELETE FROM payment_history WHERE bill_id NOT IN (SELECT id FROM bills)"
        )

        # Sanitize legacy URLs that are not http(s) (defense in depth vs XSS).
        conn.execute(
            "UPDATE bills SET url = NULL "
            "WHERE url IS NOT NULL AND url NOT LIKE 'http://%' AND url NOT LIKE 'https://%'"
        )

        conn.execute("PRAGMA user_version = 1")


def _last_day_of_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def _advance_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def _anchor_month(bill_row: sqlite3.Row, today: date) -> int:
    """Recurrence anchor month for quarterly/annual bills.

    Falls back to the current month when due_month is unset (NULL).
    """
    try:
        dm = bill_row["due_month"]
    except (IndexError, KeyError):
        dm = None
    return dm if dm else today.month


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_due_date(bill_row: sqlite3.Row, today: date) -> date | None:
    """Return the next due date on or after today.

    weekly:    due_day is weekday 0-6 (Mon-Sun)
    monthly:   due_day is day-of-month 1-31
    quarterly: due_day is day-of-month; occurs in months where (m - due_month) % 3 == 0
    annual:    due_day is day-of-month; occurs in due_month
    one-off:   returns None
    """
    freq = bill_row["frequency"]
    dd = bill_row["due_day"]

    if freq == "one-off":
        return None

    if freq == "weekly":
        days_ahead = (dd - today.weekday()) % 7
        return today + timedelta(days=days_ahead)

    if freq == "monthly":
        candidate = today.replace(day=min(dd, _last_day_of_month(today)))
        if candidate >= today:
            return candidate
        return _advance_months(candidate, 1)

    if freq == "quarterly":
        anchor = _anchor_month(bill_row, today)
        year, month = today.year, today.month
        for _ in range(15):
            if (month - anchor) % 3 == 0:
                candidate = _clamped(year, month, dd)
                if candidate >= today:
                    return candidate
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return None

    if freq == "annual":
        anchor = _anchor_month(bill_row, today)
        candidate = _clamped(today.year, anchor, dd)
        if candidate >= today:
            return candidate
        return _clamped(today.year + 1, anchor, dd)

    return None


def previous_due_date(bill_row: sqlite3.Row, today: date) -> date | None:
    """Return the most recent occurrence on or before today (None for one-off)."""
    freq = bill_row["frequency"]
    dd = bill_row["due_day"]

    if freq == "one-off":
        return None

    if freq == "weekly":
        days_since = (today.weekday() - dd) % 7
        return today - timedelta(days=days_since)

    if freq == "monthly":
        candidate = today.replace(day=min(dd, _last_day_of_month(today)))
        if candidate <= today:
            return candidate
        return _advance_months(candidate, -1)

    if freq == "quarterly":
        anchor = _anchor_month(bill_row, today)
        year, month = today.year, today.month
        for _ in range(15):
            if (month - anchor) % 3 == 0:
                candidate = _clamped(year, month, dd)
                if candidate <= today:
                    return candidate
            month -= 1
            if month < 1:
                month, year = 12, year - 1
        return None

    if freq == "annual":
        anchor = _anchor_month(bill_row, today)
        candidate = _clamped(today.year, anchor, dd)
        if candidate <= today:
            return candidate
        return _clamped(today.year - 1, anchor, dd)

    return None


def paid_period_start(bill_row: sqlite3.Row, today: date) -> date | None:
    """Start of the current 'paid' window; a payment on/after this counts as paid.

    weekly:    the most recent due weekday <= today
    monthly:   the 1st of the current month
    quarterly/annual: the 1st of the month containing the current/most-recent
               occurrence, so an early payment inside the due month still counts
    one-off:   None (any payment marks it paid)
    """
    freq = bill_row["frequency"]

    if freq == "one-off":
        return None

    if freq == "weekly":
        return previous_due_date(bill_row, today)

    if freq == "monthly":
        return today.replace(day=1)

    anchor = _anchor_month(bill_row, today)

    if freq == "quarterly":
        year, month = today.year, today.month
        for _ in range(15):
            if (month - anchor) % 3 == 0:
                return date(year, month, 1)
            month -= 1
            if month < 1:
                month, year = 12, year - 1
        return None

    if freq == "annual":
        if today.month >= anchor:
            return date(today.year, anchor, 1)
        return date(today.year - 1, anchor, 1)

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


def overdue_bill_ids(
    conn: sqlite3.Connection, today: date, last_paid: dict[int, date]
) -> set[int]:
    """Return IDs of active, non-auto-pay recurring bills whose most recent
    occurrence has passed and that have no payment in the current paid window.

    Occurrences that predate the bill's creation don't count — a bill added
    mid-cycle isn't overdue for a due date it never existed for.

    ``last_paid`` maps bill_id -> most recent payment date.
    """
    overdue: set[int] = set()
    rows = conn.execute(
        "SELECT * FROM bills WHERE active = 1 AND auto_pay = 0"
    ).fetchall()
    for row in rows:
        prev = previous_due_date(row, today)
        if prev is None or prev >= today:
            continue
        try:
            created = date.fromisoformat(str(row["created_at"])[:10])
        except (TypeError, ValueError):
            created = None
        if created is not None and prev < created:
            continue
        # A payment any time since the start of the missed occurrence's own
        # window clears it (weekly: the occurrence day; monthly/quarterly/
        # annual: the 1st of its month, so an early same-month payment
        # counts). Comparing against today's window instead would flag e.g.
        # a monthly bill due the 6th, paid 6 June, as overdue on 1-5 July.
        threshold = prev if row["frequency"] == "weekly" else prev.replace(day=1)
        lp = last_paid.get(row["id"])
        if lp is not None and lp >= threshold:
            continue
        overdue.add(row["id"])
    return overdue


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
