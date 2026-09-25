"""Render scan results as a table, as JSON or as Markdown."""

from __future__ import annotations

import json
import os
import sys
from typing import TextIO

from .catalog import App
from .checks import DISABLED, ENABLED, UNKNOWN
from .scanner import MANUAL, NOT_INSTALLED, PARTIAL, STATUS_ORDER, Result, summarize

# Every label names the telemetry, so "on" cannot be read as "protection on".
LABELS = {
    ENABLED: "Telemetry on",
    PARTIAL: "Telemetry partly off",
    UNKNOWN: "Cannot tell",
    MANUAL: "Check by hand",
    DISABLED: "Telemetry off",
    NOT_INSTALLED: "Not installed",
}
# What each group means, printed under its heading.
MEANINGS = {
    ENABLED: "these apps send telemetry",
    PARTIAL: "some telemetry is off, some is still on",
    UNKNOWN: "a setting is unreadable, conflicts, or is missing",
    MANUAL: "there is no local setting to read",
    DISABLED: "telemetry is turned off",
    NOT_INSTALLED: "",
}
# How to name a component state in a detail line.
STATE_WORDS = {
    ENABLED: "on",
    PARTIAL: "partly off",
    UNKNOWN: "cannot tell",
    MANUAL: "check by hand",
    DISABLED: "off",
}

COLORS = {
    PARTIAL: "\033[33m",
    ENABLED: "\033[31m",
    DISABLED: "\033[32m",
    UNKNOWN: "\033[33m",
    MANUAL: "\033[35m",
    NOT_INSTALLED: "\033[90m",
}
BOLD = "\033[1m"
DIM = "\033[90m"
RESET = "\033[0m"


def use_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class Painter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def status(self, status: str, text: str = "") -> str:
        text = text or LABELS[status]
        return f"{COLORS[status]}{text}{RESET}" if self.enabled else text

    def bold(self, text: str) -> str:
        return f"{BOLD}{text}{RESET}" if self.enabled else text

    def dim(self, text: str) -> str:
        return f"{DIM}{text}{RESET}" if self.enabled else text


def _plain_width(text: str) -> int:
    out, skip = 0, False
    for char in text:
        if char == "\033":
            skip = True
        elif skip:
            if char == "m":
                skip = False
        else:
            out += 1
    return out


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _plain_width(text))


def detail(result: Result) -> str:
    """One short line: which parts are on and off, or why."""
    if result.status == MANUAL:
        return ""
    if len(result.components) < 2:
        return result.reason
    if all(c.status == result.status for c in result.components):
        # The heading already gives the state; name the parts only.
        return ", ".join(dict.fromkeys(c.name for c in result.components))
    parts: dict[str, list[str]] = {}
    for component in result.components:
        parts.setdefault(component.status, [])
        if component.name not in parts[component.status]:
            parts[component.status].append(component.name)
    return "; ".join(
        f"{STATE_WORDS[state]}: {', '.join(parts[state])}" for state in STATUS_ORDER if state in parts
    )


def render_table(results: list[Result], painter: Painter, verbose: bool = False) -> str:
    if not results:
        return "No app from the catalog was found on this machine.\n"
    lines = []
    for status in STATUS_ORDER:
        group = [r for r in results if r.status == status]
        if not group:
            continue
        heading = painter.status(status, f"{LABELS[status]} ({len(group)})")
        meaning = MEANINGS[status]
        lines.append(painter.bold(heading) + (painter.dim(f"  {meaning}") if meaning else ""))
        id_w = max(len(r.app.id) for r in group)
        for result in group:
            text = f"  {result.app.id.ljust(id_w)}  {result.app.name}"
            if result.installed and detail(result):
                text += f"\n  {' ' * id_w}  {painter.dim(detail(result))}"
            lines.append(text)
            if verbose:
                pad = " " * (id_w + 4)
                lines.append(f"{pad}{painter.dim('· ' + provenance(result.app))}")
                # One setting can serve several components; show it once.
                for source, evidence in dict.fromkeys((f.source, f.evidence) for f in result.findings):
                    lines.append(f"{pad}{painter.dim('· ' + source + ': ' + evidence)}")
        lines.append("")
    return "\n".join(lines)


def provenance(app: App) -> str:
    """One line: verification, audit date, scope and version range."""
    text = f"{app.verification}, audited {app.verified_at or 'never'}; scope: {', '.join(app.scope)}"
    if app.version_note:
        text += f"; versions: {app.version_note}"
    return text


