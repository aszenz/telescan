# telescan

A command line scanner that tells you which applications on your machine still
send telemetry, and how to turn each one off.

It is two things in one:

1. **A catalog.** 70 common developer and desktop applications, with what each
   one collects, the default state, the exact switch, and a link to the vendor
   documentation.
2. **A scanner.** It reads the config files and environment variables on your
   machine and reports the real state of each application it finds.

No dependencies. Python 3.9 or later. Nothing leaves your machine, and the
scanner never writes to your configuration.

## Install

```sh
# run it straight from the checkout
./telescan.py scan

# or install the command
pip install .
telescan scan
```

## Use

```sh
telescan scan                      # scan this machine
telescan scan --fix                # scan, then print how to turn each hit off
telescan scan -v                   # show the file or variable behind each result
telescan scan --all                # include catalog entries that are not installed
telescan scan --category browsers  # scan one category
telescan scan vscode homebrew      # scan named entries only
telescan scan --run-commands       # let checks call the app itself (brew analytics state)
telescan scan --export-env         # export lines for your shell profile
telescan scan --export-commands    # the opt-out commands, printed, never run
telescan list                      # the whole catalog
telescan show homebrew             # one entry in full
telescan categories                # the categories and their size
telescan validate                  # check the catalog against app.schema.json
```

Output formats: `--format table` (default), `--format json`, `--format markdown`.

### Example

```
ON   Visual Studio Code            editors
     no opt-out found; telemetry is on by default
ON   Homebrew                      package-managers
     no opt-out found; telemetry is on by default
OFF  Next.js                       frameworks
     NEXT_TELEMETRY_DISABLED=1
OFF  Go toolchain                  package-managers
     telemetry is off by default

10 applications scanned: 2 on, 8 off, 0 unknown, 0 manual
```

## Status values

| Status | Meaning |
| --- | --- |
| `ON` | Telemetry is on, or the application is on by default and no opt-out was found. |
| `OFF` | All checked components and discovered profiles are disabled, or the catalog assumes they are off by default. |
| `PARTIAL` | Some components or profiles are disabled; others are enabled, unknown, or manual. |
| `UNKNOWN` | The application is installed but the state could not be read. |
| `MANUAL` | There is no local switch to read. The entry says what to do instead. |
| `ABSENT` | Not installed. Only shown with `--all`. |

Independent components and profiles are evaluated separately. For example,
Claude Code usage telemetry being off does not establish that error reporting
is off, and one Firefox profile cannot establish the state of another.

Within a component/profile, the existing opt-out-wins rule is retained for
recognized settings. Unreadable configuration and unrecognized values produce
`UNKNOWN` instead of falling back to catalog defaults. Application-specific
precedence between conflicting settings is not yet modeled.

`OFF` describes the checks in the catalog, not all possible data collection.
For example, Firefox currently checks health-report uploads; other browser
controls and enterprise policies still need separate review. Copilot account
policies are `MANUAL`, even when VS Code telemetry is disabled.

## Catalog

| Category | Entries | Examples |
| --- | --- | --- |
| `ai-tools` | 3 | Claude Code, Gemini CLI, GitHub Copilot |
| `browsers` | 5 | Firefox, Chrome, Brave, Edge, Chromium |
| `cloud-cli` | 20 | Terraform, gcloud, Azure CLI, Docker, Vercel, Wrangler, Snyk |
| `desktop-apps` | 4 | Slack, Zoom, Insomnia, Syncthing |
| `editors` | 7 | VS Code, Cursor, JetBrains IDEs, Zed, Android Studio |
| `frameworks` | 17 | Next.js, Nuxt, Astro, Angular, Storybook, Flutter, Cypress |
| `operating-system` | 3 | macOS analytics, Ubuntu apport, Windows diagnostic data |
| `package-managers` | 11 | Homebrew, Yarn, .NET SDK, PowerShell, Go, winget |

Run `telescan list` for the full list.

## Use it in CI

`telescan` exits `1` when something matched `--fail-on` (default: `enabled`),
`0` when no result matches the threshold, and `2` on a usage or catalog error.
`PARTIAL` also fails the default `--fail-on enabled` threshold.

```sh
# fail the build if a developer image ships with telemetry on
telescan scan --fail-on enabled --format json > telemetry.json
```

## How a check works

Each catalog entry holds detection rules and a list of checks. A check reads
one place only:

| Type | Reads |
| --- | --- |
| `env` | An environment variable. |
| `json` | A key in a JSON or JSONC file (comments and trailing commas are accepted). |
| `ini` | A key in an INI style file. |
| `regex` | A pattern in a text file. |
| `file` | A marker file that only exists when telemetry is off. |
| `command` | The output of the application itself. Runs only with `--run-commands`. |
| `manual` | Nothing. It marks an application with no local switch. |

Checks can optionally declare:

- `component`: a name such as `metrics` or `diagnostics`. Each component must
  be disabled before the application can be `OFF`.
- `profiles: true`: for JSON, INI, or regex checks, evaluate every matching
  file independently. Without it, the file list retains its precedence order.
- `default_state`: `on`, `off`, or `unknown` for that component. Profile checks
  should set it explicitly; missing preferences in discovered profiles use
  this default (otherwise `unknown`). Read errors never use the default.

Components appear in table and Markdown details and in the JSON `components`
array. JSON findings include `component` and `profile` fields; the latter is
empty for checks that do not operate on separate profiles. Profile paths refer
to all discovered matching files, which may include inactive profiles.

The regression suite checks that every environment opt-out advertised in
`disable.env` is recognized using an isolated environment and no config files.
Command and prose-only instructions still need application-specific fixtures.

Paths in the catalog use placeholders (`{home}`, `{config}`, `{data}`,
`{appsupport}`, `{appdata}`, `{localappdata}`), so one entry works on Linux,
macOS and Windows. Globs are allowed.

## Add an application

The catalog is one file per application, in `telescan/data/apps.d/`. Add
`telescan/data/apps.d/myapp.json`, where the file name is the entry `id`. No
Python change is needed, and one entry per file keeps pull requests apart:

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
  "docs": "https://example.com/telemetry"
}
```

`*truthy*` matches `1`, `true`, `yes`, `on`, `enabled`; `*falsy*` matches `0`,
`false`, `no`, `off`, `disabled`, `none`; and `*nonempty*` matches every
nonempty value. Use `*nonempty*` only for controls whose upstream implementation
tests presence rather than parsing a boolean. You can also list literal values.

`telescan/data/app.schema.json` is the contract for an entry: the fields, the
categories, the platforms, and the fields each check type needs. Check your
entry against it, and point your editor at it for completion:

```sh
telescan validate                      # the whole catalog
telescan validate path/to/myapp.json   # one file, before you commit it
python3 -m unittest discover -s tests  # the test suite
```

`telescan validate` reads a directory of entries or a single JSON file, so you
can try an entry out before you contribute it.

## Limits

- The catalog is a point in time snapshot. Vendors rename variables and move
  config files, so treat `docs` as the source of truth.
- The scanner reads the state it can see. `UNKNOWN` and `MANUAL` mean "look at
  this by hand", not "you are safe".
- Turning the local switch off does not stop an application from talking to its
  own backend for the work you asked it to do.
