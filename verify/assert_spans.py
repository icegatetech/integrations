"""Expectation engine.

Pure logic: takes expectations plus rows already fetched from IceGate and
returns human-readable failure strings. Kept free of I/O so it is unit
testable without a running IceGate.
"""

from __future__ import annotations

_TOP_LEVEL_KEYS = {"spans", "operations", "minimum_spans", "minimum_operations"}

_SPAN_KEYS = {
    "name",
    "parent",
    "count",
    "required_attributes",
    "present_attributes",
    "forbidden_attributes",
    "contains_attributes",
    "not_contains_attributes",
    "numeric_attributes",
}

_OPERATION_KEYS = {
    "operation_name",
    "required_columns",
    "non_null_columns",
    "null_columns",
}

_NUMERIC_OPERATORS = (
    ("gt", ">", lambda a, b: a > b),
    ("gte", ">=", lambda a, b: a >= b),
    ("lt", "<", lambda a, b: a < b),
    ("lte", "<=", lambda a, b: a <= b),
    ("eq", "==", lambda a, b: a == b),
)


def _unknown_keys(mapping, known):
    return sorted(set(mapping) - known)


def validate_minimums(expectations):
    """Type-check `minimum_spans`/`minimum_operations`; return failure
    strings for anything wrong, never raise.

    Both values flow into a `len(rows) >= minimum` comparison inside
    icegate_query's polling loop, where a non-int (e.g. a YAML-quoted `"8"`)
    raises an uncaught TypeError instead of a clean failure. assert_runner
    needs that caught *before* it can even fetch spans/operations — these
    values gate the wait that produces the rows `check()` asserts against, so
    validation can't wait for `check()`, which only runs after the fetch.
    `check()` calls this too, so the same behavior is covered by the
    pure-logic test suite without needing IceGate up.

    `bool` is deliberately rejected even though `isinstance(True, int)` is
    True in Python: `minimum_spans: true` reads like a typo for an int, and
    silently treating it as `1` would hide that instead of flagging it.
    """
    failures = []
    for key in ("minimum_spans", "minimum_operations"):
        if key not in expectations:
            continue
        value = expectations[key]
        if isinstance(value, bool) or not isinstance(value, int):
            failures.append(f"{key!r} must be an integer, got {value!r}")
    return failures


