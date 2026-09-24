"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__ as VERSION
from . import report
from .catalog import DATA_DIR, Catalog
from .checks import ENABLED, UNKNOWN
from .paths import current_platform
from .scanner import MANUAL, PARTIAL, scan

EPILOG = """\
examples:
  telescan                      scan this machine
  telescan scan vscode npm      scan some apps, with sources and how to turn each off
  telescan scan -v              show sources and how to turn off every hit
  telescan scan --format json   machine readable output, for CI
  telescan scan --export-env    print export lines for your shell profile

exit codes:
  0  nothing to act on
  1  at least one app matched --fail-on (default: enabled)
  2  usage or catalog error
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telescan",
        description="Report which apps on this machine still send telemetry.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"telescan {VERSION}")
    # A subparser without help= is not listed: "validate" is for contributors.
    sub = parser.add_subparsers(dest="command", metavar="{scan}")

    scan_cmd = sub.add_parser(
        "scan",
        help="scan this machine (the default)",
        description="Scan the apps on this machine and report which ones still send telemetry.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan_cmd.add_argument("apps", nargs="*", metavar="app", help="scan only these apps (catalog IDs)")
    scan_cmd.add_argument(
        "-v", "--verbose", action="store_true", help="show sources and how to turn off each hit"
    )
    scan_cmd.add_argument("--category", help="scan only one category")
    scan_cmd.add_argument("--platform", help="linux, macos or windows (default: this machine)")
    scan_cmd.add_argument(
        "--run-commands",
        action="store_true",
        help="let checks run the app itself (for example: brew analytics state)",
    )
    scan_cmd.add_argument("--format", choices=["table", "json", "markdown"], default="table")
    scan_cmd.add_argument(
        "--fail-on",
        choices=["enabled", "unknown", "manual", "never"],
        default="enabled",
        help="which status makes the exit code 1 (default: enabled)",
    )
    scan_cmd.add_argument(
        "--export-env", action="store_true", help="print export lines for your shell profile"
    )
    scan_cmd.add_argument(
        "--export-commands", action="store_true", help="print the opt-out commands (does not run them)"
    )
    scan_cmd.add_argument("--no-color", action="store_true", help="never colorize the output")
    # For tests and catalog work: also report every app that is not installed.
    scan_cmd.add_argument("--all", action="store_true", help=argparse.SUPPRESS)

    validate_cmd = sub.add_parser("validate", description="Check catalog entries against app.schema.json.")
    validate_cmd.add_argument(
        "path", nargs="?", help="a directory of entries or one JSON file (default: the shipped catalog)"
    )
    return parser


def _fail_statuses(fail_on: str) -> set[str]:
    if fail_on == "never":
        return set()
    if fail_on == "enabled":
        return {ENABLED, PARTIAL}
    if fail_on == "unknown":
        return {ENABLED, PARTIAL, UNKNOWN}
    return {ENABLED, PARTIAL, UNKNOWN, MANUAL}


def _resolve_ids(names: list[str], catalog: Catalog) -> list[str] | str:
    """Return catalog IDs for the names, or an error message."""
    ids = []
    for name in names:
        app = catalog.get(name.lower())
        matches = [app] if app else catalog.search(name)
        if len(matches) == 1:
            ids.append(matches[0].id)
        elif matches:
            return f"{name!r} matches several apps: " + ", ".join(m.id for m in matches)
        else:
            return f"no app named {name!r}. See https://github.com/aszenz/telescan/tree/main/telescan/data/apps.d"
    return ids


def cmd_scan(args: argparse.Namespace, catalog: Catalog) -> int:
    if args.category and args.category.lower() not in catalog.categories():
        report.write(
            f"unknown category {args.category!r}. Use one of: {', '.join(catalog.categories())}\n", sys.stderr
        )
        return 2
    ids = _resolve_ids(args.apps, catalog)
    if isinstance(ids, str):
        report.write(ids + "\n", sys.stderr)
        return 2
    results = scan(
        catalog,
        # An app named on the command line is scanned on any platform.
        platform=None if ids else args.platform or current_platform(),
        category=args.category,
        only=ids or None,
        allow_commands=args.run_commands,
        include_missing=args.all or bool(ids),
    )
    painter = report.Painter(not args.no_color and report.use_color(sys.stdout))
    verbose = args.verbose or bool(ids)

    if args.format == "json":
        report.write(report.render_json(results))
    elif args.format == "markdown":
        report.write(report.render_markdown(results))
    else:
        report.write(report.render_table(results, painter, verbose=verbose))
        report.write("\n" + report.render_summary(results, painter))
        if verbose:
            report.write("\n" + report.render_fixes(results, painter, include_absent=bool(ids)))
        elif any(r.needs_action for r in results):
            report.write(
                painter.dim("Run 'telescan scan <app>' or 'telescan scan -v' to see how to turn these off.\n")
            )

    if args.export_env:
        report.write("\n" + report.render_shell_profile(results))
    if args.export_commands:
        report.write("\n" + report.render_commands(results))

    fail = _fail_statuses(args.fail_on)
    return 1 if any(r.status in fail for r in results) else 0


def cmd_validate(args: argparse.Namespace) -> int:
    source = Path(args.path) if args.path else DATA_DIR
    try:
        catalog = Catalog.load(source)
    except (OSError, ValueError) as error:
        report.write(f"invalid: {error}\n", sys.stderr)
        return 2
    plural = "entry" if len(catalog) == 1 else "entries"
    report.write(f"{len(catalog)} {plural} in {source} match app.schema.json.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        # "telescan" and "telescan -v" mean "telescan scan ...".  App names
        # need "scan", so that new commands cannot clash with an app ID.
        argv.insert(0, "scan")
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    try:
        catalog = Catalog.load()
    except (OSError, ValueError) as error:
        report.write(f"catalog error: {error}\n", sys.stderr)
        return 2
    return cmd_scan(args, catalog)
