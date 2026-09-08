"""Unit tests: python3 -m unittest discover -s tests"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telescan import checks, paths, report  # noqa: E402
from telescan.catalog import App, Catalog  # noqa: E402
from telescan.cli import main  # noqa: E402
from telescan.scanner import MANUAL, NOT_INSTALLED, scan_app  # noqa: E402


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
        check = {"type": "env", "var": "TELESCAN_TEST", "disabled_when": ["*truthy*"], "enabled_when": ["*falsy*"]}
        with mock.patch.dict(os.environ, {"TELESCAN_TEST": "1"}, clear=False):
            self.assertEqual(checks.run_check(check).state, checks.DISABLED)
        with mock.patch.dict(os.environ, {"TELESCAN_TEST": "0"}, clear=False):
            self.assertEqual(checks.run_check(check).state, checks.ENABLED)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(checks.run_check(check))

    def test_json_check(self) -> None:
        path = self.write("settings.json", '{"telemetry.telemetryLevel": "off"}')
        check = {"type": "json", "key": "telemetry.telemetryLevel", "files": [str(path)],
                 "disabled_when": ["off"], "enabled_when": ["all", "error"]}
        self.assertEqual(checks.run_check(check).state, checks.DISABLED)

    def test_json_check_missing_key_is_none(self) -> None:
        path = self.write("settings.json", '{"unrelated": 1}')
        check = {"type": "json", "key": "telemetry", "files": [str(path)], "disabled_when": ["*falsy*"]}
        self.assertIsNone(checks.run_check(check))

    def test_ini_check(self) -> None:
        path = self.write("config", "[core]\ndisable_usage_reporting = True\n")
        check = {"type": "ini", "section": "core", "key": "disable_usage_reporting", "files": [str(path)],
                 "disabled_when": ["*truthy*"], "enabled_when": ["*falsy*"]}
        self.assertEqual(checks.run_check(check).state, checks.DISABLED)

    def test_regex_check(self) -> None:
        path = self.write("prefs.js", 'user_pref("datareporting.healthreport.uploadEnabled", false);\n')
        check = {"type": "regex", "files": [str(path)],
                 "disabled_pattern": r'uploadEnabled",\s*false', "enabled_pattern": r'uploadEnabled",\s*true'}
        self.assertEqual(checks.run_check(check).state, checks.DISABLED)

    def test_file_check(self) -> None:
        path = self.write(".opt-out", "")
        check = {"type": "file", "files": [str(path)], "present": checks.DISABLED}
        self.assertEqual(checks.run_check(check).state, checks.DISABLED)

    def test_command_check_needs_opt_in(self) -> None:
        check = {"type": "command", "argv": ["echo", "analytics are disabled"], "disabled_pattern": "disabled"}
        self.assertIsNone(checks.run_check(check, allow_commands=False))
        finding = checks.run_check(check, allow_commands=True)
        self.assertEqual(finding.state, checks.DISABLED)

    def test_unknown_check_type(self) -> None:
        with self.assertRaises(ValueError):
            checks.run_check({"type": "nope"})


class TestScanner(TempHome):
    def _app(self, **kwargs) -> App:
        base = dict(id="x", name="X", category="test", platforms=["linux"], what="test app")
        base.update(kwargs)
        return App(**base)

    def test_not_installed(self) -> None:
        app = self._app(detect={"paths": [str(self.home / "missing")]})
        self.assertEqual(scan_app(app).status, NOT_INSTALLED)

    def test_default_state_applies_when_nothing_is_found(self) -> None:
        marker = self.write("marker", "")
        app = self._app(detect={"paths": [str(marker)]}, default_state="on",
                        checks=[{"type": "env", "var": "TELESCAN_ABSENT", "disabled_when": ["1"]}])
        with mock.patch.dict(os.environ, {}, clear=True):
            result = scan_app(app)
        self.assertEqual(result.status, checks.ENABLED)
        self.assertTrue(result.needs_action)

    def test_opt_out_beats_enabled_finding(self) -> None:
        marker = self.write("marker", "")
        config = self.write("config.json", '{"telemetry": true}')
        app = self._app(
            detect={"paths": [str(marker)]},
            default_state="on",
            checks=[
                {"type": "json", "key": "telemetry", "files": [str(config)],
                 "disabled_when": ["*falsy*"], "enabled_when": ["*truthy*"]},
                {"type": "env", "var": "TELESCAN_OPTOUT", "disabled_when": ["1"]},
            ],
        )
        with mock.patch.dict(os.environ, {"TELESCAN_OPTOUT": "1"}, clear=True):
            self.assertEqual(scan_app(app).status, checks.DISABLED)

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
                self.assertTrue(app.detect.get("which") or app.detect.get("paths"),
                                "no way to detect the app")

    def test_checks_are_well_formed(self) -> None:
        required = {
            "env": ["var"], "json": ["key", "files"], "ini": ["key", "files"],
            "regex": ["files"], "file": ["files"], "command": ["argv"], "manual": [],
        }
        for app in self.catalog:
            for check in app.checks:
                with self.subTest(app=app.id, check=check["type"]):
                    self.assertIn(check["type"], required)
                    for field in required[check["type"]]:
                        self.assertIn(field, check)

    def test_duplicate_ids_are_rejected(self) -> None:
        entry = {"id": "dup", "name": "Dup", "category": "test", "platforms": ["linux"], "what": "x"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"apps": [entry, dict(entry)]}, handle)
            name = handle.name
        with self.assertRaises(ValueError):
            Catalog.load(Path(name))

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

    def test_list(self) -> None:
        code, out = self.run_cli(["list", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("vscode", out)

    def test_scan_json_is_valid(self) -> None:
        code, out = self.run_cli(["scan", "--format", "json", "--all"])
        payload = json.loads(out)
        self.assertIn("summary", payload)
        self.assertIn(code, (0, 1))

    def test_show_unknown_app(self) -> None:
        code, out = self.run_cli(["show", "no-such-app"])
        self.assertEqual(code, 2)
        self.assertIn("No catalog entry", out)

    def test_fail_on_never_exits_zero(self) -> None:
        code, _ = self.run_cli(["scan", "--fail-on", "never"])
        self.assertEqual(code, 0)

    def test_default_command_is_scan(self) -> None:
        code, out = self.run_cli(["--format", "json"])
        self.assertIn(code, (0, 1))
        self.assertIn("summary", out)

    def test_markdown_table(self) -> None:
        _, out = self.run_cli(["scan", "--format", "markdown", "--all"])
        self.assertIn("| Status | Application |", out)

    def test_categories(self) -> None:
        code, out = self.run_cli(["categories"])
        self.assertEqual(code, 0)
        self.assertIn("browsers", out)


class TestReport(unittest.TestCase):
    def test_no_color_painter(self) -> None:
        painter = report.Painter(False)
        self.assertEqual(painter.status(checks.ENABLED), "ON")

    def test_color_painter(self) -> None:
        painter = report.Painter(True)
        self.assertIn("ON", painter.status(checks.ENABLED))
        self.assertIn("\033", painter.status(checks.ENABLED))


if __name__ == "__main__":
    unittest.main()
