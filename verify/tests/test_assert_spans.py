import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assert_spans import check, validate_minimums


def _spans():
    return [
        {
            "name": "invoke_agent travel-concierge",
            "span_id": "aaaa000000000001",
            "parent_span_id": None,
            "service_name": "probe",
            "attributes": {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "travel-concierge",
            },
        },
        {
            "name": "execute_tool get_weather",
            "span_id": "aaaa000000000002",
            "parent_span_id": "aaaa000000000001",
            "service_name": "probe",
            "attributes": {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "get_weather",
                "gen_ai.usage.input_tokens": "69",
            },
        },
    ]


def _duplicate_named_spans(*, both_satisfy):
    """Two spans sharing a name — the exact shape that made first-match
    lookups non-deterministic (recipes emit two `chat gemma4:12b-mlx` spans,
    SQL has no ORDER BY)."""
    return [
        {
            "name": "chat gemma4:12b-mlx",
            "span_id": "bbbb000000000001",
            "parent_span_id": None,
            "service_name": "probe",
            "attributes": {"gen_ai.operation.name": "chat"},
        },
        {
            "name": "chat gemma4:12b-mlx",
            "span_id": "bbbb000000000002",
            "parent_span_id": None,
            "service_name": "probe",
            "attributes": {
                "gen_ai.operation.name": "chat" if both_satisfy else "wrong"
            },
        },
    ]


def test_passes_when_all_requirements_met():
    exp = {
        "spans": [
            {"name": "invoke_agent travel-concierge",
             "required_attributes": {"gen_ai.operation.name": "invoke_agent"}},
            {"name": "execute_tool get_weather",
             "parent": "invoke_agent travel-concierge"},
        ]
    }
    assert check(exp, _spans(), []) == []


def test_reports_missing_span():
    exp = {"spans": [{"name": "chat gemma4:12b-mlx"}]}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "chat gemma4:12b-mlx" in failures[0]


def test_reports_wrong_attribute_value():
    exp = {"spans": [{"name": "invoke_agent travel-concierge",
                      "required_attributes": {"gen_ai.operation.name": "chat"}}]}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "gen_ai.operation.name" in failures[0]


def test_reports_broken_parent_child_when_parent_span_missing():
    """The parent-missing branch: the expectation names a parent span that
    does not exist in the fixture at all."""
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "parent": "chat gemma4:12b-mlx"}]}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "parent" in failures[0]
    assert "which is missing" in failures[0]


def test_reports_wrong_parent_when_ids_mismatch():
    """The id-mismatch branch: both spans exist, but the child's
    parent_span_id points at something other than the named parent's
    span_id. This is exactly where a hex-encoding regression would show up,
    and it previously had no test at all."""
    spans = [
        {
            "name": "invoke_agent travel-concierge",
            "span_id": "aaaa000000000001",
            "parent_span_id": None,
            "service_name": "probe",
            "attributes": {},
        },
        {
            "name": "execute_tool get_weather",
            "span_id": "aaaa000000000002",
            # Deliberately wrong: does not match the parent's span_id above.
            "parent_span_id": "ffff000000000099",
            "service_name": "probe",
            "attributes": {},
        },
    ]
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "parent": "invoke_agent travel-concierge"}]}
    failures = check(exp, spans, [])
    assert len(failures) == 1
    assert "wrong parent" in failures[0]


def test_forbidden_attribute_present_is_a_failure():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "forbidden_attributes": ["gen_ai.usage.input_tokens"]}]}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "forbidden" in failures[0].lower()


def test_forbidden_attribute_absent_passes():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "forbidden_attributes": ["gen_ai.input.messages"]}]}
    assert check(exp, _spans(), []) == []


def test_numeric_predicate_casts_string_attribute():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "numeric_attributes": {"gen_ai.usage.input_tokens": {"gt": 0}}}]}
    assert check(exp, _spans(), []) == []


def test_numeric_predicate_failure_is_reported():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "numeric_attributes": {"gen_ai.usage.input_tokens": {"gt": 1000}}}]}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    # Pin the `gt` predicate branch specifically, so this test would not
    # also pass for a "missing" or "not numeric" failure.
    assert "expected > 1000" in failures[0]


