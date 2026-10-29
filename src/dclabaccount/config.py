"""Settings from the environment (``.env`` for a laptop run). Two databases: ``shared`` and ``heavy``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _dotenv() -> dict[str, str]:
    path = ROOT / ".env"
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Settings:
    admin_password: str
    app_password: str
    ports: dict[str, int]
    host: str = "127.0.0.1"

    @classmethod
    def load(cls) -> "Settings":
        env = {**_dotenv(), **os.environ}
        return cls(
            admin_password=env.get("LAB_ADMIN_PASSWORD", ""),
            app_password=env.get("LAB_APP_PASSWORD", ""),
            ports={"shared": int(env.get("SHARED_PORT", 32141)), "heavy": int(env.get("HEAVY_PORT", 32142))},
        )

    def _url(self, user: str, password: str, database: str) -> str:
        return f"postgresql://{user}:{password}@{self.host}:{self.ports[database]}/lab"

    def admin_url(self, database: str) -> str:
        """The container superuser: setup, seeding and operator actions. Never used by the API or the queries."""
        return self._url("lab_admin", self.admin_password, database)

    def app_url(self, database: str) -> str:
        """The application role: not the table owner, no BYPASSRLS."""
        return self._url("lab_app", self.app_password, database)
