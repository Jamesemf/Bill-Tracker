import asyncio
import logging
from datetime import date, datetime, timedelta

from app.alerts import alert_upcoming_bill
from app.config import settings
from app.models import bills_due_within, db, next_due_date

logger = logging.getLogger(__name__)


async def _check_upcoming_bills() -> None:
    with db() as conn:
        upcoming = bills_due_within(conn, settings.alert_days_before)
    for row, days_until in upcoming:
        logger.info("Alerting: %s due in %d days", row["name"], days_until)
        await alert_upcoming_bill(row["name"], row["amount"], days_until)


async def _auto_log_payments() -> None:
    today = date.today()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bills WHERE active = 1 AND auto_pay = 1 AND frequency != 'one-off'"
        ).fetchall()
        for row in rows:
            due = next_due_date(row, today)
            if due != today:
                continue
            already = conn.execute(
                "SELECT id FROM payment_history WHERE bill_id = ? AND paid_date = ?",
                (row["id"], today.isoformat()),
            ).fetchone()
            if not already:
                conn.execute(
                    "INSERT INTO payment_history (bill_id, amount_paid) VALUES (?, ?)",
                    (row["id"], row["amount"]),
                )
                logger.info(
                    "Auto-logged payment for %s (%s%.2f)",
                    row["name"], settings.currency_symbol, row["amount"],
                )


def _seconds_until_next_run(hour: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_scheduler() -> None:
    logger.info("Scheduler started — will check bills daily at %02d:00", settings.alert_hour)
    while True:
        wait = _seconds_until_next_run(hour=settings.alert_hour)
        logger.info("Next bill check in %.0f seconds", wait)
        await asyncio.sleep(wait)
        try:
            await _auto_log_payments()
            await _check_upcoming_bills()
        except Exception:
            logger.exception("Error during daily bill check")
