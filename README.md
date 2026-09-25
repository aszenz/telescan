# telescan

![logo](./docs/assets/telescan-logo-readme.png)

A lot of tools devs use silently track usage this is called telemetry, 
telemetry is opt in but can often be disabled in settings or via env var. 

telescan reads the config files and environment variables of the developer
and desktop apps on your machine, reports which ones still send telemetry, and
shows how to turn each one off.

## Run it

```sh
# run once, with no install
uvx --from git+https://github.com/aszenz/telescan telescan

# or install the telescan command
uv tool install git+https://github.com/aszenz/telescan
pipx install git+https://github.com/aszenz/telescan
```

Needs Python 3.9 or later. Works on Linux, macOS and Windows.

## Use it

```sh
telescan                  # scan this machine
telescan scan vscode npm  # scan some apps, and show how to turn each one off
telescan scan -v          # show sources and how to turn off every hit
```

telescan puts each app in one group:

- **Telemetry on**: the app sends telemetry.
- **Telemetry partly off**: some telemetry is off, some is still on.
- **Telemetry off**: all the telemetry that telescan knows is off.
- **Cannot tell**: a setting is unreadable, conflicts, or is missing.
- **Check by hand**: the app has no local setting to read.

## Features

- Covers 87 apps: editors, browsers, AI and cloud CLIs, frameworks, package
  managers, desktop apps and operating systems.
- Prints the documented opt-out for each app, as steps, commands or
  environment variables (`--export-env`).
- Links every result to the vendor documentation or source it is based on.
- Outputs a table, JSON or Markdown. Use `--fail-on` to fail a CI job.

A missing app or a wrong result? See [CONTRIBUTING.md](CONTRIBUTING.md).

[MIT license](LICENSE)
