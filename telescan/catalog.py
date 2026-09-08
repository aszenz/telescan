"""Load and query the application catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

DATA_FILE = Path(__file__).with_name("data") / "apps.json"

VALID_PLATFORMS = {"linux", "macos", "windows"}
VALID_DEFAULTS = {"on", "off", "unknown"}


@dataclass
class App:
    id: str
    name: str
    category: str
    platforms: list[str]
    what: str
    default_state: str = "unknown"
    detect: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    disable: dict[str, Any] = field(default_factory=dict)
    docs: str = ""

    @property
    def steps(self) -> list[str]:
        return list(self.disable.get("steps", []))

    @property
    def env(self) -> dict[str, str]:
        return dict(self.disable.get("env", {}))

    @property
    def commands(self) -> list[str]:
        return list(self.disable.get("commands", []))


class Catalog:
    def __init__(self, apps: list[App]) -> None:
        self.apps = apps

    def __iter__(self) -> Iterator[App]:
        return iter(self.apps)

    def __len__(self) -> int:
        return len(self.apps)

    @classmethod
    def load(cls, path: Path | None = None) -> "Catalog":
        raw = json.loads((path or DATA_FILE).read_text(encoding="utf-8"))
        apps = [App(**entry) for entry in raw["apps"]]
        apps.sort(key=lambda a: (a.category.lower(), a.name.lower()))
        cls._validate(apps)
        return cls(apps)

    @staticmethod
    def _validate(apps: list[App]) -> None:
        seen: set[str] = set()
        for app in apps:
            if app.id in seen:
                raise ValueError(f"duplicate app id: {app.id}")
            seen.add(app.id)
            if app.default_state not in VALID_DEFAULTS:
                raise ValueError(f"{app.id}: bad default_state {app.default_state}")
            bad = set(app.platforms) - VALID_PLATFORMS
            if bad:
                raise ValueError(f"{app.id}: bad platforms {sorted(bad)}")
            for check in app.checks:
                if "type" not in check:
                    raise ValueError(f"{app.id}: check without a type")

    def categories(self) -> list[str]:
        return sorted({app.category for app in self.apps})

    def get(self, app_id: str) -> App | None:
        for app in self.apps:
            if app.id == app_id:
                return app
        return None

    def search(self, term: str) -> list[App]:
        term = term.lower()
        return [
            app
            for app in self.apps
            if term in app.id.lower()
            or term in app.name.lower()
            or term in app.category.lower()
        ]

    def filter(
        self,
        platform: str | None = None,
        category: str | None = None,
        only: list[str] | None = None,
    ) -> list[App]:
        result = list(self.apps)
        if platform:
            result = [a for a in result if platform in a.platforms]
        if category:
            result = [a for a in result if a.category.lower() == category.lower()]
        if only:
            wanted = {o.lower() for o in only}
            result = [a for a in result if a.id.lower() in wanted]
        return result
