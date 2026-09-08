"""Scan the installed applications and decide the telemetry status."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from . import checks, paths
from .catalog import App, Catalog

NOT_INSTALLED = "not_installed"
MANUAL = "manual"
STATUS_ORDER = [checks.ENABLED, checks.UNKNOWN, MANUAL, checks.DISABLED, NOT_INSTALLED]


@dataclass
class Result:
    app: App
    installed: bool
    status: str
    reason: str
    findings: list[checks.Finding] = field(default_factory=list)

    @property
    def needs_action(self) -> bool:
        return self.status in (checks.ENABLED, checks.UNKNOWN, MANUAL)


def detect(app: App) -> tuple[bool, str]:
    """Report whether the application is present on this machine."""
    detect_rules = app.detect or {}
    for binary in detect_rules.get("which", []):
        found = shutil.which(binary)
        if found:
            return True, f"found {found}"
    for pattern in detect_rules.get("paths", []):
        hits = paths.expand_glob(pattern)
        if hits:
            return True, f"found {hits[0]}"
    return False, "no binary or config path found"


def scan_app(app: App, allow_commands: bool = False, assume_installed: bool = False) -> Result:
    installed, why = detect(app)
    if not installed and not assume_installed:
        return Result(app, False, NOT_INSTALLED, why)

    findings: list[checks.Finding] = []
    for check in app.checks:
        finding = checks.run_check(check, allow_commands=allow_commands)
        if finding is not None and finding.state != checks.UNKNOWN:
            findings.append(finding)

    for finding in findings:
        if finding.state == checks.DISABLED:
            return Result(app, installed, checks.DISABLED, finding.evidence, findings)
    for finding in findings:
        if finding.state == checks.ENABLED:
            return Result(app, installed, checks.ENABLED, finding.evidence, findings)

    only_manual = bool(app.checks) and all(c["type"] == "manual" for c in app.checks)
    if only_manual:
        return Result(app, installed, MANUAL, "no local switch to read; check by hand", findings)
    if app.default_state == "on":
        return Result(app, installed, checks.ENABLED, "no opt-out found; telemetry is on by default", findings)
    if app.default_state == "off":
        return Result(app, installed, checks.DISABLED, "telemetry is off by default", findings)
    return Result(app, installed, checks.UNKNOWN, "no setting found", findings)


def scan(
    catalog: Catalog,
    platform: str | None = None,
    category: str | None = None,
    only: list[str] | None = None,
    allow_commands: bool = False,
    include_missing: bool = False,
) -> list[Result]:
    apps = catalog.filter(platform=platform, category=category, only=only)
    results = [
        scan_app(app, allow_commands=allow_commands, assume_installed=False) for app in apps
    ]
    if not include_missing:
        results = [r for r in results if r.installed]
    results.sort(key=lambda r: (STATUS_ORDER.index(r.status), r.app.category, r.app.name))
    return results


def summarize(results: list[Result]) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_ORDER}
    for result in results:
        counts[result.status] += 1
    counts["total"] = len(results)
    return counts
