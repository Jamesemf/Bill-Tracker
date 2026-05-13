import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass
class Settings:
    ntfy_topic: str = os.getenv("NTFY_TOPIC", "")
    alert_days_before: int = int(os.getenv("ALERT_DAYS_BEFORE", "3"))
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))


settings = Settings()
