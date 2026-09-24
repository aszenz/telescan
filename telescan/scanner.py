"""Scan the installed applications and decide the telemetry status."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from . import checks, paths, versions
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
    version: str = ""

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


def scan_app(
    app: App,
    allow_commands: bool = False,
    assume_installed: bool = False,
    version: str | None = None,
) -> Result:
    installed, why = detect(app)
    if not installed and not assume_installed:
        return Result(app, False, NOT_INSTALLED, why)

    version_bound = bool(app.versions) or any("versions" in c for c in app.checks)
    if version is None and version_bound and app.version:
        version = checks.read_version(app.version, allow_commands)
    if app.versions and version is not None and not versions.matches(version, app.versions):
        return Result(
            app,
            installed,
            checks.UNKNOWN,
            f"version {version} is outside {app.versions}; this entry does not apply",
            version=version,
        )

    findings: list[checks.Finding] = []
    groups: dict[str, list[checks.Check]] = {}
    for check in app.checks:
        spec = check.get("versions") or app.versions
        if spec and version is not None and not versions.matches(version, spec):
            continue
        groups.setdefault(check.get("component", "telemetry"), []).append(check)
        found = checks.run_checks(check, allow_commands=allow_commands)
        if spec and version is None:
            # A control that depends on the version cannot prove ON or OFF
            # until the version is known.
            for finding in found:
                if finding.state != checks.UNKNOWN:
                    finding.state = checks.UNKNOWN
                    finding.evidence += f" (applies to {spec}; installed version unknown)"
        findings.extend(found)

    components = []
    for name, rules in (groups or {"telemetry": []}).items():
        relevant = [f for f in findings if f.component == name]
        profiles = list(dict.fromkeys(f.profile for f in relevant if f.profile))
        unknown_version = version is None and any(r.get("versions") or app.versions for r in rules)
        for profile in profiles or [""]:
            selected = [f for f in relevant if not f.profile or f.profile == profile]
            default = next((r["default_state"] for r in rules if "default_state" in r), app.default_state)
            manual = bool(rules) and all(r["type"] == "manual" for r in rules)
            if unknown_version:
                default, manual = "unknown", False
            status, reason = _resolve(selected, default, manual)
            if unknown_version and not selected:
                reason = "the controls depend on the version, which is unknown (try --run-commands)"
            elif not rules and app.checks:
                reason = f"no control applies to version {version}; {reason}"
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
    reason = (
        components[0].reason
        if len(components) == 1
        else "; ".join(
            f"{c.name}" + (f" [{c.profile}]" if c.profile else "") + f": {c.status} ({c.reason})"
            for c in components
        )
    )
    return Result(app, installed, status, reason, findings, components, version or "")


def _resolve(findings: list[checks.Finding], default: str, manual: bool) -> tuple[str, str]:
    # Within one component, read errors and unrecognized values cannot
    # establish an opt-out, and conflicting settings stay UNKNOWN until the
    # precedence of the application is modeled.  Independent components and
    # profiles never cancel each other; see scan_app.
    for finding in findings:
        if finding.state == checks.UNKNOWN:
            return checks.UNKNOWN, finding.evidence
    states = {finding.state for finding in findings}
    if {checks.DISABLED, checks.ENABLED} <= states:
        return checks.UNKNOWN, "conflicting settings: " + "; ".join(
            f"{finding.evidence} ({finding.state})" for finding in findings
        )
    if findings:
        return findings[0].state, findings[0].evidence
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
    results = [scan_app(app, allow_commands=allow_commands, assume_installed=False) for app in apps]
    if not include_missing:
        results = [r for r in results if r.installed]
    results.sort(key=lambda r: (STATUS_ORDER.index(r.status), r.app.category, r.app.name))
    return results


def summarize(results: list[Result]) -> dict[str, int]:
    counts = dict.fromkeys(STATUS_ORDER, 0)
    for result in results:
        counts[result.status] += 1
    counts["total"] = len(results)
    return counts
