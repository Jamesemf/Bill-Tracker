import calendar
import math
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
        # Savings pots: a balance on a date, a monthly payment in, and an
        # interest rate (AER, %). See the "Savings" helpers below.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS savings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                category     TEXT    NOT NULL,
                monthly      REAL    NOT NULL DEFAULT 0,
                rate         REAL    NOT NULL DEFAULT 0,
                balance      REAL    NOT NULL DEFAULT 0,
                balance_date TEXT    NOT NULL DEFAULT (date('now')),
                notes        TEXT,
                created_at   TEXT    NOT NULL DEFAULT (date('now'))
            )
        """)

        for migration in [
            "ALTER TABLE bills ADD COLUMN url TEXT",
            "ALTER TABLE bills ADD COLUMN auto_pay INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE bills ADD COLUMN bill_type TEXT NOT NULL DEFAULT 'other'",
            "ALTER TABLE bills ADD COLUMN due_month INTEGER",
            # Loans: loan_balance is what was left to repay when tracking
            # started (or was last corrected). Payments with an id above
            # loan_baseline count against it. NULL loan_balance = not a loan.
            "ALTER TABLE bills ADD COLUMN loan_balance REAL",
            "ALTER TABLE bills ADD COLUMN loan_baseline INTEGER NOT NULL DEFAULT 0",
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


# --- Loans -------------------------------------------------------------------
#
# A loan is a bill with a balance. The balance isn't stored as a running total:
# it's loan_balance minus the payments logged for the bill since it was set
# (payment ids above loan_baseline). Logging a payment (by hand or by auto-pay)
# brings it down, and undoing one puts it back, with nothing else to update.


def latest_payment_id(conn: sqlite3.Connection) -> int:
    """Highest payment id so far; a new or corrected balance counts payments after it."""
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM payment_history").fetchone()[0]


def loan_balances(conn: sqlite3.Connection, bill_id: int | None = None) -> dict[int, dict]:
    """Balance details for loan bills, keyed by bill id.

    ``start`` is what was left to repay when tracking began (or was last
    corrected), ``repaid`` is what has been paid since, and ``remaining`` is
    the difference (never below zero).
    """
    sql = (
        "SELECT b.id, b.loan_balance, COALESCE(SUM(ph.amount_paid), 0) AS repaid "
        "FROM bills b LEFT JOIN payment_history ph "
        "ON ph.bill_id = b.id AND ph.id > b.loan_baseline "
        "WHERE b.loan_balance IS NOT NULL"
    )
    params: tuple = ()
    if bill_id is not None:
        sql += " AND b.id = ?"
        params = (bill_id,)
    sql += " GROUP BY b.id"
    balances = {}
    for row in conn.execute(sql, params).fetchall():
        start = round(row["loan_balance"], 2)
        repaid = round(row["repaid"], 2)
        balances[row["id"]] = {
            "start": start,
            "repaid": repaid,
            "remaining": max(0.0, round(start - repaid, 2)),
        }
    return balances


def loan_remaining(conn: sqlite3.Connection, bill_id: int) -> float | None:
    """What's left to repay on a loan, or None if the bill isn't a loan."""
    info = loan_balances(conn, bill_id).get(bill_id)
    return info["remaining"] if info else None


def close_loan_if_repaid(conn: sqlite3.Connection, bill_id: int) -> bool:
    """Disable a loan once nothing is left to repay, so it drops out of the
    totals and auto-pay stops logging payments. Returns True if it was closed."""
    remaining = loan_remaining(conn, bill_id)
    if remaining is not None and remaining <= 0:
        conn.execute("UPDATE bills SET active = 0 WHERE id = ?", (bill_id,))
        return True
    return False


def loan_payoff(
    bill_row: sqlite3.Row, remaining: float, today: date, paid_this_period: bool
) -> tuple[int, date | None, float]:
    """Project the rest of a loan at the bill's regular amount.

    Returns (payments left, date of the last one, amount of the last one). The
    last payment is whatever is left over, so it can be smaller than the rest.
    When this period's payment has already been made, the next occurrence in
    the same period doesn't count.
    """
    amount = bill_row["amount"]
    if remaining <= 0 or amount <= 0:
        return 0, None, 0.0
    left = math.ceil(round(remaining / amount, 6))
    final = round(remaining - (left - 1) * amount, 2)
    if bill_row["frequency"] == "one-off":
        return left, None, final
    due = next_due_date(bill_row, today)
    if due is None:
        return left, None, final
    if paid_this_period and paid_period_start(bill_row, due) == paid_period_start(bill_row, today):
        due = next_due_date(bill_row, due + timedelta(days=1))
    for _ in range(min(left, 5000) - 1):
        if due is None:
            break
        due = next_due_date(bill_row, due + timedelta(days=1))
    return left, due, final


# --- Savings -----------------------------------------------------------------
#
# A savings pot is a balance on a date, a regular monthly payment in, and an
# interest rate (AER). Nothing is logged: the balance is estimated forward from
# the date it was entered, then projected on from today. Each month the payment
# goes in at the start and that month's interest is added at the end, using
# the monthly equivalent of the AER. The rate is assumed not to change.

SAVINGS_MAX_YEARS = 40


def monthly_rate(aer_percent: float) -> float:
    """Monthly interest rate equivalent to an annual AER (in percent)."""
    return (1 + aer_percent / 100) ** (1 / 12) - 1


def months_between(start: date, end: date) -> int:
    """Whole months from start to end (0 if end is before start)."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def grow_savings(balance: float, monthly: float, aer_percent: float, months: int) -> list[float]:
    """Balance after each month, starting with ``balance`` at index 0."""
    rate = monthly_rate(aer_percent)
    series = [balance]
    for _ in range(months):
        balance = (balance + monthly) * (1 + rate)
        series.append(balance)
    return series


def savings_estimate(row: sqlite3.Row, today: date) -> float:
    """Estimated balance today: the entered balance grown by the months since."""
    try:
        since = date.fromisoformat(str(row["balance_date"])[:10])
    except ValueError:
        since = today
    months = months_between(since, today)
    return round(grow_savings(row["balance"], row["monthly"], row["rate"], months)[-1], 2)


def savings_projection(row: sqlite3.Row, today: date, years: int = SAVINGS_MAX_YEARS) -> dict:
    """Today's estimated balance and the month-by-month balance from here."""
    now = savings_estimate(row, today)
    series = grow_savings(now, row["monthly"], row["rate"], years * 12)
    return {"now": now, "series": [round(v, 2) for v in series]}


def savings_outcome(projection: dict, monthly: float, years: int) -> dict:
    """Where a projection ends up after ``years``: the total, how much of it
    is new money paid in, and how much is interest."""
    series = projection["series"]
    months = min(years * 12, len(series) - 1)
    total = series[months]
    paid_in = round(monthly * months, 2)
    return {
        "total": total,
        "paid_in": paid_in,
        "interest": max(0.0, round(total - projection["now"] - paid_in, 2)),
    }