def test_numeric_attribute_non_numeric_value_is_reported():
    """The 'not numeric' branch (distinct from 'missing' and from a failed
    predicate) — previously had no test at all."""
    spans = [{
        "name": "execute_tool get_weather",
        "span_id": "aaaa000000000002",
        "parent_span_id": None,
        "service_name": "probe",
        "attributes": {"gen_ai.usage.input_tokens": "not-a-number"},
    }]
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "numeric_attributes": {"gen_ai.usage.input_tokens": {"gt": 0}}}]}
    failures = check(exp, spans, [])
    assert len(failures) == 1
    assert "not numeric" in failures[0]


def test_contains_attribute_substring():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "contains_attributes": {"gen_ai.tool.name": "weather"}}]}
    assert check(exp, _spans(), []) == []


def test_contains_attribute_substring_missing_is_reported():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "contains_attributes": {"gen_ai.tool.name": "forecast"}}]}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "does not contain" in failures[0]


def test_not_contains_attribute_passes_when_absent():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "not_contains_attributes": {"gen_ai.tool.name": "@example.com"}}]}
    assert check(exp, _spans(), []) == []


def test_not_contains_attribute_failure_is_reported():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "not_contains_attributes": {"gen_ai.tool.name": "weather"}}]}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "must not contain" in failures[0]


def test_required_attribute_yaml_bool_true_matches_otel_string_true():
    """YAML `true` parses to Python bool True; str(True) == 'True', but
    OpenTelemetry always emits the lowercase string 'true'. Without
    normalization this false-fails against correct telemetry."""
    spans = [{
        "name": "chat gemma4:12b-mlx",
        "span_id": "aaaa000000000001",
        "parent_span_id": None,
        "service_name": "probe",
        "attributes": {"some.flag": "true"},
    }]
    exp = {"spans": [{"name": "chat gemma4:12b-mlx",
                      "required_attributes": {"some.flag": True}}]}
    assert check(exp, spans, []) == []


def test_required_attribute_yaml_bool_false_matches_otel_string_false():
    spans = [{
        "name": "chat gemma4:12b-mlx",
        "span_id": "aaaa000000000001",
        "parent_span_id": None,
        "service_name": "probe",
        "attributes": {"some.flag": "false"},
    }]
    exp = {"spans": [{"name": "chat gemma4:12b-mlx",
                      "required_attributes": {"some.flag": False}}]}
    assert check(exp, spans, []) == []


# -- multiple spans sharing a name (finding: first-match hid broken nesting) -


def test_all_matching_spans_must_satisfy_predicates_when_all_pass():
    exp = {"spans": [{"name": "chat gemma4:12b-mlx",
                      "required_attributes": {"gen_ai.operation.name": "chat"}}]}
    assert check(exp, _duplicate_named_spans(both_satisfy=True), []) == []


def test_one_of_two_matching_spans_failing_is_reported():
    exp = {"spans": [{"name": "chat gemma4:12b-mlx",
                      "required_attributes": {"gen_ai.operation.name": "chat"}}]}
    failures = check(exp, _duplicate_named_spans(both_satisfy=False), [])
    assert len(failures) == 1
    # The failing span must be distinguishable from the passing one.
    assert "bbbb000000000002" in failures[0]


def test_span_count_matches_expected():
    exp = {"spans": [{"name": "chat gemma4:12b-mlx", "count": 2}]}
    assert check(exp, _duplicate_named_spans(both_satisfy=True), []) == []


def test_span_count_mismatch_is_reported():
    exp = {"spans": [{"name": "chat gemma4:12b-mlx", "count": 3}]}
    failures = check(exp, _duplicate_named_spans(both_satisfy=True), [])
    assert len(failures) == 1
    assert "expected count 3" in failures[0]
    assert "found 2" in failures[0]


# -- schema validation: unrecognized keys must fail, never silently skip ----


def test_unknown_top_level_key_is_reported():
    exp = {"span": [{"name": "chat gemma4:12b-mlx"}]}  # `span`, not `spans`
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "span" in failures[0]
    assert "unknown" in failures[0].lower()


def test_unknown_span_key_missing_d_is_reported():
    exp = {"spans": [{"name": "invoke_agent travel-concierge",
                      "require_attributes": {"gen_ai.operation.name": "invoke_agent"}}]}
    failures = check(exp, _spans(), [])
    assert any("require_attributes" in f for f in failures)


