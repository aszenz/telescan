"""A small JSON Schema validator, with no dependencies.

The catalog contract lives in `data/app.schema.json` so that contributors
and editors can read it, and so that validation is data rather than a list
of assertions in the test suite.  This module understands the subset of
JSON Schema (draft 2020-12) that the contract uses:

    type, enum, const, pattern, minLength, format ("uri")
    properties, required, additionalProperties, propertyNames
    items, minItems, uniqueItems
    allOf, anyOf, if/then, $ref, $defs

Anything else in a schema is ignored, so keep the contract inside that
subset.  `telescan validate` checks the catalog against the contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_FILE = Path(__file__).with_name("data") / "app.schema.json"

_TYPES: dict[str, Any] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "null": type(None),
}


def load_schema(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or SCHEMA_FILE).read_text(encoding="utf-8"))


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name in ("number", "boolean"):
        # bool is a subclass of int; keep the two apart.
        if isinstance(value, bool):
            return name == "boolean"
        return isinstance(value, _TYPES[name])
    expected = _TYPES.get(name)
    return expected is not None and isinstance(value, expected)


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def validate(instance: Any, schema: dict[str, Any], root: dict[str, Any] | None = None,
             where: str = "") -> list[str]:
    """Return a list of human readable problems.  Empty means valid."""
    root = root if root is not None else schema
    errors: list[str] = []

    def fail(message: str) -> None:
        errors.append(f"{where or 'entry'}: {message}")

    if "$ref" in schema:
        return validate(instance, _resolve(schema["$ref"], root), root, where)

    kinds = schema.get("type")
    if kinds is not None:
        names = [kinds] if isinstance(kinds, str) else list(kinds)
        if not any(_is_type(instance, name) for name in names):
            fail(f"expected {' or '.join(names)}, got {type(instance).__name__}")
            return errors

    if "const" in schema and instance != schema["const"]:
        fail(f"must be {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{instance!r} is not one of {schema['enum']}")

    if isinstance(instance, str):
        limit = schema.get("minLength")
        if limit is not None and len(instance) < limit:
            fail("must not be empty" if limit == 1 else f"is shorter than {limit} characters")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            fail(f"{instance!r} does not match {schema['pattern']}")
        if schema.get("format") == "uri" and "://" not in instance:
            fail(f"{instance!r} is not a URI")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            fail(f"needs at least {schema['minItems']} item(s)")
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for item in instance:
                if item in seen:
                    fail(f"holds the duplicate item {item!r}")
                    break
                seen.append(item)
        if "items" in schema:
            for index, item in enumerate(instance):
                errors += validate(item, schema["items"], root, f"{where}[{index}]")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                fail(f"is missing {name!r}")
        if "propertyNames" in schema:
            for name in instance:
                errors += validate(name, schema["propertyNames"], root, f"{where}.{name}")
        extra = schema.get("additionalProperties")
        for name, value in instance.items():
            child = f"{where}.{name}" if where else name
            if name in properties:
                errors += validate(value, properties[name], root, child)
            elif extra is False:
                fail(f"has the unknown field {name!r}")
            elif isinstance(extra, dict):
                errors += validate(value, extra, root, child)

    for subschema in schema.get("allOf", []):
        errors += validate(instance, subschema, root, where)
    if "anyOf" in schema:
        branches = [validate(instance, sub, root, where) for sub in schema["anyOf"]]
        if all(branches):
            fail("matches none of: " + "; ".join(
                ", ".join(problem.split(": ", 1)[-1] for problem in branch)
                for branch in branches))
    if "if" in schema:
        if not validate(instance, schema["if"], root, where):
            if "then" in schema:
                errors += validate(instance, schema["then"], root, where)
        elif "else" in schema:
            errors += validate(instance, schema["else"], root, where)

    return errors
