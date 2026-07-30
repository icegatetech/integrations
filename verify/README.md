# Verification harness

Language-agnostic. It asserts on data that landed in IceGate, not on recipe
internals, so the same harness verifies Python and TypeScript recipes
identically.

## Two layers

1. **`spans`** — did we emit it? All attribute values are strings here, because
   `span_attributes` is `Map<String, String>`.
2. **`operations`** — did IceGate understand it? This is IceGate's typed
   projection over GenAI spans, with real integers and proper NULLs. Assertions
   about token counts and provider identity belong here.

## Running the harness's own tests

    uv run --group dev pytest tests/ -v

`tests/test_assert_spans.py` is pure logic and needs nothing running.
`tests/test_icegate_query.py` needs IceGate up.

## `expectations.yaml` schema

Every recipe ships an `expectations.yaml` (see `CONVENTIONS.md`). It has no
separate schema file — `assert_spans.check()` validates it directly, and
rejects any key outside this list as a failure (never a silent skip).

Top level:

| Key | Type | Meaning |
|---|---|---|
| `spans` | list of span expectations | see below |
| `operations` | list of operation expectations | see below |
| `minimum_spans` | int | passed to `wait_for_spans` before assertions run; default `1` |
| `minimum_operations` | int | passed to `wait_for_operations` before assertions run; default: the number of distinct `operation_name` values under `operations` (`0`, i.e. no wait, if there are none) |

### Why `minimum_operations`

`spans` and `operations` land independently — `operations` is a separate
typed projection, written on its own ingest path, not derived by re-reading
`spans` at query time. `wait_for_spans` returning successfully says nothing
about whether operations rows for the same run have arrived yet. Querying
`operations` right after with no wait of its own reproduces two distinct
failures: a loud false FAIL (zero rows so far, though they land moments
later) and a silent false PASS (a partial set of rows already satisfies
every `operations[]` expectation, since each entry only requires *one*
matching row per `operation_name` — there is no per-entry `count` the way
`spans[]` has one — so the assertions can't distinguish "all rows in" from
"just enough got lucky"). `wait_for_operations` polls the same way
`wait_for_spans` does, so `assert_runner` waits for both projections before
asserting on either.

### `spans[]`

Each entry describes every span that shares a given `name` — if two spans
share a name, **all of them** must satisfy **every** predicate below; a
failure names which one (index and span id) failed.

| Key | Type | Meaning |
|---|---|---|
| `name` | string | required. Matches every span with this exact name. |
| `parent` | string | the span's `parent_span_id` must belong to a span with this name |
| `count` | int | exactly this many spans must share `name` |
| `required_attributes` | map of string → value | attribute must be present and equal this value |
| `present_attributes` | list of string | attribute must be present (any value) |
| `forbidden_attributes` | list of string | attribute must NOT be present (e.g. unredacted PII) |
| `contains_attributes` | map of string → string | attribute must be present and contain this substring |
| `not_contains_attributes` | map of string → string | if present, attribute must NOT contain this substring |
| `numeric_attributes` | map of string → predicate | attribute is cast with `float()`, then compared; predicate is a map with one or more of `gt`, `gte`, `lt`, `lte`, `eq` |

All values in `span_attributes` are strings (`Map<String, String>` in
IceGate), so `required_attributes`/`contains_attributes` compare against
strings. A YAML `true`/`false` in `required_attributes` is normalized to
match OpenTelemetry's lowercase `"true"`/`"false"` string encoding.

### `operations[]`

Each entry describes every row in the typed `operations` projection whose
`operation_name` matches — again, **all matching rows** must satisfy every
column predicate; that's the intended default (IceGate can legitimately
produce more than one row per operation name), not a bug, so failures are
labelled per-row when there's more than one.

| Key | Type | Meaning |
|---|---|---|
| `operation_name` | string | required. Selects every row with this `operation_name`. |
| `required_columns` | map of string → value | column must equal this value |
| `non_null_columns` | list of string | column must be non-NULL |
| `null_columns` | list of string | column must be NULL |

Unlike `spans`, columns here come from `operations`'s typed projection, so
values may already be real integers/booleans rather than strings.

### Worked example

```yaml
minimum_spans: 2
minimum_operations: 1  # optional — this is already the default here: 1
                        # distinct operation_name ("chat") below

spans:
  - name: "chat gemma4:12b-mlx"
    count: 1
    required_attributes:
      gen_ai.operation.name: "chat"
      gen_ai.provider.name: "openai"
    forbidden_attributes:
      - gen_ai.input.messages   # only present if content capture is opted in
    numeric_attributes:
      gen_ai.usage.input_tokens:
        gt: 0

  - name: "execute_tool get_weather"
    parent: "chat gemma4:12b-mlx"
    contains_attributes:
      gen_ai.tool.name: "weather"

operations:
  - operation_name: "chat"
    required_columns:
      provider_name: "openai"
    non_null_columns:
      - input_tokens
      - output_tokens
```

`gen_ai.provider.name`/`provider_name` read `"openai"` here even though the
model is served by Ollama: the instrumentation names the SDK/protocol
surface it patches (the OpenAI-compatible Chat Completions API), not the
backend actually serving the request.
