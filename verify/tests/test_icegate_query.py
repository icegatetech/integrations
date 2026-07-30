import subprocess
import sys
from pathlib import Path

import pytest

VERIFY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VERIFY_DIR))

import icegate_query  # noqa: E402
from assert_spans import check  # noqa: E402


@pytest.fixture(scope="module")
def probe_run_id():
    out = subprocess.run(
        [sys.executable, str(VERIFY_DIR / "fixtures" / "emit_probe_span.py")],
        capture_output=True, text=True, check=True,
    ).stdout
    line = [l for l in out.splitlines() if l.startswith("ICEGATE_RUN_ID=")][-1]
    return line.split("=", 1)[1].strip()


def test_probe_spans_are_queryable_and_nested(probe_run_id):
    conn = icegate_query.connect()
    spans = icegate_query.wait_for_spans(conn, probe_run_id, minimum=2, timeout_s=90)
    failures = check(
        {"spans": [
            {"name": "invoke_agent probe-agent",
             "required_attributes": {"gen_ai.operation.name": "invoke_agent"}},
            {"name": "execute_tool probe_tool",
             "parent": "invoke_agent probe-agent"},
        ]},
        spans, [],
    )
    assert failures == [], "\n".join(failures)


# -- pure-logic / monkeypatched tests: no IceGate required -------------------


def test_query_operations_hex_encodes_byte_columns(monkeypatch):
    """`SELECT o.*` used to pass span_id/trace_id through as raw bytes while
    query_spans hex-encoded the same kind of column. Any bytes value coming
    back from `_rows` must be hex-encoded, whatever column it's in."""

    def fake_rows(conn, sql, params=None):
        return [{
            "span_id": b"\xaa\xbb",
            "trace_id": b"\xcc\xdd",
            "provider_name": "openai",
            "input_tokens": 69,
        }]

    monkeypatch.setattr(icegate_query, "_rows", fake_rows)
    rows = icegate_query.query_operations(conn=object(), run_id="doesnt-matter")

    assert rows == [{
        "span_id": "aabb",
        "trace_id": "ccdd",
        "provider_name": "openai",
        "input_tokens": 69,
    }]


def test_wait_for_spans_queries_at_least_once_even_with_nonpositive_timeout(monkeypatch):
    """The old `while time.monotonic() < deadline:` guard ran before the
    first poll, so timeout_s<=0 raised "found 0 spans" without ever having
    queried — indistinguishable from the recipe genuinely exporting
    nothing."""
    calls = []

    def fake_query_spans(conn, run_id):
        calls.append(run_id)
        return []

    monkeypatch.setattr(icegate_query, "query_spans", fake_query_spans)

    with pytest.raises(TimeoutError):
        icegate_query.wait_for_spans(
            object(), "run-id", minimum=1, timeout_s=0, interval_s=5.0
        )

    assert calls == ["run-id"]


