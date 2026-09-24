# Contributing to telescan

Thank you for helping. Most contributions are catalog changes: add an
application, or fix a setting. Each application is one JSON file, so you do
not need to change Python code.

- [Report a problem without code](#report-a-problem-without-code)
- [Add or update an application](#add-or-update-an-application)
- [Evidence rules](#evidence-rules)
- [Entry reference](#entry-reference)
- [How the scanner decides](#how-the-scanner-decides)
- [Test your change](#test-your-change)
- [Change the code](#change-the-code)
- [Catalog review policy](#catalog-review-policy)
- [Releases](#releases)

## Report a problem without code

[Open an issue](https://github.com/aszenz/telescan/issues/new) and include:

- **Add an application**: the application, and a link to its telemetry
  documentation.
- **Update an application**: what changed, and a link that shows it.
- **Wrong result**: what telescan reported, what you expected, and the
  output of `telescan scan <id>`. Remove private paths first.

## Add or update an application

1. Fork the repository and clone it. You need Python 3.9 or later and nothing
   else.
2. Copy an entry that is similar to your application, or start from the
   template below. Save it as `telescan/data/apps.d/<id>.json`. The file name
   must be the same as the `id`.
3. Add the evidence (see [Evidence rules](#evidence-rules)).
4. Run `./telescan.py validate telescan/data/apps.d/<id>.json`.
5. Run `./telescan.py scan <id>` and look at the result.
6. Add a test when you add or change a check (see
   [Test your change](#test-your-change)).
7. Open a pull request. List the evidence, versions, platforms and scope.

Keep one application per pull request. Then each review stays small.

### Template

```json
{
  "$schema": "../app.schema.json",
  "id": "myapp",
  "name": "My App",
  "category": "frameworks",
  "platforms": ["linux", "macos", "windows"],
  "what": "What the app collects, in one or two sentences.",
  "default_state": "on",
  "detect": { "which": ["myapp"], "paths": ["{config}/myapp"] },
  "checks": [
    { "type": "env", "var": "MYAPP_TELEMETRY_DISABLED",
      "disabled_when": ["*truthy*"], "enabled_when": ["*falsy*"] }
  ],
  "disable": {
    "steps": ["Run: myapp telemetry disable"],
    "commands": ["myapp telemetry disable"],
    "env": { "MYAPP_TELEMETRY_DISABLED": "1" }
  },
  "docs": "https://example.com/telemetry",
  "verification": "confirmed",
  "verified_at": "2026-09-24",
  "scope": ["usage-analytics"],
  "evidence": ["https://example.com/telemetry"]
}
```

`$schema` points to `telescan/data/app.schema.json`. Most editors then give
completion and show errors while you type.

## Evidence rules

telescan is only useful when its results are correct. A false `OFF` is the
worst possible result.

- **Link primary evidence**: the vendor documentation, or the upstream source
  that reads the setting. Link to a tag or a commit when possible, because
  branches move. Blog posts and forum answers are not enough by themselves.
- **Copy the exact values.** When the source tests `value === "true"`, use
  `"exact": true` and `["true"]`, not `*truthy*`. When it tests "the variable
  is set", use `*nonempty*`.
- **Say which versions.** When a control exists only in some versions, add
  `versions` (see below).
- **Say what the control covers.** A crash-report switch does not stop usage
  analytics. List only what the check covers in `scope`. Use a separate
  `component` for each independent control.
- **Never assume "no telemetry".** If you find no documented opt-out, the
  entry is `unresolved`, it uses a `manual` check, and its `default_state` is
  `unknown`. The schema enforces this.
- **Set `verified_at`** to the date on which you read the evidence.

Choose the verification level:

| Level | Use when |
| --- | --- |
| `confirmed` | Documentation or source states the control, its values and where it is stored. |
| `qualified` | The control is real, but part of it is not verified (values, paths, precedence, versions), or it covers less than it seems to. Say what in `what` or `disable.steps`. |
| `unresolved` | You could not verify a control. Use only `manual` checks. |

## Entry reference

The schema in `telescan/data/app.schema.json` is the full contract. This is a
summary.

| Field | Meaning |
| --- | --- |
| `id` | Lowercase ID, and the file name. |
| `category` | One of `ai-tools`, `browsers`, `cloud-cli`, `desktop-apps`, `editors`, `frameworks`, `operating-system`, `package-managers`. |
| `platforms` | Some of `linux`, `macos`, `windows`. |
| `default_state` | `on`, `off` or `unknown`: the state of a fresh install. |
| `detect` | `which` (executables on `PATH`) and/or `paths` (files or directories). |
| `checks` | Where to read the setting. See below. |
| `disable` | `steps` (required), `commands`, and `env` (variables that turn telemetry off). |
| `docs` | The main vendor page for the opt-out. |
| `verification`, `verified_at`, `evidence` | See [Evidence rules](#evidence-rules). |
| `scope` | Some of `usage-analytics`, `crash-reporting`, `update-checks`, `ai-data`, `service-data`, `plugins`. |
| `versions`, `version` | Optional. The versions the entry applies to, and how to read the installed version. |

### Check types

| Type | Reads | Needs |
| --- | --- | --- |
| `env` | An environment variable. | `var` |
| `json` | A key in a JSON or JSONC file. A dot in `key` is a path separator. | `key`, `files` |
| `ini` | A key in an INI file. | `key`, `files`, optional `section` |
| `regex` | A pattern in a text file. | `files`, `disabled_pattern` and/or `enabled_pattern` |
| `file` | A marker file. `present` is the state when it exists. | `files` |
| `command` | The output of a command. Runs only with `--run-commands`. | `argv`, patterns |
| `manual` | Nothing. There is no local setting to read. | nothing |

`env`, `json` and `ini` checks need `disabled_when` and/or `enabled_when`.
These lists hold literal values or these words:

- `*truthy*`: `1`, `true`, `yes`, `on`, `enabled` (case is ignored)
- `*falsy*`: `0`, `false`, `no`, `off`, `disabled`, `none`
- `*nonempty*`: any value that is not empty

A value that matches neither list gives `UNKNOWN`.

Optional fields on a check:

- `component`: the name of an independent control, for example `metrics` or
  `crash reports`. Every component must be off before the application is
  `OFF`.
- `profiles: true`: read every matching file as a separate profile (for
  example browser profiles). Without it, `files` is in order of precedence,
  and the first file that has the key wins.
- `default_state`: the fallback for this component when nothing is found.
- `exact: true`: compare as case-sensitive strings. A JSON boolean does not
  match the string `"true"`.
- `versions`: a range such as `>=2.1100.0` or `>=6,<7`. Out of range, the
  check is skipped. When the version is unknown, the check cannot give `ON` or
  `OFF`. The entry must then have `version`: a JSON file and key (for example
  `node_modules/myapp/package.json` and `version`), and/or an `argv` command.

### Paths

Paths can use placeholders, so one entry works on every platform. Globs are
allowed.

| Placeholder | Linux and macOS | Windows |
| --- | --- | --- |
| `{home}` | `~` | `~` |
| `{config}` | `$XDG_CONFIG_HOME` or `~/.config` | `%APPDATA%` |
| `{data}` | `$XDG_DATA_HOME` or `~/.local/share` | `%APPDATA%` |
| `{appsupport}` | `~/Library/Application Support` | `%APPDATA%` |
| `{appdata}` | same as `{config}` | `%APPDATA%` |
| `{localappdata}` | same as `{data}` | `%LOCALAPPDATA%` |
| `{programdata}` | `/etc` | `%PROGRAMDATA%` |

See `telescan/paths.py` for the exact rules.

## How the scanner decides

1. `detect` finds the application. If it is not found, the result is `ABSENT`.
2. If the entry or a check has `versions`, telescan reads the installed
   version. A check outside its range is skipped. When the version is
   unknown, a version-bound check cannot give `ON` or `OFF`, and its
   component is `UNKNOWN`. An entry whose `versions` do not match is `UNKNOWN`.
3. Each check gives `disabled`, `enabled` or `unknown`, or finds nothing.
4. The checks are grouped by `component`, and by profile for `profiles`
   checks. For each group:
   - an unreadable file or an unrecognized value gives `UNKNOWN`
   - settings that conflict (one off, one on) give `UNKNOWN`. telescan does
     not yet model the precedence of each application, so a lower-precedence
     opt-out must never give `OFF`.
   - otherwise the setting that was found wins
   - if nothing was found: `MANUAL` for manual checks, otherwise the
     `default_state`
5. The groups are combined:
   - all groups agree: that state
   - some off, and others not: `PARTIAL`
   - none off, and some on: `ON`
   - otherwise: `UNKNOWN`

## Test your change

```sh
./telescan.py validate                   # every entry against the schema
python3 -m unittest discover -s tests    # the test suite
```

The test suite already checks that every variable in `disable.env` is
recognized as an opt-out. For other checks, add a test in
`tests/test_coverage.py`. The tests there use a temporary home directory, so
you can write a config file and scan it:

```python
def test_myapp_config_opt_out(self):
    self.write(".config/myapp/settings.json", '{"telemetry": false}')
    self.assertEqual(self.scan("myapp").status, checks.DISABLED)
```

Add a test for each setting that the check can read, and for precedence or
profile cases when the entry has them.

## Change the code

The package has no dependencies. Keep it that way.

| File | Purpose |
| --- | --- |
| `telescan/catalog.py` | Loads and validates the entries. |
| `telescan/schema.py` | A small JSON Schema validator for the subset that `app.schema.json` uses. |
| `telescan/checks.py` | The check types. |
| `telescan/scanner.py` | Detection, and the rules that combine the checks into a result. |
| `telescan/versions.py` | Version ranges. |
| `telescan/report.py` | Table, JSON and Markdown output. |
| `telescan/cli.py` | The command line. |

When you add a field to the schema, also add it to `App` in `catalog.py`.

Format, lint and type check with [ruff](https://docs.astral.sh/ruff/) and
[ty](https://docs.astral.sh/ty/). The tools are in the `dev` dependency group
of `pyproject.toml`, and `uv.lock` pins their versions, so everyone and CI use
the same ones. With [uv](https://docs.astral.sh/uv/) installed:

```sh
uv sync                 # install the pinned tools into .venv
uv run ruff format .    # format
uv run ruff check .     # lint
uv run ty check         # type check
```

To check the built package the way CI does:

```sh
uv build
uv run --isolated --no-project --with dist/*.whl scripts/smoke_test.py
```

To update the tools inside their ranges, run `uv lock --upgrade`. To move
ruff to a new minor version, change its range in `pyproject.toml` first.
Add type annotations to new code.

CI runs these checks, and the tests on Linux, macOS and Windows with Python 3.9
and 3.12.

## Catalog review policy

- A pull request that adds or changes a check needs primary evidence, the
  versions and platforms it applies to, its scope, and a test.
- Reviewers check the evidence, not only the JSON.
- Entries with a `verified_at` date older than one year are due for review.
  Update the date only after you read the evidence again.
- When a vendor changes a control, keep the old control with a `versions`
  range if the old versions are still in use.

## Releases

The version is in `telescan/__init__.py` only. Record user-visible changes
under "Unreleased" in [CHANGELOG.md](CHANGELOG.md) in the same pull request.
To release, move those notes under the new version, set the version, and tag
the commit `vX.Y.Z`.
