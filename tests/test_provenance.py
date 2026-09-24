"""Regression tests: provenance, conflicting settings and version ranges."""

from __future__ import annotations

import io
import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from test_telescan import TempHome, entry, found, valid_entry

from telescan import checks, schema, versions
from telescan.catalog import App, Catalog
from telescan.cli import main
from telescan.scanner import MANUAL, scan_app


class TestVersions(unittest.TestCase):
    def test_ranges(self) -> None:
        self.assertTrue(versions.matches("2.1100.0", ">=2.1100.0"))
        self.assertFalse(versions.matches("2.999.9", ">=2.1100.0"))
        self.assertTrue(versions.matches("12.0.1", "<13"))
        self.assertFalse(versions.matches("13", "<13"))
        self.assertTrue(versions.matches("6.19.0", ">=6,<7"))
        self.assertFalse(versions.matches("7.0.0", ">=6,<7"))
        self.assertTrue(versions.matches("2.10", ">2.9"))

    def test_version_text_is_parsed(self) -> None:
        self.assertEqual(versions.parse("2.1100.0 (build abc123)"), (2, 1100, 0))
        self.assertEqual(versions.parse("prisma: 6.1.0-beta"), (6, 1, 0))
        self.assertIsNone(versions.parse("none"))

    def test_bad_range_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            versions.matches("1.0", "~1.0")


class TestSchemaProvenance(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = schema.load_schema()

    def problems(self, **overrides: Any) -> str:
        return " ".join(schema.validate(valid_entry(**overrides), self.contract))

    def test_provenance_is_required(self) -> None:
        entry = valid_entry()
        del entry["verification"]
        self.assertIn("is missing 'verification'", " ".join(schema.validate(entry, self.contract)))

    def test_verification_and_scope_values(self) -> None:
        self.assertTrue(self.problems(verification="probably"))
        self.assertTrue(self.problems(scope=["everything"]))
        self.assertTrue(self.problems(verified_at="last week"))
        self.assertTrue(self.problems(evidence=["http://example.com"]))

    def test_unresolved_entry_must_be_manual(self) -> None:
        self.assertTrue(self.problems(verification="unresolved"))
        self.assertTrue(
            self.problems(verification="unresolved", checks=[{"type": "manual"}], default_state="off")
        )
        self.assertEqual(self.problems(verification="unresolved", checks=[{"type": "manual"}]), "")

    def test_versions_need_a_version_reader(self) -> None:
        self.assertIn("is missing 'version'", self.problems(versions=">=2"))
        self.assertEqual(self.problems(versions=">=2", version={"argv": ["dup", "--version"]}), "")
        self.assertTrue(self.problems(versions="2.x", version={"argv": ["dup", "--version"]}))

    def test_check_versions_need_a_version_reader(self) -> None:
        entry = valid_entry(checks=[{"type": "env", "var": "DUP", "disabled_when": ["1"], "versions": "<2"}])
        path = self._write(entry)
        with self.assertRaises(ValueError) as raised:
            Catalog.load(path)
        self.assertIn('has no "version"', str(raised.exception))

    def _write(self, entry: dict[str, Any]) -> Path:
        from test_telescan import write_entries

        return write_entries([entry])


class TestCatalogPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = Catalog.load()

    def test_no_entry_infers_off_without_a_check(self) -> None:
        for app in self.catalog:
            automatic = [c for c in app.checks if c["type"] != "manual"]
            with self.subTest(app=app.id):
                self.assertTrue(app.checks, "an entry needs at least one check, or a manual check")
                if not automatic:
                    self.assertNotEqual(app.default_state, "off")

    def test_unresolved_entries_are_manual(self) -> None:
        for app in self.catalog:
            if app.verification == "unresolved":
                with self.subTest(app=app.id):
                    self.assertEqual({c["type"] for c in app.checks}, {"manual"})


class TestResolution(TempHome):
    def app(self, **kwargs: Any) -> App:
        marker = self.write("marker", "")
        base: dict[str, Any] = {
            "id": "x",
            "name": "X",
            "category": "test",
            "platforms": ["linux"],
            "what": "test app",
            "detect": {"paths": [str(marker)]},
            "default_state": "on",
        }
        base.update(kwargs)
        return App(**base)

    def test_lower_precedence_opt_out_cannot_establish_off(self) -> None:
        config = self.write("config.json", '{"send_metrics": true}')
        app = self.app(
            checks=[
                {
                    "type": "env",
                    "var": "X_SEND_METRICS",
                    "disabled_when": ["false"],
                    "enabled_when": ["true"],
                },
                {
                    "type": "json",
                    "key": "send_metrics",
                    "files": [str(config)],
                    "disabled_when": [False],
                    "enabled_when": [True],
                },
            ]
        )
        with mock.patch.dict(os.environ, {"X_SEND_METRICS": "false"}, clear=True):
            self.assertEqual(scan_app(app).status, checks.UNKNOWN)

    def test_agreeing_settings_still_resolve(self) -> None:
        config = self.write("config.json", '{"send_metrics": false}')
        app = self.app(
            checks=[
                {"type": "env", "var": "X_SEND_METRICS", "disabled_when": ["false"]},
                {"type": "json", "key": "send_metrics", "files": [str(config)], "disabled_when": [False]},
            ]
        )
        with mock.patch.dict(os.environ, {"X_SEND_METRICS": "false"}, clear=True):
            self.assertEqual(scan_app(app).status, checks.DISABLED)

    def test_exact_values(self) -> None:
        check = {
            "type": "env",
            "var": "X_OFF",
            "exact": True,
            "disabled_when": ["true"],
            "enabled_when": ["*nonempty*"],
        }
        for value, state in (("true", checks.DISABLED), ("TRUE", checks.ENABLED), ("1", checks.ENABLED)):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"X_OFF": value}, clear=True):
                self.assertEqual(found(check).state, state)
        config = self.write("config.json", '{"disable-telemetry": true}')
        json_check = {
            "type": "json",
            "key": "disable-telemetry",
            "files": [str(config)],
            "exact": True,
            "disabled_when": ["true"],
            "enabled_when": ["*nonempty*"],
        }
        self.assertEqual(found(json_check).state, checks.ENABLED)


