import asyncio
import logging
from datetime import date, datetime, timedelta

from app.alerts import alert_upcoming_bill
from app.config import settings
from app.models import bills_due_within, db, paid_period_start, previous_due_date

logger = logging.getLogger(__name__)

# How many days after a due date we will still auto-log a missed payment.
CATCHUP_DAYS = 3


async def _check_upcoming_bills() -> None:
    with db() as conn:
        upcoming = bills_due_within(conn, settings.alert_days_before)
    for row, days_until in upcoming:
        logger.info("Alerting: %s due in %d days", row["name"], days_until)
        await alert_upcoming_bill(
            row["name"], row["amount"], days_until, bool(row["auto_pay"])
        )


async def _auto_log_payments() -> None:
    """Log payments for auto-pay bills whose due date has arrived.

    Idempotent catch-up: for each active auto-pay recurring bill, if its most
    recent occurrence is within CATCHUP_DAYS and no payment exists in the
    current paid window, log one dated on the due date.
    """
    today = date.today()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bills WHERE active = 1 AND auto_pay = 1 AND frequency != 'one-off'"
        ).fetchall()
        for row in rows:
            prev = previous_due_date(row, today)
            if prev is None or (today - prev).days > CATCHUP_DAYS:
                continue
            period_start = paid_period_start(row, today)
            if period_start is not None:
                already = conn.execute(
                    "SELECT id FROM payment_history WHERE bill_id = ? AND paid_date >= ?",
                    (row["id"], period_start.isoformat()),
                ).fetchone()
                if already:
                    continue
            conn.execute(
                "INSERT OR IGNORE INTO payment_history (bill_id, paid_date, amount_paid) VALUES (?, ?, ?)",
                (row["id"], prev.isoformat(), row["amount"]),
            )
            logger.info(
                "Auto-logged payment for %s (%s%.2f) on %s",
                row["name"], settings.currency_symbol, row["amount"], prev.isoformat(),
            )


def _seconds_until_next_run(hour: int) -> float:
    # Skips to tomorrow if the alert hour has already passed today. This now
    # only defers *alerts* — auto-pay logging runs once at startup below and
    # is an idempotent catch-up, so no payment is missed by skipping to tomorrow.
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_scheduler() -> None:
    logger.info("Scheduler started — will check bills daily at %02d:00", settings.alert_hour)
    # Idempotent catch-up on startup so a service (re)start during/after the
    # alert hour still logs any due auto-pay payments without re-spamming alerts.
    try:
        await _auto_log_payments()
    except Exception:
        logger.exception("Error during startup auto-pay catch-up")
    while True:
        wait = _seconds_until_next_run(hour=settings.alert_hour)
        logger.info("Next bill check in %.0f seconds", wait)
        await asyncio.sleep(wait)
        try:
            await _auto_log_payments()
            await _check_upcoming_bills()
        except Exception:
            logger.exception("Error during daily bill check")
