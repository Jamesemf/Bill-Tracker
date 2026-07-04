import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_alert(title: str, message: str, priority: str = "default") -> None:
    if not settings.ntfy_topic:
        logger.info("ntfy topic not configured — skipping alert")
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.ntfy_server}/{settings.ntfy_topic}",
                content=message,
                headers={
                    "Title": title,
                    "Priority": priority,
                    "Tags": "money_with_wings",
                },
                timeout=10,
            )
    except Exception as exc:
        logger.warning("Failed to send ntfy alert: %s", exc)


async def alert_upcoming_bill(
    name: str, amount: float, days_until: int, auto_pay: bool = False
) -> None:
    when = "today" if days_until == 0 else f"in {days_until} day{'s' if days_until != 1 else ''}"
    if auto_pay:
        title = f"Auto-pay {when}: {name}"
        detail = "will be paid automatically"
        priority = "default"
    else:
        title = f"Bill due {when}: {name}"
        detail = f"due {when}"
        priority = "high" if days_until == 0 else "default"
    message = f"{name} — {settings.currency_symbol}{amount:.2f} {detail}"
    await send_alert(title, message, priority)
