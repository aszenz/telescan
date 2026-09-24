"""Regression coverage for independent controls, profiles and unreadable settings."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest import mock

from test_telescan import TempHome, entry, found, valid_entry

from telescan import checks, schema
from telescan.catalog import Catalog
from telescan.cli import main
from telescan.scanner import MANUAL, NOT_INSTALLED, PARTIAL, Result, scan_app


class TestCoverage(TempHome):
    def setUp(self) -> None:
        super().setUp()
        self.catalog = Catalog.load()
        self.env = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "USERPROFILE": str(self.home),
                "XDG_CONFIG_HOME": str(self.home / ".config"),
                "XDG_DATA_HOME": str(self.home / ".local" / "share"),
                "APPDATA": str(self.home / ".config"),
                "LOCALAPPDATA": str(self.home / ".local" / "share"),
                "PROGRAMDATA": str(self.home / ".programdata"),
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        patch = mock.patch("telescan.scanner.shutil.which", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def scan(self, name: str) -> Result:
        return scan_app(entry(self.catalog, name), assume_installed=True)

    def test_copilot_not_detected_from_vscode_settings(self) -> None:
        self.write(".config/Code/User/settings.json", '{"telemetry.telemetryLevel":"off"}')
        self.assertEqual(scan_app(entry(self.catalog, "github-copilot")).status, NOT_INSTALLED)

    def test_copilot_extension_detected_but_policy_is_manual(self) -> None:
        self.write(".vscode/extensions/github.copilot-chat-1.2.3/package.json", "{}")
        self.write(".config/Code/User/settings.json", '{"telemetry.telemetryLevel":"off"}')
        result = scan_app(entry(self.catalog, "github-copilot"))
        self.assertTrue(result.installed)
        self.assertEqual(result.status, MANUAL)

    def test_claude_usage_only_is_partial(self) -> None:
        self.write(".claude/settings.json", '{"env":{"DISABLE_TELEMETRY":"1"}}')
        result = self.scan("claude-code")
        self.assertEqual(result.status, PARTIAL)
        self.assertTrue(result.needs_action)
        self.assertEqual(
            {c.name: c.status for c in result.components}, {"usage": "disabled", "errors": "enabled"}
        )

    def test_claude_both_controls_off(self) -> None:
        self.write(".claude/settings.json", '{"env":{"DISABLE_TELEMETRY":"1","DISABLE_ERROR_REPORTING":"1"}}')
        self.assertEqual(self.scan("claude-code").status, checks.DISABLED)

    def test_claude_umbrella_settings_recognized(self) -> None:
        self.write(".claude/settings.json", '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":"1"}}')
        self.assertEqual(self.scan("claude-code").status, checks.DISABLED)

    def test_zed_metrics_does_not_disable_diagnostics(self) -> None:
        self.write(".config/zed/settings.json", '{"telemetry":{"metrics":false,"diagnostics":true}}')
        self.assertEqual(self.scan("zed").status, PARTIAL)
        self.write(".config/zed/settings.json", '{"telemetry":{"metrics":false,"diagnostics":false}}')
        self.assertEqual(self.scan("zed").status, checks.DISABLED)

    def test_firefox_mixed_profiles(self) -> None:
        self.write(
            ".mozilla/firefox/a/prefs.js", 'user_pref("datareporting.healthreport.uploadEnabled", false);'
        )
        self.write(
            ".mozilla/firefox/b/prefs.js", 'user_pref("datareporting.healthreport.uploadEnabled", true);'
        )
        result = self.scan("firefox")
        self.assertEqual(result.status, PARTIAL)
        self.assertEqual(len(result.components), 2)
        self.assertEqual(len({f.profile for f in result.findings}), 2)

    def test_firefox_profile_missing_preference_uses_default(self) -> None:
        self.write(
            ".mozilla/firefox/a/prefs.js", 'user_pref("datareporting.healthreport.uploadEnabled", false);'
        )
        self.write(".mozilla/firefox/b/prefs.js", "")
        result = self.scan("firefox")
        self.assertEqual(result.status, PARTIAL)
        self.assertIn("catalog default", result.components[1].reason)

    def test_all_profiles_off(self) -> None:
        for name in ("a", "b"):
            self.write(
                f".mozilla/firefox/{name}/prefs.js",
                'user_pref("datareporting.healthreport.uploadEnabled", false);',
            )
        self.assertEqual(self.scan("firefox").status, checks.DISABLED)

    def test_invalid_json_is_unknown_instead_of_catalog_default(self) -> None:
        path = self.write(".config/zed/settings.json", "{broken")
        result = self.scan("zed")
        self.assertEqual(result.status, checks.UNKNOWN)
        self.assertTrue(result.findings)
        self.assertIn("cannot read JSON", result.reason)
        self.assertEqual(result.findings[0].source, str(path))

    def test_permission_error_is_preserved(self) -> None:
        path = self.write("config.json", "{}")
        check = {"type": "json", "key": "metrics", "files": [str(path)], "disabled_when": [False]}
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("permission denied")):
            finding = found(check)
        self.assertEqual(finding.state, checks.UNKNOWN)
        self.assertIn("permission denied", finding.evidence)

    def test_invalid_ini_is_unknown(self) -> None:
        path = self.write("config", "not an ini file")
        finding = found({"type": "ini", "files": [str(path)], "key": "metrics", "disabled_when": [False]})
        self.assertEqual(finding.state, checks.UNKNOWN)

    def test_unrecognized_setting_does_not_fall_back(self) -> None:
        self.write(".config/zed/settings.json", '{"telemetry":{"metrics":"surprise","diagnostics":false}}')
        result = self.scan("zed")
        self.assertEqual(result.status, PARTIAL)
        self.assertEqual(result.components[0].status, checks.UNKNOWN)

    def test_failed_command_output_cannot_report_off(self) -> None:
        check = {"type": "command", "argv": ["example"], "disabled_pattern": "disabled"}
        proc = mock.Mock(returncode=1, stdout="disabled", stderr="")
        with (
            mock.patch("telescan.checks.shutil.which", return_value="/example"),
            mock.patch("telescan.checks.subprocess.run", return_value=proc),
        ):
            finding = found(check, allow_commands=True)
        self.assertEqual(finding.state, checks.UNKNOWN)
        self.assertIn("exited 1", finding.evidence)

    def test_every_exported_opt_out_is_recognized(self) -> None:
        for app in self.catalog:
            if not app.env:
                continue
            with (
                self.subTest(app=app.id),
                mock.patch.dict(os.environ, app.env, clear=True),
                mock.patch("telescan.paths.expand_glob", return_value=[]),
            ):
                # A version inside every range, so that version-bound controls apply.
                result = scan_app(app, assume_installed=True, version="999999")
                self.assertTrue(
                    any(f.source == "environment" and f.state == checks.DISABLED for f in result.findings)
                )

    def test_nonempty_match_is_not_boolean_parsing(self) -> None:
        check = {"type": "env", "var": "PRESENT_DISABLE", "disabled_when": ["*nonempty*"]}
        for value in ("0", "false", "anything"):
            with (
                self.subTest(value=value),
                mock.patch.dict(os.environ, {"PRESENT_DISABLE": value}, clear=True),
            ):
                self.assertEqual(found(check).state, checks.DISABLED)

    def test_npm_update_notifier_is_on_by_default(self) -> None:
        self.assertEqual(self.scan("npm").status, checks.ENABLED)
        self.write(".npmrc", "registry=https://registry.npmjs.org/\nupdate-notifier=false\n")
        self.assertEqual(self.scan("npm").status, checks.DISABLED)

    def test_verbose_output_lists_a_shared_setting_once(self) -> None:
        self.write(".claude/settings.json", '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":"1"}}')
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            main(["scan", "claude-code", "-v", "--no-color"])
        details = [line for line in output.getvalue().splitlines() if "· " in line]
        self.assertEqual(sum("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" in line for line in details), 1)

    def components(self, name: str) -> dict[str, str]:
        return {c.name: c.status for c in self.scan(name).components}

    def test_codex_config_toml(self) -> None:
        self.write(
            ".codex/config.toml",
            'model = "x"\ncheck_for_update_on_startup = false\n\n[analytics]\nenabled = false\n',
        )
        self.assertEqual(self.scan("codex").status, checks.DISABLED)
        self.write(".codex/config.toml", "[analytics]\nenabled = false\n")
        self.assertEqual(self.components("codex")["update-check"], checks.ENABLED)

    def test_gh_config_yml(self) -> None:
        self.write(".config/gh/config.yml", "git_protocol: ssh\ntelemetry: disabled\n")
        self.assertEqual(self.components("gh")["telemetry"], checks.DISABLED)
        self.write(".config/gh/config.yml", "telemetry: enabled\n")
        self.assertEqual(self.components("gh")["telemetry"], checks.ENABLED)

    def test_ngrok_update_check(self) -> None:
        self.write(".config/ngrok/ngrok.yml", 'version: "3"\nagent:\n  update_check: false\n')
        self.assertEqual(self.scan("ngrok").status, checks.DISABLED)

    def test_sentry_cli_rc(self) -> None:
        self.write(".sentryclirc", "[update]\ndisable_check = true\n")
        self.assertEqual(self.scan("sentry-cli").status, checks.DISABLED)

    def test_vlc_metadata_network_access(self) -> None:
        self.write(".config/vlc/vlcrc", "[core]\nmetadata-network-access=1\n")
        self.assertEqual(self.components("vlc")["metadata network access"], checks.ENABLED)
        self.write(".config/vlc/vlcrc", "[core]\nmetadata-network-access=0\n")
        self.assertEqual(self.components("vlc")["metadata network access"], checks.DISABLED)

    def test_kitty_last_setting_wins(self) -> None:
        self.write(".config/kitty/kitty.conf", "update_check_interval 24\nupdate_check_interval 0\n")
        self.assertEqual(self.scan("kitty").status, checks.DISABLED)
        self.write(".config/kitty/kitty.conf", "update_check_interval 0\nupdate_check_interval 24\n")
        self.assertEqual(self.scan("kitty").status, checks.ENABLED)

    def test_libreoffice_registry(self) -> None:
        self.write(
            ".config/libreoffice/4/user/registrymodifications.xcu",
            '<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="CrashReport" oor:op="fuse">'
            "<value>false</value></prop></item>\n"
            "<item oor:path=\"/org.openoffice.Office.Jobs/Jobs/org.openoffice.Office.Jobs:Job['UpdateCheck']"
            '/Arguments"><prop oor:name="AutoCheckEnabled" oor:op="fuse">'
            "<value>false</value></prop></item>\n",
        )
        self.assertEqual(self.scan("libreoffice").status, checks.DISABLED)

    def test_obsidian_updates(self) -> None:
        self.write(".config/obsidian/obsidian.json", '{"vaults": {}, "updateDisabled": true}')
        self.assertEqual(self.scan("obsidian").status, checks.DISABLED)
        self.write(".config/obsidian/obsidian.json", '{"vaults": {}}')
        self.assertEqual(self.scan("obsidian").status, checks.ENABLED)

    def test_corrected_catalog_controls(self) -> None:
        self.assertEqual(entry(self.catalog, "gemini-cli").checks[0]["key"], "privacy.usageStatisticsEnabled")
        self.assertEqual(entry(self.catalog, "cordova").default_state, "off")
        self.assertIsNone(self.catalog.get("docker-cli-hints"))

    def test_partial_in_all_formats_and_exit_codes(self) -> None:
        self.write(".claude/settings.json", '{"env":{"DISABLE_TELEMETRY":"1"}}')
        for fmt in ("table", "json", "markdown"):
            with self.subTest(format=fmt), mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(["scan", "claude-code", "--format", fmt, "--no-color"]), 1)
                self.assertIn("partial", output.getvalue().lower())
                if fmt == "json":
                    data = json.loads(output.getvalue())
                    self.assertEqual(data["summary"]["partial"], 1)
                    self.assertEqual(len(data["results"][0]["components"]), 2)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(["scan", "claude-code", "--fail-on", "never"]), 0)

    def test_profile_json_and_ini_checks_read_every_file(self) -> None:
        for kind in ("json", "ini"):
            for index, val in enumerate(("false", "true")):
                self.write(
                    f"{kind}/{index}",
                    '{"metrics":' + val + "}" if kind == "json" else "[DEFAULT]\nmetrics=" + val,
                )
            check = {
                "type": kind,
                "files": [str(self.home / kind / "*")],
                "key": "metrics",
                "profiles": True,
                "disabled_when": ["*falsy*"],
                "enabled_when": ["*truthy*"],
            }
            self.assertEqual([f.state for f in checks.run_checks(check)], [checks.DISABLED, checks.ENABLED])

    def test_profiles_schema_rejects_non_file_checks(self) -> None:
        entry = valid_entry(
            checks=[{"type": "env", "var": "EXAMPLE", "disabled_when": ["1"], "profiles": True}]
        )
        self.assertTrue(schema.validate(entry, schema.load_schema()))