def _normalize(value):
    """YAML parses an unquoted `true`/`false` into a Python bool, and
    `str(True) == "True"` — but OpenTelemetry always emits the lowercase
    string `"true"`/`"false"`. Comparing through this instead of raw `str()`
    keeps `required_attributes: {some.flag: true}` matching real telemetry.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _values_equal(got, want):
    return _normalize(got) == _normalize(want)


def _mapping_or_fail(exp, key, label, failures):
    value = exp.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        failures.append(f"{label}: {key!r} must be a mapping, got {value!r}")
        return {}
    return value


def _list_or_fail(exp, key, label, failures):
    value = exp.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        failures.append(f"{label}: {key!r} must be a list, got {value!r}")
        return []
    return value


def _check_span(exp, spans):
    """A span expectation applies to every span sharing its name — not just
    an arbitrary first match. Recipes legitimately emit multiple spans with
    the same name (e.g. one per turn), and matching only "the first one" a
    query happens to return makes both PASS and FAIL non-deterministic
    across runs. So: every span with the name must satisfy every predicate,
    and an optional `count` pins how many are expected.
    """
    name = exp["name"]
    matches = [s for s in spans if s["name"] == name]
    if not matches:
        available = ", ".join(sorted(s["name"] for s in spans)) or "(none)"
        return [f"missing span {name!r}; found: {available}"]

    failures = []

    if "count" in exp:
        expected_count = exp["count"]
        if len(matches) != expected_count:
            failures.append(
                f"span {name!r}: expected count {expected_count}, "
                f"found {len(matches)}"
            )

    multiple = len(matches) > 1
    for index, span in enumerate(matches):
        if multiple:
            label = f"span {name!r} #{index} (id={span['span_id']!r})"
        else:
            label = f"span {name!r}"
        failures.extend(_check_span_predicates(exp, span, spans, label))

    return failures


def _check_span_predicates(exp, span, spans, label):
    failures = []

    for key, want in _mapping_or_fail(exp, "required_attributes", label, failures).items():
        got = span["attributes"].get(key)
        if got is None:
            failures.append(f"{label}: missing attribute {key!r}")
        elif not _values_equal(got, want):
            failures.append(
                f"{label}: attribute {key!r} is {got!r}, expected {want!r}"
            )

    for key in _list_or_fail(exp, "present_attributes", label, failures):
        if key not in span["attributes"]:
            failures.append(f"{label}: missing attribute {key!r}")

    for key in _list_or_fail(exp, "forbidden_attributes", label, failures):
        if key in span["attributes"]:
            failures.append(
                f"{label}: forbidden attribute {key!r} is present "
                f"(value would leak: {span['attributes'][key]!r:.40})"
            )

    for key, needle in _mapping_or_fail(exp, "contains_attributes", label, failures).items():
        got = span["attributes"].get(key)
        if got is None:
            failures.append(f"{label}: missing attribute {key!r}")
        elif needle not in got:
            failures.append(
                f"{label}: attribute {key!r} does not contain {needle!r}"
            )

    for key, needle in _mapping_or_fail(exp, "not_contains_attributes", label, failures).items():
        got = span["attributes"].get(key)
        if got is not None and needle in got:
            failures.append(
                f"{label}: attribute {key!r} must not contain {needle!r} "
                f"— redaction did not apply"
            )

    for key, pred in _mapping_or_fail(exp, "numeric_attributes", label, failures).items():
        if not isinstance(pred, dict):
            failures.append(
                f"{label}: numeric predicate for {key!r} must be a mapping, "
                f"got {pred!r}"
            )
            continue
        raw = span["attributes"].get(key)
        if raw is None:
            failures.append(f"{label}: missing numeric attribute {key!r}")
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            failures.append(
                f"{label}: attribute {key!r} is {raw!r}, not numeric"
            )
            continue
        failures.extend(_check_predicate(f"{label}: {key}", value, pred))

    parent_name = exp.get("parent")
    if parent_name is not None:
        parent_candidates = [s for s in spans if s["name"] == parent_name]
        if not parent_candidates:
            failures.append(
                f"{label}: expected parent {parent_name!r}, which is missing"
            )
        else:
            parent_ids = {p["span_id"] for p in parent_candidates}
            if span["parent_span_id"] not in parent_ids:
                if len(parent_ids) == 1:
                    expected_desc = repr(next(iter(parent_ids)))
                else:
                    expected_desc = "one of " + repr(sorted(parent_ids))
                failures.append(
                    f"{label}: wrong parent — parent_span_id is "
                    f"{span['parent_span_id']!r}, expected {expected_desc} "
                    f"({parent_name!r})"
                )

    return failures


def _check_predicate(label, value, pred):
    failures = []
    known = {key for key, _, _ in _NUMERIC_OPERATORS}
    for key, symbol, op in _NUMERIC_OPERATORS:
        if key in pred and not op(value, pred[key]):
            failures.append(f"{label} is {value}, expected {symbol} {pred[key]}")
    for key in sorted(set(pred) - known):
        failures.append(f"{label}: unknown numeric predicate operator {key!r}")
    return failures


def _check_operation(exp, operations):
    """Every row whose `operation_name` matches must independently satisfy
    every column predicate — not just one of them — since `operations` can
    legitimately hold multiple rows for the same operation name (e.g. one
    per call). Rows are labelled with an index (and span_id, if present)
    whenever more than one row matches, so failures on different rows don't
    collapse into identical, indistinguishable strings.
    """
    wanted = exp.get("operation_name")
    rows = [o for o in operations if o.get("operation_name") == wanted]
    label_base = f"operations[{wanted!r}]"
    if not rows:
        available = ", ".join(
            sorted({str(o.get("operation_name")) for o in operations})
        ) or "(none)"
        return [f"missing operations row for operation_name={wanted!r}; found: {available}"]

    failures = []
    required_columns = _mapping_or_fail(exp, "required_columns", label_base, failures)
    non_null_columns = _list_or_fail(exp, "non_null_columns", label_base, failures)
    null_columns = _list_or_fail(exp, "null_columns", label_base, failures)

    multiple = len(rows) > 1
    for index, row in enumerate(rows):
        row_label = label_base
        if multiple:
            span_id = row.get("span_id")
            if span_id is not None:
                row_label = f"{label_base} row #{index} (span_id={span_id!r})"
            else:
                row_label = f"{label_base} row #{index}"

        for col, want in required_columns.items():
            got = row.get(col)
            if got is None:
                failures.append(
                    f"{row_label}: column {col!r} is NULL, expected {want!r}"
                )
            elif not _values_equal(got, want):
                failures.append(
                    f"{row_label}: column {col!r} is {got!r}, expected {want!r}"
                )
        for col in non_null_columns:
            if row.get(col) is None:
                failures.append(f"{row_label}: column {col!r} is NULL")
        for col in null_columns:
            if row.get(col) is not None:
                failures.append(
                    f"{row_label}: column {col!r} is {row[col]!r}, expected NULL"
                )
    return failures


def check(expectations, spans, operations):
    """Return a list of failure strings. Empty means everything passed.

    Validates `expectations` against the known schema first: an unrecognized
    key (a typo like `require_attributes`, or a top-level `span:` instead of
    `spans:`) is reported as a failure rather than silently skipped, and a
    predicate mapping given as the wrong type (e.g. `numeric_attributes:
    {tokens: 5}`) fails clearly instead of raising a TypeError. Without this,
    a malformed expectations.yaml can report PASSED having verified nothing.
    `minimum_spans`/`minimum_operations` are also type-checked here (see
    `validate_minimums`), since a non-int reaches a `>=` comparison inside
    the polling loop instead of a predicate this function could catch.
    """
    if not isinstance(expectations, dict):
        return [f"expectations must be a mapping, got {expectations!r}"]

    failures = [
        f"unknown top-level expectation key {key!r}"
        for key in _unknown_keys(expectations, _TOP_LEVEL_KEYS)
    ]
    failures.extend(validate_minimums(expectations))

    spans_exp = expectations.get("spans")
    if spans_exp is None:
        spans_exp = []
    elif not isinstance(spans_exp, list):
        failures.append(f"'spans' must be a list, got {spans_exp!r}")
        spans_exp = []

    for exp in spans_exp:
        if not isinstance(exp, dict):
            failures.append(f"span expectation must be a mapping, got {exp!r}")
            continue
        failures.extend(
            f"span expectation {exp.get('name')!r}: unknown key {key!r}"
            for key in _unknown_keys(exp, _SPAN_KEYS)
        )
        if "name" not in exp:
            failures.append(f"span expectation missing required key 'name': {exp!r}")
            continue
        failures.extend(_check_span(exp, spans))

    operations_exp = expectations.get("operations")
    if operations_exp is None:
        operations_exp = []
    elif not isinstance(operations_exp, list):
        failures.append(f"'operations' must be a list, got {operations_exp!r}")
        operations_exp = []

    for exp in operations_exp:
        if not isinstance(exp, dict):
            failures.append(f"operation expectation must be a mapping, got {exp!r}")
            continue
        failures.extend(
            f"operation expectation {exp.get('operation_name')!r}: unknown key {key!r}"
            for key in _unknown_keys(exp, _OPERATION_KEYS)
        )
        if "operation_name" not in exp:
            failures.append(
                f"operation expectation missing required key 'operation_name': {exp!r}"
            )
            continue
        failures.extend(_check_operation(exp, operations))

    return failures
