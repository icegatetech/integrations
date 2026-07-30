"""Reads telemetry back out of IceGate over Arrow Flight SQL.

`span_attributes` and `resource_attributes` are Map<String, String>, so every
attribute value arrives as a string. The `operations` table holds the typed
projection; use it whenever a real integer or list is needed.
"""

from __future__ import annotations

import os
import time
import warnings

import adbc_driver_flightsql.dbapi

SPANS_TABLE = "iceberg.icegate.spans"
OPERATIONS_TABLE = "iceberg.icegate.operations"

# IceGate's Flight SQL layer is DataFusion, which uses $1-style placeholders.
# The DB-API-conventional `?` fails with "Failed to parse placeholder id:
# cannot parse integer from empty string" — verified 2026-07-29. Do not
# "correct" $1 to ?.


def connect(uri=None, tenant=None):
    uri = uri or os.environ.get("ICEGATE_FLIGHTSQL_URI", "grpc://localhost:8815")
    tenant = tenant or os.environ.get("ICEGATE_TENANT", "default")
    # adbc_driver_manager/dbapi.py emits "Warning: Cannot disable autocommit;
    # conn will not be DB-API 2.0 compliant" on every connect, because
    # IceGate's Flight SQL server doesn't support turning off autocommit.
    # Harmless, but it pollutes test output, so silence it narrowly: scoped to
    # just this call (catch_warnings, not a global filter) AND scoped to just
    # this message. `category=Warning` is required — that's the base category
    # ADBC actually raises under — but category alone would also swallow every
    # *subclass* of Warning for the duration of the call, including TLS, auth,
    # and driver-deprecation warnings that connect() is precisely where you'd
    # want to see. The `message` filter is what keeps this targeted instead of
    # a blanket "ignore all warnings" during connect.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Cannot disable autocommit", category=Warning
        )
        return adbc_driver_flightsql.dbapi.connect(
            uri,
            db_kwargs={"adbc.flight.sql.rpc.call_header.x-scope-orgid": tenant},
        )


def _rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _hex(value):
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value)


def query_spans(conn, run_id):
    """Spans for one run. span_id/parent_span_id are Fixed-width bytes; hex
    them here so callers never compare raw bytes against hex strings."""
    sql = f"""
        SELECT name,
               span_id,
               parent_span_id,
               service_name,
               span_attributes
        FROM {SPANS_TABLE}
        WHERE array_element(
                  map_extract(resource_attributes, 'icegate.run_id'), 1
              ) = $1
    """
    out = []
    for row in _rows(conn, sql, (run_id,)):
        attributes = row.get("span_attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {k: v for k, v in attributes}
        out.append({
            "name": row["name"],
            "span_id": _hex(row["span_id"]),
            "parent_span_id": _hex(row["parent_span_id"]),
            "service_name": row["service_name"],
            "attributes": {str(k): str(v) for k, v in attributes.items()},
        })
    return out


def query_operations(conn, run_id):
    """The typed GenAI projection. Joined to spans on span_id because
    `operations` carries no resource attributes of its own.

    `SELECT o.*` passes every column through untouched, so id columns
    (span_id, trace_id) arrive as raw bytes. Hex-encode them here too, so
    this matches `query_spans` and callers never have to compare raw bytes
    against hex strings from one half of the API but not the other.
    """
    sql = f"""
        SELECT o.*
        FROM {OPERATIONS_TABLE} o
        JOIN {SPANS_TABLE} s ON o.span_id = s.span_id
        WHERE array_element(
                  map_extract(s.resource_attributes, 'icegate.run_id'), 1
              ) = $1
    """
    return [
        {k: (_hex(v) if isinstance(v, (bytes, bytearray)) else v) for k, v in row.items()}
        for row in _rows(conn, sql, (run_id,))
    ]


def _poll_until_minimum(query_fn, conn, run_id, minimum, timeout_s, interval_s):
    """Shared polling loop behind `wait_for_spans`/`wait_for_operations`.

    Calls `query_fn(conn, run_id)` — `query_spans` or `query_operations` —
    until it returns at least `minimum` rows, or the deadline passes. Returns
    `(rows, timed_out)` rather than raising, so each caller phrases its own
    TimeoutError: spans and operations warrant different hints about what a
    shortfall means. Factoring only the loop (not the exception) is enough to
    stop the deadline/sleep arithmetic from being duplicated, without forcing
    both callers through one generic message.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        rows = query_fn(conn, run_id)
        if len(rows) >= minimum:
            return rows, False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return rows, True
        time.sleep(min(interval_s, remaining))


def wait_for_spans(conn, run_id, minimum, timeout_s=60.0, interval_s=2.0):
    """Poll until `minimum` spans are queryable, or raise.

    Spans cross BatchSpanProcessor -> OTLP -> WAL before becoming visible, so a
    single sleep is a race. Polling to a timeout is the difference between a
    reliable suite and a flaky one.
    """
    rows, timed_out = _poll_until_minimum(
        query_spans, conn, run_id, minimum, timeout_s, interval_s
    )
    if not timed_out:
        return rows
    raise TimeoutError(
        f"expected at least {minimum} spans for run_id={run_id} within "
        f"{timeout_s:.0f}s, found {len(rows)}. If this is 0, the recipe "
        f"probably exported nothing — run `make doctor`."
    )


def wait_for_operations(conn, run_id, minimum, timeout_s=60.0, interval_s=2.0):
    """Poll until `minimum` operations rows are queryable, or raise.

    `operations` is a separate typed projection from `spans`, written on its
    own ingest path (see `query_operations`'s docstring) — spans landing is
    no signal that operations has landed too. Querying it once, right after
    `wait_for_spans` returns with no wait of its own, reproduces two distinct
    failures: a loud false FAIL (0 rows so far, though they land moments
    later) and a silent false PASS (a partial set of rows already satisfies
    every `operations[]` expectation, since `_check_operation` only requires
    *one* matching row per `operation_name` — there is no per-name `count`
    the way `spans[]` has one — so the assertions can't tell "all rows in"
    from "just enough got lucky"). Polling here, the same way `wait_for_spans`
    does, closes both.
    """
    rows, timed_out = _poll_until_minimum(
        query_operations, conn, run_id, minimum, timeout_s, interval_s
    )
    if not timed_out:
        return rows
    if not rows:
        raise TimeoutError(
            f"expected at least {minimum} operations rows for run_id={run_id} "
            f"within {timeout_s:.0f}s, found none — none arrived. If spans "
            f"exist for this run but no operations rows ever show up, the "
            f"GenAI projection likely isn't recognizing them — run "
            f"`make doctor`."
        )
    raise TimeoutError(
        f"expected at least {minimum} operations rows for run_id={run_id} "
        f"within {timeout_s:.0f}s, found only {len(rows)} — not enough yet. "
        f"Some rows landed but the projection is still catching up; consider "
        f"raising timeout_s."
    )
