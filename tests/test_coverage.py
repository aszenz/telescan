"""Regression coverage for independent controls, profiles and unreadable settings."""
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from telescan import checks, report, schema
from telescan.catalog import App, Catalog
from telescan.cli import main
from telescan.scanner import MANUAL, NOT_INSTALLED, PARTIAL, scan_app
from test_telescan import TempHome, valid_entry


class TestCoverage(TempHome):
    def setUp(self):
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
        patch = mock.patch('telescan.scanner.shutil.which', return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def scan(self, name):
        return scan_app(self.catalog.get(name), assume_installed=True)

    def test_copilot_not_detected_from_vscode_settings(self):
        self.write('.config/Code/User/settings.json', '{"telemetry.telemetryLevel":"off"}')
        self.assertEqual(scan_app(self.catalog.get('github-copilot')).status, NOT_INSTALLED)

    def test_copilot_extension_detected_but_policy_is_manual(self):
        self.write('.vscode/extensions/github.copilot-chat-1.2.3/package.json', '{}')
        self.write('.config/Code/User/settings.json', '{"telemetry.telemetryLevel":"off"}')
        result = scan_app(self.catalog.get('github-copilot'))
        self.assertTrue(result.installed)
        self.assertEqual(result.status, MANUAL)

    def test_claude_usage_only_is_partial(self):
        self.write('.claude/settings.json', '{"env":{"DISABLE_TELEMETRY":"1"}}')
        result = self.scan('claude-code')
        self.assertEqual(result.status, PARTIAL)
        self.assertTrue(result.needs_action)
        self.assertEqual({c.name: c.status for c in result.components}, {'usage':'disabled','errors':'enabled'})

    def test_claude_both_controls_off(self):
        self.write('.claude/settings.json', '{"env":{"DISABLE_TELEMETRY":"1","DISABLE_ERROR_REPORTING":"1"}}')
        self.assertEqual(self.scan('claude-code').status, checks.DISABLED)

    def test_claude_umbrella_settings_recognized(self):
        self.write('.claude/settings.json', '{"env":{"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":"1"}}')
        self.assertEqual(self.scan('claude-code').status, checks.DISABLED)

    def test_zed_metrics_does_not_disable_diagnostics(self):
        self.write('.config/zed/settings.json', '{"telemetry":{"metrics":false,"diagnostics":true}}')
        self.assertEqual(self.scan('zed').status, PARTIAL)
        self.write('.config/zed/settings.json', '{"telemetry":{"metrics":false,"diagnostics":false}}')
        self.assertEqual(self.scan('zed').status, checks.DISABLED)

    def test_firefox_mixed_profiles(self):
        self.write('.mozilla/firefox/a/prefs.js', 'user_pref("datareporting.healthreport.uploadEnabled", false);')
        self.write('.mozilla/firefox/b/prefs.js', 'user_pref("datareporting.healthreport.uploadEnabled", true);')
        result = self.scan('firefox')
        self.assertEqual(result.status, PARTIAL)
        self.assertEqual(len(result.components), 2)
        self.assertEqual(len({f.profile for f in result.findings}), 2)

    def test_firefox_profile_missing_preference_uses_default(self):
        self.write('.mozilla/firefox/a/prefs.js', 'user_pref("datareporting.healthreport.uploadEnabled", false);')
        self.write('.mozilla/firefox/b/prefs.js', '')
        result = self.scan('firefox')
        self.assertEqual(result.status, PARTIAL)
        self.assertIn('catalog default', result.components[1].reason)

    def test_all_profiles_off(self):
        for name in ('a','b'):
            self.write(f'.mozilla/firefox/{name}/prefs.js', 'user_pref("datareporting.healthreport.uploadEnabled", false);')
        self.assertEqual(self.scan('firefox').status, checks.DISABLED)

    def test_invalid_json_is_unknown_instead_of_catalog_default(self):
        path = self.write('.config/zed/settings.json', '{broken')
        result = self.scan('zed')
        self.assertEqual(result.status, checks.UNKNOWN)
        self.assertTrue(result.findings)
        self.assertIn('cannot read JSON', result.reason)
        self.assertEqual(result.findings[0].source, str(path))

    def test_permission_error_is_preserved(self):
        path = self.write('config.json', '{}')
        check = {'type':'json','key':'metrics','files':[str(path)],'disabled_when':[False]}
        with mock.patch.object(Path, 'read_text', side_effect=PermissionError('permission denied')):
            finding = checks.run_check(check)
        self.assertEqual(finding.state, checks.UNKNOWN)
        self.assertIn('permission denied', finding.evidence)

    def test_invalid_ini_is_unknown(self):
        path = self.write('config', 'not an ini file')
        finding = checks.run_check({'type':'ini','files':[str(path)],'key':'metrics','disabled_when':[False]})
        self.assertEqual(finding.state, checks.UNKNOWN)

    def test_unrecognized_setting_does_not_fall_back(self):
        self.write('.config/zed/settings.json', '{"telemetry":{"metrics":"surprise","diagnostics":false}}')
        result = self.scan('zed')
        self.assertEqual(result.status, PARTIAL)
        self.assertEqual(result.components[0].status, checks.UNKNOWN)

    def test_failed_command_output_cannot_report_off(self):
        check = {'type':'command','argv':['example'],'disabled_pattern':'disabled'}
        proc = mock.Mock(returncode=1, stdout='disabled', stderr='')
        with mock.patch('telescan.checks.shutil.which', return_value='/example'), mock.patch('telescan.checks.subprocess.run', return_value=proc):
            finding = checks.run_check(check, allow_commands=True)
        self.assertEqual(finding.state, checks.UNKNOWN)
        self.assertIn('exited 1', finding.evidence)

    def test_every_exported_opt_out_is_recognized(self):
        for app in self.catalog:
            if not app.env:
                continue
            with self.subTest(app=app.id), mock.patch.dict(os.environ, app.env, clear=True), mock.patch('telescan.paths.expand_glob', return_value=[]):
                result = scan_app(app, assume_installed=True)
                self.assertTrue(any(f.source == 'environment' and f.state == checks.DISABLED for f in result.findings))

    def test_nonempty_match_is_not_boolean_parsing(self):
        check = {'type':'env','var':'PRESENT_DISABLE','disabled_when':['*nonempty*']}
        for value in ('0','false','anything'):
            with self.subTest(value=value), mock.patch.dict(os.environ, {'PRESENT_DISABLE':value}, clear=True):
                self.assertEqual(checks.run_check(check).state, checks.DISABLED)

    def test_corrected_catalog_controls(self):
        self.assertEqual(self.catalog.get('gemini-cli').checks[0]['key'], 'privacy.usageStatisticsEnabled')
        self.assertEqual(self.catalog.get('cordova').default_state, 'off')
        self.assertIsNone(self.catalog.get('docker-cli-hints'))

    def test_partial_in_all_formats_and_exit_codes(self):
        self.write('.claude/settings.json', '{"env":{"DISABLE_TELEMETRY":"1"}}')
        for fmt in ('table','json','markdown'):
            with self.subTest(format=fmt), mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(main(['scan','claude-code','--format',fmt,'--no-color']),1)
                self.assertIn('partial', output.getvalue().lower())
                if fmt == 'json':
                    data = json.loads(output.getvalue())
                    self.assertEqual(data['summary']['partial'],1)
                    self.assertEqual(len(data['results'][0]['components']),2)
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main(['scan','claude-code','--fail-on','never']),0)

    def test_profile_json_and_ini_checks_read_every_file(self):
        for kind in ('json','ini'):
            for index, val in enumerate(('false','true')):
                self.write(f'{kind}/{index}', '{"metrics":'+val+'}' if kind == 'json' else '[DEFAULT]\nmetrics='+val)
            check = {'type':kind,'files':[str(self.home / kind / '*')],'key':'metrics','profiles':True,'disabled_when':['*falsy*'],'enabled_when':['*truthy*']}
            self.assertEqual([f.state for f in checks.run_checks(check)], [checks.DISABLED,checks.ENABLED])

    def test_profiles_schema_rejects_non_file_checks(self):
        entry = valid_entry(checks=[{'type':'env','var':'EXAMPLE','disabled_when':['1'],'profiles':True}])
        self.assertTrue(schema.validate(entry,schema.load_schema()))
