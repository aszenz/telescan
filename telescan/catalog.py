"""Load and query the application catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterator

from . import schema as app_schema

DATA_DIR = Path(__file__).with_name("data") / "apps.d"


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
        """Load the catalog.

        `path` is a directory of one file per application (the default), or
        a single JSON file holding either one entry or an {"apps": [...]}
        object.  A user supplied file is read the same way as the shipped
        catalog, so an entry can be tried out before it is contributed.
        """
        source = path or DATA_DIR
        entries = cls._read(source)
        contract = app_schema.load_schema()
        apps: list[App] = []
        seen: set[str] = set()
        for origin, entry in entries:
            problems = app_schema.validate(entry, contract)
            if problems:
                raise ValueError(f"{origin} does not match the schema:\n  - "
                                 + "\n  - ".join(problems))
            app_id = entry["id"]
            if app_id in seen:
                raise ValueError(f"{origin}: duplicate app id: {app_id}")
            seen.add(app_id)
            apps.append(App(**{k: v for k, v in entry.items() if k != "$schema"}))
        apps.sort(key=lambda a: (a.category.lower(), a.name.lower()))
        return cls(apps)

    @staticmethod
    def _read(source: Path) -> list[tuple[str, dict[str, Any]]]:
        files = sorted(source.glob("*.json")) if source.is_dir() else [source]
        if source.is_dir() and not files:
            raise ValueError(f"no catalog entries in {source}")
        known = {f.name for f in fields(App)}
        entries: list[tuple[str, dict[str, Any]]] = []
        for file in files:
            try:
                raw = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ValueError(f"{file.name}: {error}") from error
            found = raw["apps"] if isinstance(raw, dict) and "apps" in raw else [raw]
            for entry in found:
                if not isinstance(entry, dict):
                    raise ValueError(f"{file.name}: an entry is not an object")
                unknown = sorted(set(entry) - known - {"$schema"})
                if unknown:
                    raise ValueError(f"{file.name}: unknown field(s) {unknown}")
                if source.is_dir() and entry.get("id") != file.stem:
                    raise ValueError(
                        f"{file.name}: id {entry.get('id')!r} does not match the file name"
                    )
                entries.append((file.name, entry))
        return entries

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
