"""Scan the installed applications and decide the telemetry status."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from . import checks, paths
from .catalog import App, Catalog

NOT_INSTALLED = "not_installed"
MANUAL = "manual"
PARTIAL = "partial"
STATUS_ORDER = [checks.ENABLED, PARTIAL, checks.UNKNOWN, MANUAL, checks.DISABLED, NOT_INSTALLED]


@dataclass
class ComponentResult:
    name: str
    status: str
    reason: str
    profile: str = ""


@dataclass
class Result:
    app: App
    installed: bool
    status: str
    reason: str
    findings: list[checks.Finding] = field(default_factory=list)

    components: list[ComponentResult] = field(default_factory=list)

    @property
    def needs_action(self) -> bool:
        return self.status in (checks.ENABLED, PARTIAL, checks.UNKNOWN, MANUAL)


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
    groups: dict[str, list[dict]] = {}
    for check in app.checks:
        groups.setdefault(check.get("component", "telemetry"), []).append(check)
        findings.extend(checks.run_checks(check, allow_commands=allow_commands))

    components = []
    for name, rules in (groups or {"telemetry": []}).items():
        relevant = [f for f in findings if f.component == name]
        profiles = list(dict.fromkeys(f.profile for f in relevant if f.profile))
        for profile in profiles or [""]:
            selected = [f for f in relevant if not f.profile or f.profile == profile]
            default = next((r["default_state"] for r in rules if "default_state" in r), app.default_state)
            status, reason = _resolve(selected, default, bool(rules) and all(r["type"] == "manual" for r in rules))
            components.append(ComponentResult(name, status, reason, profile))

    states = {c.status for c in components}
    if len(states) == 1:
        status = components[0].status
    elif checks.DISABLED in states:
        status = PARTIAL
    elif checks.ENABLED in states:
        status = checks.ENABLED
    else:
        status = checks.UNKNOWN
    reason = components[0].reason if len(components) == 1 else "; ".join(
        f"{c.name}" + (f" [{c.profile}]" if c.profile else "") + f": {c.status} ({c.reason})"
        for c in components
    )
    return Result(app, installed, status, reason, findings, components)


def _resolve(findings: list[checks.Finding], default: str, manual: bool) -> tuple[str, str]:
    # Preserve legacy opt-out semantics within one component only. Independent
    # components and profiles must never cancel each other's enabled findings.
    # Read errors and unrecognized values cannot establish an opt-out.
    for finding in findings:
        if finding.state == checks.UNKNOWN:
            return checks.UNKNOWN, finding.evidence
    for state in (checks.DISABLED, checks.ENABLED):
        for finding in findings:
            if finding.state == state:
                return state, finding.evidence
    if manual:
        return MANUAL, "no local switch to read; check by hand"
    if default == "on":
        return checks.ENABLED, "no opt-out found; assumed on from catalog default"
    if default == "off":
        return checks.DISABLED, "assumed off from catalog default"
    return checks.UNKNOWN, "no setting found"


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
