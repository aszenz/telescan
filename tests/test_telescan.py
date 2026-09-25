"""Unit tests: python3 -m unittest discover -s tests"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telescan import checks, paths, report
from telescan import schema as app_schema
from telescan.catalog import DATA_DIR, App, Catalog
from telescan.cli import main
from telescan.scanner import MANUAL, NOT_INSTALLED, scan_app


def valid_entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "id": "dup",
        "name": "Dup",
        "category": "editors",
        "platforms": ["linux"],
        "what": "Sends usage events somewhere.",
        "detect": {"which": ["dup"]},
        "checks": [{"type": "env", "var": "DUP_TELEMETRY", "disabled_when": ["1"]}],
        "disable": {"steps": ["Set DUP_TELEMETRY=1."]},
        "docs": "https://example.com/telemetry",
        "verification": "confirmed",
        "verified_at": "2026-09-08",
        "scope": ["usage-analytics"],
        "evidence": ["https://example.com/telemetry"],
    }
    entry.update(overrides)
    return entry


def found(check: dict[str, Any], allow_commands: bool = False) -> checks.Finding:
    """Run a check that must find something."""
    finding = checks.run_check(check, allow_commands)
    assert finding is not None, f"no finding for {check}"
    return finding


def entry(catalog: Catalog, app_id: str) -> App:
    """Return a catalog entry that must exist."""
    app = catalog.get(app_id)
    assert app is not None, f"no catalog entry {app_id}"
    return app


def write_entries(entries: list[dict]) -> Path:
    """Write entries to one temporary file and return its path."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"apps": entries}, handle)
        return Path(handle.name)


class TestSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = app_schema.load_schema()

    def test_a_valid_entry_has_no_problems(self) -> None:
        self.assertEqual(app_schema.validate(valid_entry(), self.contract), [])

    def test_unknown_field_is_rejected(self) -> None:
        problems = app_schema.validate(valid_entry(surprise=1), self.contract)
        self.assertIn("unknown field 'surprise'", " ".join(problems))

    def test_check_needs_the_fields_of_its_type(self) -> None:
        entry = valid_entry(checks=[{"type": "json", "key": "a.b"}])
        self.assertIn("is missing 'files'", " ".join(app_schema.validate(entry, self.contract)))

    def test_unknown_check_type_is_rejected(self) -> None:
        entry = valid_entry(checks=[{"type": "carrier-pigeon"}])
        self.assertTrue(app_schema.validate(entry, self.contract))

    def test_manual_check_needs_nothing_else(self) -> None:
        self.assertEqual(app_schema.validate(valid_entry(checks=[{"type": "manual"}]), self.contract), [])

    def test_detect_needs_which_or_paths(self) -> None:
        self.assertTrue(app_schema.validate(valid_entry(detect={}), self.contract))
        self.assertEqual(app_schema.validate(valid_entry(detect={"paths": ["{home}/x"]}), self.contract), [])

    def test_docs_must_be_https(self) -> None:
        self.assertTrue(app_schema.validate(valid_entry(docs="http://example.com"), self.contract))

    def test_platform_must_be_known(self) -> None:
        self.assertTrue(app_schema.validate(valid_entry(platforms=["solaris"]), self.contract))

    def test_duplicate_platform_is_rejected(self) -> None:
        entry = valid_entry(platforms=["linux", "linux"])
        self.assertIn("duplicate", " ".join(app_schema.validate(entry, self.contract)))

    def test_types_are_checked(self) -> None:
        self.assertTrue(app_schema.validate(valid_entry(platforms="linux"), self.contract))
        self.assertTrue(app_schema.validate(valid_entry(name=True), self.contract))


class TempHome(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, relative: str, text: str) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class TestJsonc(unittest.TestCase):
    def test_comments_and_trailing_commas(self) -> None:
        text = """{
          // a line comment
          "telemetry.telemetryLevel": "off", /* block */
          "other": [1, 2,],
        }"""
        data = checks.read_jsonc(text)
        self.assertEqual(data["telemetry.telemetryLevel"], "off")
        self.assertEqual(data["other"], [1, 2])

    def test_comment_inside_string_is_kept(self) -> None:
        data = checks.read_jsonc('{"url": "https://example.com//path"}')
        self.assertEqual(data["url"], "https://example.com//path")

    def test_literal_dotted_key_wins_over_path(self) -> None:
        data = {"a.b": "flat", "a": {"b": "nested"}}
        self.assertEqual(checks.json_lookup(data, "a.b"), (True, "flat"))

    def test_nested_lookup(self) -> None:
        data = {"a": {"b": {"c": 1}}}
        self.assertEqual(checks.json_lookup(data, "a.b.c"), (True, 1))
        self.assertEqual(checks.json_lookup(data, "a.x"), (False, None))


