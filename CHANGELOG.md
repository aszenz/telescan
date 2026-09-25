# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

- Catalog of 87 applications, one JSON file per application, with a JSON Schema.
- Each entry records its verification level, audit date, scope and evidence.
  The output shows them.
- Conflicting settings give `UNKNOWN`, not `OFF`.
- Controls that depend on the version apply only to the matching versions.
- Entries with no documented opt-out are `MANUAL`, not "no telemetry".
- npm: the update notifier is on by default, and `~/.npmrc` is now read.
- `-v` lists a setting once, even when several components use it.
- `telescan scan <app>` shows the sources and how to turn each app off;
  `-v` does this for every app. `list`, `show`, `categories` and `--fix`
  are removed.
- The output groups apps by telemetry state ("Telemetry on", "Telemetry off",
  ...) and starts with a count for each group.