def test_wait_for_spans_clamps_sleep_to_remaining_time(monkeypatch):
    """A failed check used to sleep the full `interval_s` unconditionally,
    overshooting the deadline by up to one interval. The sleep must be
    clamped to whatever time is actually left."""
    fake_times = [100.0, 101.0, 106.0]

    def fake_monotonic():
        return fake_times.pop(0)

    sleep_calls = []

    monkeypatch.setattr(icegate_query, "query_spans", lambda conn, run_id: [])
    monkeypatch.setattr(icegate_query.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(icegate_query.time, "sleep", sleep_calls.append)

    with pytest.raises(TimeoutError):
        icegate_query.wait_for_spans(
            object(), "run-id", minimum=1, timeout_s=5.0, interval_s=10.0
        )

    # deadline = 100.0 + 5.0 = 105.0; after the first failed check at
    # monotonic()==101.0, remaining is 4.0 — less than interval_s (10.0) — so
    # the sleep must be clamped to 4.0, not the full interval.
    assert sleep_calls == [4.0]


# -- wait_for_operations: fake-connection tests (no IceGate required) --------
#
# Unlike the wait_for_spans tests above, these drive the real
# query_operations() -> _rows() -> conn.cursor() path instead of
# monkeypatching query_operations itself, per a fake connection/cursor whose
# cursor() call scripts one result set per poll.


class _FakeCursor:
    """Minimal DB-API cursor stub — just enough of the surface `_rows` in
    icegate_query.py actually drives: `execute`, `description`, `fetchall`,
    and the context-manager protocol (`_rows` opens it via
    `with conn.cursor() as cur:`)."""

    def __init__(self, columns, rows):
        self.description = [(c,) for c in columns]
        self._rows = rows

    def execute(self, sql, params):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    """Scripts one result set per call to `.cursor()`, so a test can lay out
    an exact sequence of polls (e.g. "1 row, then enough") — `query_operations`
    opens a fresh cursor on every call, so this exercises the same shape a
    real poll loop produces."""

    def __init__(self, columns, scripted_rows):
        self._columns = columns
        self._scripted_rows = list(scripted_rows)
        self.call_count = 0

    def cursor(self):
        rows = self._scripted_rows[self.call_count]
        self.call_count += 1
        return _FakeCursor(self._columns, rows)


def test_wait_for_operations_returns_rows_once_minimum_reached(monkeypatch):
    """Proves the polling loop terminates on success: the fake connection
    scripts one short poll (1 row) followed by one that meets minimum=2, and
    the call must stop there — not loop past success, not under-return."""
    conn = _FakeConnection(
        ["operation_name", "span_id"],
        [
            [("chat", "aaaa")],                    # poll 1: short of minimum
            [("chat", "aaaa"), ("chat", "bbbb")],   # poll 2: meets minimum
        ],
    )
    sleep_calls = []
    monkeypatch.setattr(icegate_query.time, "sleep", sleep_calls.append)

    rows = icegate_query.wait_for_operations(
        conn, "run-id", minimum=2, timeout_s=10.0, interval_s=1.0
    )

    assert rows == [
        {"operation_name": "chat", "span_id": "aaaa"},
        {"operation_name": "chat", "span_id": "bbbb"},
    ]
    assert conn.call_count == 2
    assert sleep_calls == [1.0]  # slept exactly once, between the two polls


def test_wait_for_operations_raises_with_none_arrived_message(monkeypatch):
    """Proves the empty-result TimeoutError branch, and that it queries at
    least once even with a nonpositive timeout (mirrors
    test_wait_for_spans_queries_at_least_once_even_with_nonpositive_timeout).
    The message must say rows never arrived at all — this is the loud false
    FAIL from the bug report, and must read differently from the "some rows
    landed" case below."""
    conn = _FakeConnection(["operation_name"], [[]])  # one scripted poll: no rows

    with pytest.raises(TimeoutError) as exc_info:
        icegate_query.wait_for_operations(
            conn, "run-id", minimum=1, timeout_s=0, interval_s=5.0
        )

    assert conn.call_count == 1
    message = str(exc_info.value)
    assert "found none" in message
    assert "none arrived" in message


def test_wait_for_operations_clamps_sleep_and_reports_partial_count(monkeypatch):
    """Proves the loop does not sleep past the deadline (same clamp as
    wait_for_spans), and that a nonzero-but-insufficient row count raises a
    distinctly worded "not enough yet" TimeoutError. This is the silent
    false-PASS shape from the bug report — some rows landed, not all — so the
    message must stay distinguishable from the "none arrived" case above."""
    conn = _FakeConnection(
        ["operation_name"],
        [
            [("chat",)],  # poll 1: 1 row, short of minimum=2
            [("chat",)],  # poll 2: still 1 row
        ],
    )
    fake_times = [100.0, 101.0, 106.0]

    def fake_monotonic():
        return fake_times.pop(0)

    sleep_calls = []
    monkeypatch.setattr(icegate_query.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(icegate_query.time, "sleep", sleep_calls.append)

    with pytest.raises(TimeoutError) as exc_info:
        icegate_query.wait_for_operations(
            conn, "run-id", minimum=2, timeout_s=5.0, interval_s=10.0
        )

    # Same clamp arithmetic as test_wait_for_spans_clamps_sleep_to_remaining_time:
    # deadline = 100.0 + 5.0 = 105.0; after the first failed poll at
    # monotonic()==101.0, remaining is 4.0 — less than interval_s (10.0) — so
    # the sleep must be clamped to 4.0, not the full interval.
    assert sleep_calls == [4.0]
    assert conn.call_count == 2
    message = str(exc_info.value)
    assert "found only 1" in message
    assert "not enough yet" in message