class TestChecks(TempHome):
    def test_env_check(self) -> None:
        check = {
            "type": "env",
            "var": "TELESCAN_TEST",
            "disabled_when": ["*truthy*"],
            "enabled_when": ["*falsy*"],
        }
        with mock.patch.dict(os.environ, {"TELESCAN_TEST": "1"}, clear=False):
            self.assertEqual(found(check).state, checks.DISABLED)
        with mock.patch.dict(os.environ, {"TELESCAN_TEST": "0"}, clear=False):
            self.assertEqual(found(check).state, checks.ENABLED)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(checks.run_check(check))

    def test_json_check(self) -> None:
        path = self.write("settings.json", '{"telemetry.telemetryLevel": "off"}')
        check = {
            "type": "json",
            "key": "telemetry.telemetryLevel",
            "files": [str(path)],
            "disabled_when": ["off"],
            "enabled_when": ["all", "error"],
        }
        self.assertEqual(found(check).state, checks.DISABLED)

    def test_json_check_missing_key_is_none(self) -> None:
        path = self.write("settings.json", '{"unrelated": 1}')
        check = {"type": "json", "key": "telemetry", "files": [str(path)], "disabled_when": ["*falsy*"]}
        self.assertIsNone(checks.run_check(check))

    def test_ini_check(self) -> None:
        path = self.write("config", "[core]\ndisable_usage_reporting = True\n")
        check = {
            "type": "ini",
            "section": "core",
            "key": "disable_usage_reporting",
            "files": [str(path)],
            "disabled_when": ["*truthy*"],
            "enabled_when": ["*falsy*"],
        }
        self.assertEqual(found(check).state, checks.DISABLED)

    def test_regex_check(self) -> None:
        path = self.write("prefs.js", 'user_pref("datareporting.healthreport.uploadEnabled", false);\n')
        check = {
            "type": "regex",
            "files": [str(path)],
            "disabled_pattern": r'uploadEnabled",\s*false',
            "enabled_pattern": r'uploadEnabled",\s*true',
        }
        self.assertEqual(found(check).state, checks.DISABLED)

    def test_file_check(self) -> None:
        path = self.write(".opt-out", "")
        check = {"type": "file", "files": [str(path)], "present": checks.DISABLED}
        self.assertEqual(found(check).state, checks.DISABLED)

    def test_command_check_needs_opt_in(self) -> None:
        check = {
            "type": "command",
            "argv": ["echo", "analytics are disabled"],
            "disabled_pattern": "disabled",
        }
        self.assertIsNone(checks.run_check(check, allow_commands=False))
        finding = found(check, allow_commands=True)
        self.assertEqual(finding.state, checks.DISABLED)

    def test_unknown_check_type(self) -> None:
        with self.assertRaises(ValueError):
            checks.run_check({"type": "nope"})


