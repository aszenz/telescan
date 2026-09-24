"""Check engine.

A catalog entry holds a list of checks.  Every check looks at one place
(an environment variable, a config key, a file) and reports one of:

    DISABLED  telemetry is off
    ENABLED   telemetry is on
    UNKNOWN   nothing found here

The application status is the sum of its checks; see `scanner.py`.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import paths, versions

DISABLED = "disabled"
ENABLED = "enabled"
UNKNOWN = "unknown"

TRUE_WORDS = {"1", "true", "yes", "on", "enabled"}
# One check, or the `version` reader, of a catalog entry: parsed JSON.
Check = dict[str, Any]

FALSE_WORDS = {"0", "false", "no", "off", "disabled", "none"}


@dataclass
class Finding:
    """One result of one check."""

    state: str
    evidence: str
    source: str
    component: str = "telemetry"
    profile: str = ""


def _normalize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    return str(value).strip().lower()


def _matches(value: Any, wanted: Iterable[Any], exact: bool = False) -> bool:
    got = _normalize(value)
    for item in wanted:
        item_norm = _normalize(item)
        if item_norm == "*nonempty*":
            if str(value) != "":
                return True
        elif item_norm == "*truthy*":
            if got in TRUE_WORDS:
                return True
        elif item_norm == "*falsy*":
            if got in FALSE_WORDS:
                return True
        elif exact:
            # The application compares strings as they are: no case folding,
            # and a JSON boolean is not the string "true".
            if isinstance(value, str) and value == item:
                return True
        elif got == item_norm:
            return True
    return False


def _state_for(value: Any, check: Check) -> str:
    exact = check.get("exact", False)
    if _matches(value, check.get("disabled_when", []), exact):
        return DISABLED
    if _matches(value, check.get("enabled_when", []), exact):
        return ENABLED
    return UNKNOWN


# --- tolerant readers -------------------------------------------------------

_COMMENT = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def read_jsonc(text: str) -> Any:
    """Parse JSON that may hold comments and trailing commas (JSONC)."""

    def keep_strings(match: re.Match) -> str:
        return match.group(1) or ""

    stripped = _COMMENT.sub(keep_strings, text)
    stripped = _TRAILING_COMMA.sub(r"\1", stripped)
    return json.loads(stripped)


def json_lookup(data: Any, key: str) -> tuple[bool, Any]:
    """Find `key` in `data`.  Dots are path separators, but a literal
    dotted key wins, because some apps use keys such as
    "telemetry.telemetryLevel"."""
    if isinstance(data, dict) and key in data:
        return True, data[key]
    current = data
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


# --- checks -----------------------------------------------------------------


def check_env(check: Check) -> Finding | None:
    name = check["var"]
    if name not in os.environ:
        return None
    value = os.environ[name]
    state = _state_for(value, check)
    return Finding(state, f"{name}={value}", "environment")


def check_json(check: Check) -> Finding | None:
    key = check["key"]
    for pattern in check["files"]:
        for path in paths.expand_glob(pattern):
            if not path.is_file():
                continue
            try:
                data = read_jsonc(path.read_text(encoding="utf-8", errors="replace"))
            except (ValueError, OSError) as error:
                return Finding(UNKNOWN, f"cannot read JSON: {error}", str(path))
            found, value = json_lookup(data, str(key))
            if not found:
                continue
            state = _state_for(value, check)
            return Finding(state, f"{key} = {json.dumps(value)}", str(path))
    return None


def check_ini(check: Check) -> Finding | None:
    section = check.get("section", "DEFAULT")
    key = check["key"]
    for pattern in check["files"]:
        for path in paths.expand_glob(pattern):
            if not path.is_file():
                continue
            parser = configparser.ConfigParser(strict=False, allow_no_value=True, interpolation=None)
            try:
                parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
            except (configparser.Error, OSError) as error:
                return Finding(UNKNOWN, f"cannot read INI: {error}", str(path))
            if not parser.has_option(section, key):
                continue
            value = parser.get(section, key)
            return Finding(_state_for(value, check), f"[{section}] {key} = {value}", str(path))
    return None


def check_regex(check: Check) -> Finding | None:
    disabled_re = check.get("disabled_pattern")
    enabled_re = check.get("enabled_pattern")
    for pattern in check["files"]:
        for path in paths.expand_glob(pattern):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                return Finding(UNKNOWN, f"cannot read file: {error}", str(path))
            if disabled_re:
                match = re.search(disabled_re, text, re.M)
                if match:
                    return Finding(DISABLED, match.group(0).strip()[:120], str(path))
            if enabled_re:
                match = re.search(enabled_re, text, re.M)
                if match:
                    return Finding(ENABLED, match.group(0).strip()[:120], str(path))
    return None


def check_file(check: Check) -> Finding | None:
    """A file that only exists when telemetry is off (or on)."""
    state_when_present = check.get("present", DISABLED)
    for pattern in check["files"]:
        found = paths.expand_glob(pattern)
        if found:
            return Finding(state_when_present, f"{found[0]} exists", str(found[0]))
    return None


def check_command(check: Check, allow_commands: bool) -> Finding | None:
    if not allow_commands:
        return None
    argv = check["argv"]
    if not shutil.which(argv[0]):
        return None
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=check.get("timeout", 15),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return Finding(UNKNOWN, f"command failed: {error}", " ".join(argv))
    if proc.returncode != 0:
        return Finding(UNKNOWN, f"command exited {proc.returncode}", " ".join(argv))
    output = (proc.stdout + proc.stderr).strip()
    disabled_re = check.get("disabled_pattern")
    enabled_re = check.get("enabled_pattern")
    if disabled_re and re.search(disabled_re, output, re.I | re.M):
        return Finding(DISABLED, output.splitlines()[0][:120] if output else "", " ".join(argv))
    if enabled_re and re.search(enabled_re, output, re.I | re.M):
        return Finding(ENABLED, output.splitlines()[0][:120] if output else "", " ".join(argv))
    return Finding(UNKNOWN, "command output did not match a known state", " ".join(argv))


def read_version(spec: Check, allow_commands: bool = False) -> str | None:
    """Read the installed version as described by the entry's `version`.
    Return None when it cannot be read."""
    key = spec.get("key")
    patterns = spec.get("files", []) if key else []
    for path in (p for pattern in patterns for p in paths.expand_glob(pattern)):
        try:
            data = read_jsonc(path.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            continue
        found, value = json_lookup(data, str(key))
        parsed = versions.parse(str(value)) if found else None
        if parsed:
            return ".".join(map(str, parsed))
    argv = spec.get("argv")
    if not argv or not allow_commands or not shutil.which(argv[0]):
        return None
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    got = versions.parse(proc.stdout + proc.stderr) if proc.returncode == 0 else None
    return ".".join(map(str, got)) if got else None


def run_check(check: Check, allow_commands: bool = False) -> Finding | None:
    kind = check["type"]
    if kind == "env":
        return check_env(check)
    if kind == "json":
        return check_json(check)
    if kind == "ini":
        return check_ini(check)
    if kind == "regex":
        return check_regex(check)
    if kind == "file":
        return check_file(check)
    if kind == "command":
        return check_command(check, allow_commands)
    if kind == "manual":
        return None
    raise ValueError(f"unknown check type: {kind}")


def run_checks(check: Check, allow_commands: bool = False) -> list[Finding]:
    """Read every profile when requested; ordinary file lists retain precedence."""
    component = check.get("component", "telemetry")
    if check.get("profiles"):
        findings = []
        seen = set()
        for pattern in check["files"]:
            for path in paths.expand_glob(pattern):
                source = str(path)
                if source in seen:
                    continue
                seen.add(source)
                finding = run_check(dict(check, files=[source]), allow_commands)
                if finding is None:
                    default = check.get("default_state", "unknown")
                    state = {"on": ENABLED, "off": DISABLED}.get(default, UNKNOWN)
                    finding = Finding(state, f"no setting found; catalog default: {default}", source)
                finding.component = component
                finding.profile = source
                findings.append(finding)
        return findings
    finding = run_check(check, allow_commands)
    if finding is None:
        return []
    finding.component = component
    return [finding]
