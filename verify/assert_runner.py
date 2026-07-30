"""CLI: assert_runner <expectations.yaml> <run_id>"""

import sys

import yaml

import icegate_query
from assert_spans import check, validate_minimums


def _default_minimum_operations(expectations):
    """Fallback for `minimum_operations` when the expectations file doesn't
    set it explicitly.

    `_check_operation` only requires *one* matching row per `operation_name`
    (there's no per-name `count` the way `spans[]` has one), so the smallest
    floor that still means something is: at least one row per distinct
    `operation_name` this file actually asserts on. That's not a guarantee
    every row for every name has landed — a name with several rows could
    still be short one — but it's a real improvement over not waiting at
    all, and it costs nothing to compute from the expectations already in
    hand. A file with no `operations` expectations gets `0`, i.e. no wait,
    since there is nothing to assert against that projection anyway.
    """
    operations_exp = expectations.get("operations")
    if not isinstance(operations_exp, list):
        return 0
    names = {
        exp.get("operation_name")
        for exp in operations_exp
        if isinstance(exp, dict) and "operation_name" in exp
    }
    return len(names)


def main():
    if len(sys.argv) != 3:
        print("usage: assert_runner <expectations.yaml> <run_id>", file=sys.stderr)
        return 2
    path, run_id = sys.argv[1], sys.argv[2]
    with open(path) as handle:
        expectations = yaml.safe_load(handle)

    if not isinstance(expectations, dict):
        print(
            f"error: {path} did not parse to a mapping (got {expectations!r}); "
            "check for an empty file or invalid YAML",
            file=sys.stderr,
        )
        return 2

    # Validated ahead of check() on purpose: minimum_spans/minimum_operations
    # gate the waits below that produce the rows check() asserts against, so
    # a bad type here (e.g. a quoted "8") must fail cleanly now rather than
    # raise an uncaught TypeError deep inside the polling loop.
    schema_failures = validate_minimums(expectations)
    if schema_failures:
        print(f"error: {path} has an invalid schema:", file=sys.stderr)
        for failure in schema_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 2

    minimum_spans = expectations.get("minimum_spans", 1)
    conn = icegate_query.connect()
    try:
        spans = icegate_query.wait_for_spans(conn, run_id, minimum=minimum_spans, timeout_s=90)
    except TimeoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    minimum_operations = expectations.get(
        "minimum_operations", _default_minimum_operations(expectations)
    )
    try:
        operations = icegate_query.wait_for_operations(
            conn, run_id, minimum=minimum_operations, timeout_s=90
        )
    except TimeoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    failures = check(expectations, spans, operations)
    print(f"  {len(spans)} spans, {len(operations)} operations rows for run {run_id}")
    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
