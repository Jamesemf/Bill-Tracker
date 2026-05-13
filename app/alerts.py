import httpx
import logging
from app.config import settings

logger = logging.getLogger(__name__)


async def send_alert(title: str, message: str, priority: str = "default") -> None:
    if not settings.ntfy_topic:
        logger.info("ntfy topic not configured — skipping alert")
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://ntfy.sh/{settings.ntfy_topic}",
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


async def alert_upcoming_bill(name: str, amount: float, currency: str, days_until: int) -> None:
    if days_until == 0:
        title = f"Bill due today: {name}"
        priority = "high"
    else:
        title = f"Bill due in {days_until} day{'s' if days_until != 1 else ''}: {name}"
        priority = "default"
    message = f"{name} — {currency}{amount:.2f} due {'today' if days_until == 0 else f'in {days_until} days'}"
    await send_alert(title, message, priority)
