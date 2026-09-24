# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

## 0.1.0

First public preview.

- Catalog of 70 applications, one JSON file per application, with a JSON Schema.
- Each entry records its verification level, audit date, scope and evidence.
  The output shows them.
- Conflicting settings give `UNKNOWN`, not `OFF`.
- Controls that depend on the version apply only to the matching versions.
- Entries with no documented opt-out are `MANUAL`, not "no telemetry".