class TestScanner(TempHome):
    def _app(self, **kwargs: Any) -> App:
        base: dict[str, Any] = {
            "id": "x",
            "name": "X",
            "category": "test",
            "platforms": ["linux"],
            "what": "test app",
        }
        base.update(kwargs)
        return App(**base)

    def test_not_installed(self) -> None:
        app = self._app(detect={"paths": [str(self.home / "missing")]})
        self.assertEqual(scan_app(app).status, NOT_INSTALLED)

    def test_default_state_applies_when_nothing_is_found(self) -> None:
        marker = self.write("marker", "")
        app = self._app(
            detect={"paths": [str(marker)]},
            default_state="on",
            checks=[{"type": "env", "var": "TELESCAN_ABSENT", "disabled_when": ["1"]}],
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            result = scan_app(app)
        self.assertEqual(result.status, checks.ENABLED)
        self.assertTrue(result.needs_action)

    def test_conflicting_settings_are_unknown(self) -> None:
        marker = self.write("marker", "")
        config = self.write("config.json", '{"telemetry": true}')
        app = self._app(
            detect={"paths": [str(marker)]},
            default_state="on",
            checks=[
                {
                    "type": "json",
                    "key": "telemetry",
                    "files": [str(config)],
                    "disabled_when": ["*falsy*"],
                    "enabled_when": ["*truthy*"],
                },
                {"type": "env", "var": "TELESCAN_OPTOUT", "disabled_when": ["1"]},
            ],
        )
        with mock.patch.dict(os.environ, {"TELESCAN_OPTOUT": "1"}, clear=True):
            result = scan_app(app)
        self.assertEqual(result.status, checks.UNKNOWN)
        self.assertIn("conflicting settings", result.reason)

    def test_manual_only(self) -> None:
        marker = self.write("marker", "")
        app = self._app(detect={"paths": [str(marker)]}, checks=[{"type": "manual"}])
        self.assertEqual(scan_app(app).status, MANUAL)


class TestCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = Catalog.load()

    def test_loads_and_validates(self) -> None:
        self.assertGreater(len(self.catalog), 40)

    def test_every_entry_is_usable(self) -> None:
        for app in self.catalog:
            with self.subTest(app=app.id):
                self.assertTrue(app.what, "missing description")
                self.assertTrue(app.platforms)
                self.assertTrue(app.steps, "missing opt-out steps")
                self.assertTrue(app.docs.startswith("https://"))
                self.assertTrue(
                    app.detect.get("which") or app.detect.get("paths"), "no way to detect the app"
                )

    def test_one_file_per_app(self) -> None:
        files = sorted(path.stem for path in DATA_DIR.glob("*.json"))
        self.assertEqual(files, sorted(app.id for app in self.catalog))

    def test_every_entry_matches_the_schema(self) -> None:
        contract = app_schema.load_schema()
        for path in sorted(DATA_DIR.glob("*.json")):
            with self.subTest(entry=path.stem):
                entry = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(app_schema.validate(entry, contract), [])

    def test_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaises(ValueError) as raised:
            Catalog.load(write_entries([valid_entry(), valid_entry()]))
        self.assertIn("duplicate app id", str(raised.exception))

    def test_a_bad_entry_names_its_file(self) -> None:
        entry = valid_entry()
        del entry["docs"]
        with self.assertRaises(ValueError) as raised:
            Catalog.load(write_entries([entry]))
        self.assertIn("is missing 'docs'", str(raised.exception))

    def test_id_must_match_the_file_name(self) -> None:
        directory = Path(tempfile.mkdtemp())
        (directory / "other.json").write_text(json.dumps(valid_entry()), encoding="utf-8")
        with self.assertRaises(ValueError) as raised:
            Catalog.load(directory)
        self.assertIn("does not match the file name", str(raised.exception))

    def test_an_empty_directory_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            Catalog.load(Path(tempfile.mkdtemp()))

    def test_a_single_file_entry_loads(self) -> None:
        directory = Path(tempfile.mkdtemp())
        path = directory / "dup.json"
        path.write_text(json.dumps(valid_entry()), encoding="utf-8")
        self.assertEqual(len(Catalog.load(path)), 1)

    def test_search_and_filter(self) -> None:
        self.assertTrue(self.catalog.search("code"))
        linux = self.catalog.filter(platform="linux")
        self.assertTrue(all("linux" in a.platforms for a in linux))


class TestPaths(unittest.TestCase):
    def test_placeholders_expand(self) -> None:
        expanded = paths.expand("{home}/x")
        self.assertNotIn("{home}", expanded)
        self.assertTrue(expanded.endswith("x"))

    def test_glob_of_missing_path_is_empty(self) -> None:
        self.assertEqual(paths.expand_glob("/definitely/not/here/*.json"), [])


class TestCli(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        import io

        buffer = io.StringIO()
        stdout, sys.stdout = sys.stdout, buffer
        try:
            code = main(argv)
        finally:
            sys.stdout = stdout
        return code, buffer.getvalue()

    def test_all_lists_the_catalog(self) -> None:
        code, out = self.run_cli(["--all", "--no-color", "--fail-on", "never"])
        self.assertEqual(code, 0)
        self.assertIn("Visual Studio Code", out)

    def test_app_names_need_the_scan_command(self) -> None:
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            self.run_cli(["vscode"])

    def test_scan_json_is_valid(self) -> None:
        code, out = self.run_cli(["--format", "json", "--all"])
        payload = json.loads(out)
        self.assertIn("summary", payload)
        self.assertIn(code, (0, 1))

    def test_unknown_app(self) -> None:
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code, _ = self.run_cli(["scan", "no-such-app"])
        self.assertEqual(code, 2)
        self.assertIn("no app named", err.getvalue())

    def test_unknown_category(self) -> None:
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code, _ = self.run_cli(["--category", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("browsers", err.getvalue())

    def test_named_app_shows_how_to_turn_it_off(self) -> None:
        _, out = self.run_cli(["scan", "homebrew", "--no-color", "--fail-on", "never"])
        self.assertIn("HOMEBREW_NO_ANALYTICS", out)
        self.assertIn("https://docs.brew.sh/Analytics", out)

    def test_fail_on_never_exits_zero(self) -> None:
        code, _ = self.run_cli(["--fail-on", "never"])
        self.assertEqual(code, 0)

    def test_default_command_is_scan(self) -> None:
        code, out = self.run_cli(["--format", "json"])
        self.assertIn(code, (0, 1))
        self.assertIn("summary", out)

    def test_markdown_table(self) -> None:
        _, out = self.run_cli(["--format", "markdown", "--all"])
        self.assertIn("| Status | Application |", out)

    def test_validate(self) -> None:
        code, out = self.run_cli(["validate"])
        self.assertEqual(code, 0)
        self.assertIn("app.schema.json", out)

    def test_validate_reports_a_bad_entry(self) -> None:
        path = write_entries([valid_entry(docs="http://example.com")])
        self.assertEqual(main(["validate", str(path)]), 2)


class TestReport(unittest.TestCase):
    def test_no_color_painter(self) -> None:
        painter = report.Painter(False)
        self.assertEqual(painter.status(checks.ENABLED), "Telemetry on")

    def test_color_painter(self) -> None:
        painter = report.Painter(True)
        self.assertIn("Telemetry on", painter.status(checks.ENABLED))
        self.assertIn("\033", painter.status(checks.ENABLED))


if __name__ == "__main__":
    unittest.main()
