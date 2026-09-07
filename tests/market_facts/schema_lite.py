"""Zero-dependency JSON Schema (draft-07 subset) validator for market-facts.

TA-MF-01 requires machine-verifiable schemas, but the project venv has no
``jsonschema`` package (no pip available).  This module implements exactly the
keyword subset used by ``docs/contracts/schemas/*.schema.json`` so the shared
fixtures stay machine-checked in CI without adding dependencies:

``$ref`` (internal ``#/definitions/...`` only), ``type`` (string or list,
``null`` included), ``enum``, ``const``, ``properties``, ``required``,
``additionalProperties``, ``items``, ``minItems``, ``maxItems``, ``pattern``,
``minimum``, ``maximum``, ``minLength``, ``maxLength``, ``minProperties``,
``allOf``, ``anyOf``, ``oneOf``, ``if``/``then``/``else``.

Anything outside this subset raises ``UnsupportedSchema`` instead of being
silently ignored, so an accidental keyword upgrade fails loudly.
"""

from __future__ import annotations

import re
from typing import Any

SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema", "$id", "title", "description", "definitions",
        "type", "enum", "const", "properties", "required", "additionalProperties",
        "items", "minItems", "maxItems", "pattern", "minimum", "maximum",
        "minLength", "maxLength", "minProperties",
        "allOf", "anyOf", "oneOf", "if", "then", "else", "$ref",
    }
)

TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")


class UnsupportedSchema(ValueError):
    """Raised when the schema uses a keyword outside the supported subset."""


def _matches_type(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, dict)
    raise UnsupportedSchema(f"unknown type keyword value: {expected!r}")


def _equal(instance: Any, const: Any) -> bool:
    # bool is an int subclass in Python; keep true/false from matching 1/0.
    if isinstance(instance, bool) != isinstance(const, bool):
        return False
    return instance == const


def _resolve_ref(schema: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise UnsupportedSchema(f"only internal #/ definitions refs are supported: {ref!r}")
    node: Any = schema
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise UnsupportedSchema(f"unresolvable $ref: {ref!r}")
        node = node[part]
    return node


def _check(instance: Any, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    unknown = set(schema) - SUPPORTED_KEYWORDS
    if unknown:
        raise UnsupportedSchema(f"unsupported schema keywords at {path or '<root>'}: {sorted(unknown)}")

    if "$ref" in schema:
        _check(instance, _resolve_ref(root, schema["$ref"]), root, path, errors)

    if "type" in schema:
        expected = schema["type"]
        options = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(instance, option) for option in options):
            errors.append(f"{path}: expected type {expected}, got {type(instance).__name__}")
            return

    if "enum" in schema:
        if not any(_equal(instance, option) for option in schema["enum"]):
            errors.append(f"{path}: {instance!r} not in enum {schema['enum']!r}")

    if "const" in schema:
        if not _equal(instance, schema["const"]):
            errors.append(f"{path}: {instance!r} != const {schema['const']!r}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: length {len(instance)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: length {len(instance)} > maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: {len(instance)} items < minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: {len(instance)} items > maxItems {schema['maxItems']}")
        if "items" in schema:
            for index, element in enumerate(instance):
                _check(element, schema["items"], root, f"{path}[{index}]", errors)

    if isinstance(instance, dict):
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append(
                f"{path}: {len(instance)} properties < minProperties {schema['minProperties']}"
            )
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, sub_schema in properties.items():
            if name in instance:
                _check(instance[name], sub_schema, root, f"{path}.{name}", errors)
        additional = schema.get("additionalProperties", True)
        if additional is False:
            unknown_props = sorted(set(instance) - set(properties))
            for name in unknown_props:
                errors.append(f"{path}: additional property {name!r} is not allowed")
        elif isinstance(additional, dict):
            for name, value in instance.items():
                if name not in properties:
                    _check(value, additional, root, f"{path}.{name}", errors)

    for sub_schema in schema.get("allOf", []):
        _check(instance, sub_schema, root, path, errors)

    if "anyOf" in schema:
        branch_errors: list[list[str]] = []
        for sub_schema in schema["anyOf"]:
            branch: list[str] = []
            _check(instance, sub_schema, root, path, branch)
            branch_errors.append(branch)
        if not any(not branch for branch in branch_errors):
            errors.append(f"{path}: does not match anyOf ({len(branch_errors)} branches failed)")

    if "oneOf" in schema:
        matched = 0
        for sub_schema in schema["oneOf"]:
            branch: list[str] = []
            _check(instance, sub_schema, root, path, branch)
            if not branch:
                matched += 1
        if matched != 1:
            errors.append(f"{path}: expected exactly one oneOf match, got {matched}")

    if "if" in schema:
        condition_errors: list[str] = []
        _check(instance, schema["if"], root, path, condition_errors)
        branch = "then" if not condition_errors else "else"
        if branch in schema:
            _check(instance, schema[branch], root, path, errors)


def validate(instance: Any, schema: dict) -> list[str]:
    """Validate ``instance`` against ``schema``; return a list of error strings."""

    errors: list[str] = []
    _check(instance, schema, schema, "", errors)
    return errors


def assert_valid(instance: Any, schema: dict) -> None:
    errors = validate(instance, schema)
    if errors:
        raise AssertionError("schema validation failed:\n  " + "\n  ".join(errors))