class TestVersionBoundChecks(TempHome):
    def app(self) -> App:
        marker = self.write("marker", "")
        package = self.home / "node_modules" / "x" / "package.json"
        return App(
            id="x",
            name="X",
            category="test",
            platforms=["linux"],
            what="test app",
            detect={"paths": [str(marker)]},
            default_state="on",
            version={"files": [str(package)], "key": "version"},
            checks=[
                {"type": "env", "var": "X_CHECKPOINT_DISABLE", "versions": "<7", "disabled_when": ["1"]},
                {"type": "manual", "versions": ">=7"},
            ],
        )

    def test_unknown_version_cannot_prove_off(self) -> None:
        with mock.patch.dict(os.environ, {"X_CHECKPOINT_DISABLE": "1"}, clear=True):
            result = scan_app(self.app())
        self.assertEqual(result.status, checks.UNKNOWN)
        self.assertIn("installed version unknown", result.reason)

    def test_unknown_version_does_not_use_the_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            result = scan_app(self.app())
        self.assertEqual(result.status, checks.UNKNOWN)
        self.assertIn("version", result.reason)

    def test_version_in_range_applies(self) -> None:
        self.write("node_modules/x/package.json", '{"version": "6.19.2"}')
        with mock.patch.dict(os.environ, {"X_CHECKPOINT_DISABLE": "1"}, clear=True):
            result = scan_app(self.app())
        self.assertEqual((result.status, result.version), (checks.DISABLED, "6.19.2"))

    def test_version_out_of_range_skips_the_check(self) -> None:
        self.write("node_modules/x/package.json", '{"version": "8.0.0"}')
        with mock.patch.dict(os.environ, {"X_CHECKPOINT_DISABLE": "1"}, clear=True):
            result = scan_app(self.app())
        self.assertEqual(result.status, MANUAL)
        self.assertEqual(result.findings, [])

    def test_version_command_needs_opt_in(self) -> None:
        spec = {"argv": ["x", "--version"]}
        with (
            mock.patch("telescan.checks.shutil.which", return_value="/x"),
            mock.patch(
                "telescan.checks.subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="x 2.1100.3\n", stderr=""),
            ),
        ):
            self.assertIsNone(checks.read_version(spec))
            self.assertEqual(checks.read_version(spec, allow_commands=True), "2.1100.3")

    def test_aws_cdk_cli_telemetry_is_version_bound(self) -> None:
        app = entry(Catalog.load(), "aws-cdk")
        with (
            mock.patch.dict(os.environ, {"CDK_DISABLE_CLI_TELEMETRY": "true"}, clear=True),
            mock.patch("telescan.paths.expand_glob", return_value=[]),
        ):
            old = scan_app(app, assume_installed=True, version="2.1000.0")
            new = scan_app(app, assume_installed=True, version="2.1100.0")
        self.assertEqual([c.name for c in old.components], ["library version reporting"])
        self.assertIn("CLI telemetry", [c.name for c in new.components])


class TestProvenanceOutput(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            code = main(argv)
        return code, output.getvalue()

    def test_json_has_provenance(self) -> None:
        _, out = self.run_cli(["scan", "vscode", "--all", "--format", "json"])
        result = json.loads(out)["results"][0]
        for key in ("verification", "verified_at", "scope", "versions", "evidence", "version"):
            self.assertIn(key, result)
        self.assertTrue(result["evidence"])

    def test_markdown_and_table_have_verification(self) -> None:
        _, out = self.run_cli(["scan", "vscode", "--all", "--format", "markdown"])
        self.assertIn("| Verification | Scope |", out)
        _, out = self.run_cli(["scan", "vscode", "--all", "--no-color", "-v"])
        self.assertIn("qualified", out)
        self.assertIn("audited", out)

    def test_show_has_provenance(self) -> None:
        _, out = self.run_cli(["show", "aws-cdk", "--no-color"])
        for text in ("scope", "verified", "versions", "CLI telemetry >=2.1100.0", "evidence"):
            self.assertIn(text, out)
