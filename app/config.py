import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DEFAULT_MEMBERS = "alex,sam,family"
DEFAULT_BILL_TYPES = "utilities,streaming,health,wellness,insurance,education,software,finance,other"


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    """Parse a comma-separated env var into lowercase, CSS-class-safe slugs."""
    raw = os.getenv(name, default)
    items = []
    for part in raw.split(","):
        slug = "-".join(part.strip().lower().split())
        if slug and slug not in items:
            items.append(slug)
    return tuple(items)


@dataclass
class Settings:
    app_title: str = os.getenv("APP_TITLE", "Bill Tracker")
    currency_symbol: str = os.getenv("CURRENCY_SYMBOL", "£")
    family_members: tuple[str, ...] = field(default_factory=lambda: _csv_env("FAMILY_MEMBERS", DEFAULT_MEMBERS))
    bill_types: tuple[str, ...] = field(default_factory=lambda: _csv_env("BILL_TYPES", DEFAULT_BILL_TYPES))
    ntfy_server: str = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    ntfy_topic: str = os.getenv("NTFY_TOPIC", "")
    alert_days_before: int = int(os.getenv("ALERT_DAYS_BEFORE", "3"))
    alert_hour: int = int(os.getenv("ALERT_HOUR", "8"))
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))


settings = Settings()
