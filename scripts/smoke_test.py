"""Smoke test for a built wheel.

Run it against the wheel, not the checkout:

    uv build
    uv run --isolated --no-project --with dist/*.whl scripts/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import telescan
from telescan.catalog import DATA_DIR, Catalog
from telescan.cli import main

SOURCE = Path(__file__).resolve().parents[1] / "telescan"


def fail(message: str) -> None:
    sys.exit(f"smoke test failed: {message}")


installed = Path(telescan.__file__).resolve().parent
if installed == SOURCE:
    fail("telescan was imported from the checkout, not from the wheel")

shipped = {path.name for path in DATA_DIR.glob("*.json")}
expected = {path.name for path in (SOURCE / "data" / "apps.d").glob("*.json")}
if shipped != expected:
    fail(f"catalog entries missing from the wheel: {sorted(expected - shipped)}")
if not (DATA_DIR.parent / "app.schema.json").is_file():
    fail("app.schema.json is missing from the wheel")

Catalog.load()
for argv in (["--version"], ["validate"], ["scan", "--all", "--fail-on", "never"]):
    try:
        code = main(argv)
    except SystemExit as exit_:  # argparse exits after --version
        code = exit_.code
    if code:
        fail(f"telescan {' '.join(argv)} exited {code}")

print(f"smoke test passed: telescan {telescan.__version__}, {len(shipped)} catalog entries")