def test_unknown_span_key_singular_forbidden_attribute_is_reported():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "forbidden_attribute": ["gen_ai.usage.input_tokens"]}]}
    failures = check(exp, _spans(), [])
    assert any("forbidden_attribute" in f for f in failures)


def test_unknown_operation_key_is_reported():
    ops = [{"operation_name": "chat", "provider_name": "openai"}]
    exp = {"operations": [{"operation_name": "chat",
                           "require_columns": {"provider_name": "openai"}}]}
    failures = check(exp, [], ops)
    assert any("require_columns" in f for f in failures)


def test_numeric_attributes_non_dict_predicate_is_reported():
    """`numeric_attributes: {tokens: 5}` used to crash with
    `TypeError: argument of type 'int' is not iterable`."""
    spans = [{
        "name": "execute_tool get_weather",
        "span_id": "aaaa000000000002",
        "parent_span_id": None,
        "service_name": "probe",
        "attributes": {"gen_ai.usage.input_tokens": "69"},
    }]
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "numeric_attributes": {"gen_ai.usage.input_tokens": 5}}]}
    failures = check(exp, spans, [])
    assert len(failures) == 1
    assert "mapping" in failures[0]


def test_numeric_attributes_unknown_operator_is_reported():
    exp = {"spans": [{"name": "execute_tool get_weather",
                      "numeric_attributes": {"gen_ai.usage.input_tokens": {"gtt": 0}}}]}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "gtt" in failures[0]


def test_operations_row_requirements():
    ops = [{"operation_name": "chat", "provider_name": "openai", "input_tokens": 69}]
    exp = {"operations": [{"operation_name": "chat",
                           "required_columns": {"provider_name": "openai"},
                           "non_null_columns": ["input_tokens"]}]}
    assert check(exp, [], ops) == []


def test_operations_null_column_is_reported():
    ops = [{"operation_name": "chat", "provider_name": None}]
    exp = {"operations": [{"operation_name": "chat",
                           "non_null_columns": ["provider_name"]}]}
    failures = check(exp, [], ops)
    assert len(failures) == 1
    assert "provider_name" in failures[0]


# -- minimum_operations: registered key + type-checking (shared with
# -- minimum_spans) -----------------------------------------------------------


def test_minimum_operations_key_is_accepted_at_top_level():
    """Before this key was registered in _TOP_LEVEL_KEYS, any expectations
    file using it would fail with 'unknown top-level expectation key' even
    though the value itself was fine."""
    exp = {"minimum_operations": 3, "operations": [{"operation_name": "chat"}]}
    ops = [{"operation_name": "chat"}]
    assert check(exp, [], ops) == []


def test_minimum_spans_wrong_type_is_reported():
    exp = {"minimum_spans": "8"}  # quoted by mistake — a str, not an int
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "minimum_spans" in failures[0]
    assert "integer" in failures[0]


def test_minimum_operations_wrong_type_is_reported():
    exp = {"minimum_operations": "3"}
    failures = check(exp, [], [])
    assert len(failures) == 1
    assert "minimum_operations" in failures[0]
    assert "integer" in failures[0]


def test_minimum_spans_bool_is_rejected():
    """`isinstance(True, int)` is True in Python, so this must be checked
    explicitly — otherwise `minimum_spans: true` (a likely typo for an int)
    would silently poll for 'at least 1' instead of failing."""
    exp = {"minimum_spans": True}
    failures = check(exp, _spans(), [])
    assert len(failures) == 1
    assert "minimum_spans" in failures[0]


def test_minimum_operations_bool_is_rejected():
    exp = {"minimum_operations": False}
    failures = check(exp, [], [])
    assert len(failures) == 1
    assert "minimum_operations" in failures[0]


def test_both_minimum_keys_wrong_type_are_both_reported():
    exp = {"minimum_spans": "8", "minimum_operations": "3"}
    failures = check(exp, [], [])
    assert len(failures) == 2
    joined = " ".join(failures)
    assert "minimum_spans" in joined
    assert "minimum_operations" in joined


def test_minimum_keys_absent_is_not_reported():
    assert validate_minimums({}) == []


def test_minimum_keys_valid_ints_are_not_reported():
    assert validate_minimums({"minimum_spans": 8, "minimum_operations": 3}) == []