def render_summary(results: list[Result], painter: Painter) -> str:
    counts = summarize(results)
    found = counts["total"] - counts[NOT_INSTALLED]
    noun = "app" if found == 1 else "apps"
    if not found:
        return painter.bold("None of these apps is installed on this machine.") + "\n"
    lines = [painter.bold(f"telescan checked {found} {noun} on this machine.")]
    width = len(str(max(counts[s] for s in STATUS_ORDER)))
    for status in STATUS_ORDER:
        if counts[status] and status != NOT_INSTALLED:
            lines.append(f"  {str(counts[status]).rjust(width)}  {painter.status(status)}")
    return "\n".join(lines) + "\n"


def render_fixes(results: list[Result], painter: Painter, include_absent: bool = False) -> str:
    todo = [r for r in results if r.needs_action or (include_absent and not r.installed)]
    if not todo:
        return painter.bold("No actionable findings in the scanned catalog entries.\n")
    lines = [painter.bold("How to turn the telemetry off:"), ""]
    for result in todo:
        app = result.app
        lines.append(f"{painter.bold(app.name)}  {painter.status(result.status)}")
        lines.append(f"    {app.what}")
        for step in app.steps:
            lines.append(f"    - {step}")
        if app.env:
            pairs = " ".join(f"{k}={v}" for k, v in app.env.items())
            lines.append(f"    - export {pairs}")
        if app.docs:
            lines.append(f"    {painter.dim(app.docs)}")
        for url in app.evidence:
            if url != app.docs:
                lines.append(f"    {painter.dim('evidence: ' + url)}")
        lines.append("")
    return "\n".join(lines)


def render_shell_profile(results: list[Result]) -> str:
    """Print the export lines that switch the telemetry off."""
    lines = ["# Generated by telescan. Add to your shell profile.", ""]
    seen: dict[str, str] = {}
    for result in results:
        if not result.needs_action:
            continue
        for key, value in result.app.env.items():
            if key not in seen:
                seen[key] = value
                lines.append(f"export {key}={value}  # {result.app.name}")
    if len(lines) == 2:
        lines.append("# Nothing to export.")
    return "\n".join(lines) + "\n"


def render_commands(results: list[Result]) -> str:
    lines = ["# Generated by telescan. Review before you run any of these.", ""]
    for result in results:
        if not result.needs_action:
            continue
        for command in result.app.commands:
            lines.append(f"{command}  # {result.app.name}")
    if len(lines) == 2:
        lines.append("# No command to run.")
    return "\n".join(lines) + "\n"


def render_json(results: list[Result]) -> str:
    payload = {
        "summary": summarize(results),
        "results": [
            {
                "id": r.app.id,
                "name": r.app.name,
                "category": r.app.category,
                "installed": r.installed,
                "status": r.status,
                "reason": r.reason,
                "what": r.app.what,
                "docs": r.app.docs,
                "verification": r.app.verification,
                "verified_at": r.app.verified_at,
                "scope": r.app.scope,
                "versions": r.app.version_note,
                "evidence": r.app.evidence,
                "version": r.version,
                "findings": [
                    {
                        "state": f.state,
                        "evidence": f.evidence,
                        "source": f.source,
                        "component": f.component,
                        "profile": f.profile,
                    }
                    for f in r.findings
                ],
                "components": [
                    {"name": c.name, "status": c.status, "reason": c.reason, "profile": c.profile}
                    for c in r.components
                ],
                "disable": {
                    "steps": r.app.steps,
                    "commands": r.app.commands,
                    "env": r.app.env,
                },
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def render_markdown(results: list[Result]) -> str:
    counts = summarize(results)
    lines = [
        "# Telemetry report",
        "",
        ", ".join(f"{LABELS[s]}: {counts[s]}" for s in STATUS_ORDER if counts[s]) + ".",
        "",
        "| Status | Application | Category | Verification | Scope | Detail | Docs |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        reason = (detail(r) or r.reason).replace("|", "\\|")
        docs = f"[docs]({r.app.docs})" if r.app.docs else ""
        verification = f"{r.app.verification} ({r.app.verified_at})"
        scope = ", ".join(r.app.scope)
        if r.app.version_note:
            scope += f"; versions: {r.app.version_note}"
        lines.append(
            f"| {LABELS[r.status]} | {r.app.name} | {r.app.category} | {verification} | {scope} "
            f"| {reason} | {docs} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def write(text: str, stream: TextIO | None = None) -> None:
    # Resolved at call time so that tests (and pipes) can replace sys.stdout.
    (stream or sys.stdout).write(text)
