"""Path expansion for catalog entries.

Catalog paths use placeholders so that one entry works on all platforms:

    {home}     user home directory
    {config}   XDG config dir  (~/.config, %APPDATA% on Windows)
    {data}     XDG data dir    (~/.local/share, %APPDATA% on Windows)
    {appsupport} macOS Application Support dir
    {appdata}  Windows %APPDATA%
    {localappdata} Windows %LOCALAPPDATA%
    {programdata}  Windows %PROGRAMDATA%

A path can also contain glob wildcards.  Expansion never touches the disk;
use `expand_glob` to get the files that exist.
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"


def current_platform() -> str:
    if WINDOWS:
        return "windows"
    if MACOS:
        return "macos"
    return "linux"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def placeholders() -> dict[str, str]:
    home = _home()
    if WINDOWS:
        appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
        local = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        return {
            "home": str(home),
            "config": appdata,
            "data": appdata,
            "appsupport": appdata,
            "appdata": appdata,
            "localappdata": local,
            "programdata": os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
        }
    config = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
    data = os.environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    appsupport = str(home / "Library" / "Application Support")
    if MACOS:
        # Many cross platform tools still use ~/.config on macOS, so keep
        # {config} as is and expose the native dir separately.
        pass
    return {
        "home": str(home),
        "config": config,
        "data": data,
        "appsupport": appsupport,
        "appdata": config,
        "localappdata": data,
        "programdata": "/etc",
    }


def expand(path: str) -> str:
    """Replace placeholders and environment variables in `path`."""
    out = path
    for key, value in placeholders().items():
        out = out.replace("{%s}" % key, value)
    out = os.path.expandvars(out)
    return os.path.expanduser(out)


def expand_glob(path: str) -> list[Path]:
    """Return the existing files or directories that `path` points to."""
    expanded = expand(path)
    if any(ch in expanded for ch in "*?["):
        return sorted(Path(p) for p in glob.glob(expanded))
    p = Path(expanded)
    return [p] if p.exists() else []
