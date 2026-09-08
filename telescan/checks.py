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
from dataclasses import dataclass
from typing import Any, Iterable

from . import paths

DISABLED = "disabled"
ENABLED = "enabled"
UNKNOWN = "unknown"

TRUE_WORDS = {"1", "true", "yes", "on", "enabled"}
FALSE_WORDS = {"0", "false", "no", "off", "disabled", "none"}


@dataclass
class Finding:
    """One result of one check."""

    state: str
    evidence: str
    source: str


def _normalize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    return str(value).strip().lower()


def _matches(value: Any, wanted: Iterable[Any]) -> bool:
    got = _normalize(value)
    for item in wanted:
        item_norm = _normalize(item)
        if item_norm == "*truthy*":
            if got in TRUE_WORDS:
                return True
        elif item_norm == "*falsy*":
            if got in FALSE_WORDS:
                return True
        elif got == item_norm:
            return True
    return False


def _state_for(value: Any, check: dict) -> str:
    if _matches(value, check.get("disabled_when", [])):
        return DISABLED
    if _matches(value, check.get("enabled_when", [])):
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


def check_env(check: dict) -> Finding | None:
    name = check["var"]
    if name not in os.environ:
        return None
    value = os.environ[name]
    state = _state_for(value, check)
    return Finding(state, f"{name}={value}", "environment")


def check_json(check: dict) -> Finding | None:
    key = check["key"]
    for pattern in check["files"]:
        for path in paths.expand_glob(pattern):
            if not path.is_file():
                continue
            try:
                data = read_jsonc(path.read_text(encoding="utf-8", errors="replace"))
            except (ValueError, OSError):
                continue
            found, value = json_lookup(data, key)
            if not found:
                continue
            state = _state_for(value, check)
            return Finding(state, f"{key} = {json.dumps(value)}", str(path))
    return None


def check_ini(check: dict) -> Finding | None:
    section = check.get("section", "DEFAULT")
    key = check["key"]
    for pattern in check["files"]:
        for path in paths.expand_glob(pattern):
            if not path.is_file():
                continue
            parser = configparser.ConfigParser(strict=False, allow_no_value=True)
            try:
                parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
            except (configparser.Error, OSError):
                continue
            if not parser.has_option(section, key):
                continue
            value = parser.get(section, key)
            return Finding(_state_for(value, check), f"[{section}] {key} = {value}", str(path))
    return None


def check_regex(check: dict) -> Finding | None:
    disabled_re = check.get("disabled_pattern")
    enabled_re = check.get("enabled_pattern")
    for pattern in check["files"]:
        for path in paths.expand_glob(pattern):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if disabled_re:
                match = re.search(disabled_re, text, re.M)
                if match:
                    return Finding(DISABLED, match.group(0).strip()[:120], str(path))
            if enabled_re:
                match = re.search(enabled_re, text, re.M)
                if match:
                    return Finding(ENABLED, match.group(0).strip()[:120], str(path))
    return None


def check_file(check: dict) -> Finding | None:
    """A file that only exists when telemetry is off (or on)."""
    state_when_present = check.get("present", DISABLED)
    for pattern in check["files"]:
        found = paths.expand_glob(pattern)
        if found:
            return Finding(state_when_present, f"{found[0]} exists", str(found[0]))
    return None


def check_command(check: dict, allow_commands: bool) -> Finding | None:
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
    except (OSError, subprocess.SubprocessError):
        return None
    output = (proc.stdout + proc.stderr).strip()
    disabled_re = check.get("disabled_pattern")
    enabled_re = check.get("enabled_pattern")
    if disabled_re and re.search(disabled_re, output, re.I | re.M):
        return Finding(DISABLED, output.splitlines()[0][:120] if output else "", " ".join(argv))
    if enabled_re and re.search(enabled_re, output, re.I | re.M):
        return Finding(ENABLED, output.splitlines()[0][:120] if output else "", " ".join(argv))
    return None


def run_check(check: dict, allow_commands: bool = False) -> Finding | None:
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
