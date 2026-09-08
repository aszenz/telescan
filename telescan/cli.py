"""Command line interface."""

from __future__ import annotations

import argparse
import sys

from . import report
from .catalog import Catalog
from .checks import DISABLED, ENABLED, UNKNOWN
from .paths import current_platform
from .scanner import MANUAL, scan, scan_app

VERSION = "1.0.0"

EPILOG = """\
examples:
  telescan scan                     scan this machine
  telescan scan --fix               scan, then print how to turn each one off
  telescan scan --all               include applications that are not installed
  telescan scan --format json       machine readable output for CI
  telescan scan --export-env        print the export lines for your shell profile
  telescan list --category browsers list the catalog entries of one category
  telescan show homebrew            show one entry in full

exit codes:
  0  nothing to act on
  1  at least one application matched --fail-on (default: enabled)
  2  usage or catalog error
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telescan",
        description="Scan the applications on this machine and report which ones still send telemetry.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"telescan {VERSION}")
    sub = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--category", help="limit to one category")
    common.add_argument("--platform", help="linux, macos or windows (default: this machine)")
    common.add_argument("--no-color", action="store_true", help="never colorize the output")

    scan_cmd = sub.add_parser("scan", parents=[common], help="scan this machine (default command)")
    scan_cmd.add_argument("apps", nargs="*", help="scan only these catalog IDs")
    scan_cmd.add_argument("-a", "--all", action="store_true", help="also report applications that are not installed")
    scan_cmd.add_argument("-v", "--verbose", action="store_true", help="show the file or variable each result came from")
    scan_cmd.add_argument("--fix", action="store_true", help="print the opt-out steps for every hit")
    scan_cmd.add_argument("--export-env", action="store_true", help="print export lines for your shell profile")
    scan_cmd.add_argument("--export-commands", action="store_true", help="print the opt-out commands (does not run them)")
    scan_cmd.add_argument("--run-commands", action="store_true",
                          help="let checks call the application itself (for example: brew analytics state)")
    scan_cmd.add_argument("--format", choices=["table", "json", "markdown"], default="table")
    scan_cmd.add_argument("--fail-on", choices=["enabled", "unknown", "manual", "never"], default="enabled",
                          help="which status makes the exit code 1 (default: enabled)")

    list_cmd = sub.add_parser("list", parents=[common], help="list the catalog")
    list_cmd.add_argument("term", nargs="?", help="filter by name, ID or category")
    list_cmd.add_argument("--format", choices=["table", "json", "markdown"], default="table")

    show_cmd = sub.add_parser("show", parents=[common], help="show one catalog entry in full")
    show_cmd.add_argument("app", help="catalog ID, for example: vscode")

    sub.add_parser("categories", help="list the categories")
    return parser


def _fail_statuses(fail_on: str) -> set[str]:
    if fail_on == "never":
        return set()
    if fail_on == "enabled":
        return {ENABLED}
    if fail_on == "unknown":
        return {ENABLED, UNKNOWN}
    return {ENABLED, UNKNOWN, MANUAL}


def cmd_scan(args: argparse.Namespace, catalog: Catalog) -> int:
    platform = args.platform or current_platform()
    results = scan(
        catalog,
        platform=platform,
        category=args.category,
        only=args.apps or None,
        allow_commands=args.run_commands,
        include_missing=args.all,
    )
    painter = report.Painter(not args.no_color and report.use_color(sys.stdout))

    if args.format == "json":
        report.write(report.render_json(results))
    elif args.format == "markdown":
        report.write(report.render_markdown(results))
    else:
        report.write(report.render_table(results, painter, verbose=args.verbose))
        report.write("\n" + report.render_summary(results, painter))
        if args.fix:
            report.write("\n" + report.render_fixes(results, painter))
        elif any(r.needs_action for r in results):
            report.write(painter.dim("Run with --fix to see how to turn these off.\n"))

    if args.export_env:
        report.write("\n" + report.render_shell_profile(results))
    if args.export_commands:
        report.write("\n" + report.render_commands(results))

    fail = _fail_statuses(args.fail_on)
    return 1 if any(r.status in fail for r in results) else 0


def cmd_list(args: argparse.Namespace, catalog: Catalog) -> int:
    apps = catalog.search(args.term) if args.term else list(catalog)
    if args.category:
        apps = [a for a in apps if a.category.lower() == args.category.lower()]
    if args.platform:
        apps = [a for a in apps if args.platform in a.platforms]
    if not apps:
        report.write("No catalog entry matched.\n")
        return 0
    painter = report.Painter(not args.no_color and report.use_color(sys.stdout))
    if args.format == "json":
        report.write(report.render_json([scan_app(a, assume_installed=True) for a in apps]))
        return 0
    width = max(len(a.id) for a in apps)
    for app in apps:
        default = f"default: telemetry {app.default_state}"
        report.write(f"{app.id.ljust(width)}  {app.name}\n")
        report.write(f"{' ' * width}  {painter.dim(app.category + ' · ' + default)}\n")
    report.write(f"\n{len(apps)} of {len(catalog)} catalog entries.\n")
    return 0


def cmd_show(args: argparse.Namespace, catalog: Catalog) -> int:
    app = catalog.get(args.app)
    if app is None:
        matches = catalog.search(args.app)
        if len(matches) == 1:
            app = matches[0]
        elif matches:
            report.write("Did you mean: " + ", ".join(m.id for m in matches) + "\n")
            return 2
        else:
            report.write(f"No catalog entry named {args.app!r}. Try: telescan list\n")
            return 2
    painter = report.Painter(not args.no_color and report.use_color(sys.stdout))
    report.write(report.render_app_details(app, painter))
    result = scan_app(app)
    report.write(f"  on this machine: {painter.status(result.status)} ({result.reason})\n")
    return 0


def cmd_categories(catalog: Catalog) -> int:
    for category in catalog.categories():
        count = len(catalog.filter(category=category))
        report.write(f"{category.ljust(20)} {count}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    known = {"scan", "list", "show", "categories"}
    global_flags = {"-h", "--help", "--version"}
    if not argv or (argv[0] not in known and argv[0] not in global_flags):
        # "telescan" and "telescan --format json" both mean "telescan scan ...".
        argv.insert(0, "scan")
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["scan"])

    try:
        catalog = Catalog.load()
    except (OSError, ValueError) as error:
        report.write(f"catalog error: {error}\n", sys.stderr)
        return 2

    if args.command == "scan":
        return cmd_scan(args, catalog)
    if args.command == "list":
        return cmd_list(args, catalog)
    if args.command == "show":
        return cmd_show(args, catalog)
    if args.command == "categories":
        return cmd_categories(catalog)
    parser.print_help()
    return 2
