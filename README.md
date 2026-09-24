# telescan

![logo](./docs/assets/telescan-logo-readme.png)

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

Each app gets a status: `ON`, `OFF`, `PARTIAL`, `UNKNOWN` or `MANUAL`.
`MANUAL` means there is no local setting to read, so check it by hand.

## Features

- Covers 87 apps: editors, browsers, AI and cloud CLIs, frameworks, package
  managers, desktop apps and operating systems.
- Prints the documented opt-out for each app, as steps, commands or
  environment variables (`--export-env`).
- Links every result to the vendor documentation or source it is based on.
- Outputs a table, JSON or Markdown. Use `--fail-on` to fail a CI job.

A missing app or a wrong result? See [CONTRIBUTING.md](CONTRIBUTING.md).

[MIT license](LICENSE)
