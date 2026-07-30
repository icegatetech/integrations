# python/openai-ollama

## What it proves

That an agent's real structure — not just one auto-instrumented API call —
shows up correctly in IceGate as nested spans and a typed `operations`
projection, using the OpenTelemetry GenAI semantic conventions
(`gen_ai_latest_experimental`) against a local Ollama model through its
OpenAI-compatible surface.

Three layers, built up in order:

1. **Article Step 1** — one auto-instrumented `chat` span
   (`opentelemetry-instrumentation-openai-v2`), exported over OTLP gRPC.
2. **Article Step 2** — manual `invoke_agent`/`execute_tool` spans wrapping
   the auto-instrumented `chat` spans, so the trace shows the agent's actual
   shape: a planning call that is forced to request a tool, the tool
   executing, and a final call that turns the tool result into an answer.
3. **Conversation grouping** (this recipe's current state, not covered by
   the article at all) — `recipe.py` now
   runs the agent twice, as two turns of one conversation. Each turn is a
   separate trace (a fresh root span with no active parent), so this is the
   smallest case that actually exercises IceGate's cross-trace grouping:
   both `invoke_agent` calls, and every span nested under either of them —
   including the auto-instrumented `chat` spans — carry the same
   `gen_ai.conversation.id`. See "Conversation grouping" below.

The one tool (`tools.py`, `get_weather`) returns a fixed, deterministic
result, so verification never depends on what the weather actually is.

## Prerequisites

- [Ollama](https://ollama.com) running locally with `ollama pull gemma4:12b-mlx`
- IceGate running locally (OTLP gRPC on `4317`, Flight SQL on `8815`)

Check both at once from the repo root:

    make doctor

## Running

    cp .env.example .env
    set -a && . ./.env && set +a
    uv run python -m recipe

Prints each turn's answer in order, then `ICEGATE_CONVERSATION_ID=<id>` and
`ICEGATE_RUN_ID=<hex>` as the last two lines.

## Verifying

From the repo root, in one step (runs `doctor`, then the recipe, then
asserts the telemetry that landed in IceGate against `expectations.yaml`):

    make verify RECIPE=python/openai-ollama

A real verified run: 8 spans, 8 `operations` rows (two turns × 4 spans each),
`PASSED`.

## Zero-code instrumentation

The article's headline claim: instrument an existing app with no code
changes at all, via `opentelemetry-instrument`. `app_zerocode.py` is a
second, separate entrypoint that tests exactly this — it contains **no
OpenTelemetry imports of any kind** (`import os` and `from openai import
OpenAI`, nothing else; grep it yourself, the only mentions of
"opentelemetry" are in its docstring's prose). All instrumentation comes
from the launcher wrapping the process from outside.

The launcher path has no application code to stamp `icegate.run_id` into —
doing that would require importing the OTel SDK, which defeats the entire
point of this file. `OTEL_RESOURCE_ATTRIBUTES` carries it in from the shell
instead; the launcher's auto-configured `Resource` reads that variable on
its own.

Exact working command (run from this directory):

    RUN_ID=$(python3 -c "import uuid;print(uuid.uuid4().hex)") \
      && echo "run_id=${RUN_ID}" \
      && OTEL_RESOURCE_ATTRIBUTES="icegate.run_id=${RUN_ID}" \
         OTEL_SERVICE_NAME=openai-ollama-python-zerocode \
         OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
         OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
         OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental \
         uv run opentelemetry-instrument python app_zerocode.py

**This is the command that actually worked, first attempt, with the pinned
versions in `pyproject.toml`** (`opentelemetry-distro` 0.65b0,
`opentelemetry-instrumentation-openai-v2` 2.4b0) on Python 3.14.5 — not an
idealised version of it. No `opentelemetry-bootstrap` step, no protocol
workaround, no exporter errors: run twice, stderr was 0 bytes both times.
Confirmed by direct query against IceGate, run
`ce4d3128e6014789ac16b2f3675fdf24`: one `chat gemma4:12b-mlx` span, service
name `openai-ollama-python-zerocode`, no parent span, and its
`resource_attributes` carry both `icegate.run_id=ce4d3128e6014789ac16b2f3675fdf24`
and `service.name=openai-ollama-python-zerocode` — confirming
`OTEL_RESOURCE_ATTRIBUTES` really does reach IceGate through the launcher
path with no application code involved. The zero-code claim holds.
Zero code changes is not zero dependencies to declare: those packages still
have to be installed (`uv sync` reads them from `pyproject.toml`) before the
launcher has anything to discover — this command was never run against a
bare environment, and won't work against one.

This validates the article's Step 1 claim specifically — one
auto-instrumented call, zero imports. It says nothing about the manual
`invoke_agent`/`execute_tool` structure elsewhere in this recipe (`agent.py`,
Task 4): those spans come from explicit `tracer.start_as_current_span()`
calls in application code, which auto-instrumentation cannot infer from
nothing. The two approaches are not in tension — Step 1 zero-code and Step 2
manual agent structure are different depths of the same article, and this
recipe demonstrates both, separately, on purpose.

## Token usage and cost

Article Step 3: token counts and cost. Cost is computed **at query time**,
by joining stored telemetry against IceGate's own `iceberg.icegate.prices`
table — nothing is hardcoded in Python. Ollama is free to run locally, so a
hand-written Python price table could only *demonstrate* cost by inventing
numbers; querying `prices` instead means the same SQL keeps working
unchanged the day a real paid model — and real prices — show up in the same
run.

Three files, in `queries/`, each taking the run id as a `$1` placeholder
(IceGate's Flight SQL layer is DataFusion — `$1`, not `?`; see
`verify/icegate_query.py`):

- `token_usage.sql` — tokens per model for one run, read from `operations`
  (typed integers), not `spans` (`span_attributes` is `Map<String,String>`,
  so token counts there arrive as strings).
- `cost_per_run.sql` — the production query: `operations` joined to
  `prices` by `(provider, model)`. This is the query a real deployment
  should run, but it assumes at most one active price row per
  `(model, provider)`. `prices` also carries `valid_from`, `service_tier`,
  `region`, and min/max input-token columns specifically so a model can
  have several price rows, and if more than one ever matches, the `LEFT
  JOIN` fans out before aggregation and inflates the summed token counts
  as well as the cost — see the SQL comment for the fix once `prices`
  holds multiple rows per model.
- `cost_per_run_worked_example.sql` — the same arithmetic, but with rates
  supplied inline through a `WITH rates AS (...)` CTE instead of joined
  from `prices`, for when `prices` is empty.

### Real output

Run against `ICEGATE_RUN_ID=d1a2c385b1b3416691213133c0d9a9c2` (4 spans, 4
`operations` rows, `make verify` PASSED), via:

    cd verify && uv run python -c "
    import sys, icegate_query
    run_id = sys.argv[1]
    sql = open(sys.argv[2]).read()
    c = icegate_query.connect()
    with c.cursor() as cur:
        cur.execute(sql, (run_id,))
        print([d[0] for d in cur.description])
        for r in cur.fetchall(): print(r)
    " d1a2c385b1b3416691213133c0d9a9c2 ../python/openai-ollama/queries/<file>.sql

`token_usage.sql`:

```
['request_model', 'calls', 'input_tokens', 'output_tokens', 'total_tokens']
('gemma4:12b-mlx', 2, 185, 458, None)
```

`total_tokens` is genuinely `NULL` here — not a query bug. Checked directly
against the raw spans for this run: both `chat` spans carry
`gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`, but neither
carries a `gen_ai.usage.total_tokens` attribute — because the OpenTelemetry
GenAI semantic conventions (`gen_ai_latest_experimental`) don't define one.
Only `input_tokens` and `output_tokens` exist in that convention; no version
of an OTel-GenAI-compliant instrumentation will ever emit a combined total.
IceGate's `operations.total_tokens` column exists to serve other
conventions (OpenInference, Traceloop) that do supply one. For both of
these rows, `operations.total_tokens` has nothing to read from, so `sum()`
over all-`NULL` input is `NULL`, not `0` — which is why the aggregate comes
back `NULL` rather than a misleadingly precise-looking `0`. (This was
checked directly for these two `chat` spans, not against every `operations`
row IceGate has ever held.) A caller that wants a total should add
`input_tokens + output_tokens` itself.

`cost_per_run.sql`:

```
['request_model', 'input_tokens', 'output_tokens', 'input_cost_usd', 'output_cost_usd', 'total_cost_usd', 'currency']
('gemma4:12b-mlx', 185, 458, None, None, None, None)
```

Token counts are real (they match `token_usage.sql`); every cost column and
`currency` is `NULL`. Not a bug — `iceberg.icegate.prices` holds **zero
rows** on a stock IceGate. The table exists (22 columns, confirmed by
querying it directly before writing this SQL: `provider`, `model`,
`canonical_id`, `input_usd_per_1m`, `output_usd_per_1m`, `currency`,
`valid_from`, plus cache/reasoning/image/audio price tiers) but IceGate's
pricing crawler defaults to `enabled: false`
(`crates/icegate-maintain/src/pricing/config.rs` in the sibling `icegate`
repo); enabling it — out of scope here — would have it crawl OpenRouter and
LiteLLM every 6 hours to populate `prices`. Populating `prices` is an
IceGate deployment concern, not something this recipe can or should work
around, and a local Ollama model would have no price row regardless, since
it isn't a paid API. The `LEFT JOIN` to `prices` is what makes the empty
table visible here: an inner join would have silently dropped the row
instead of showing real token counts next to `NULL` costs.

`cost_per_run_worked_example.sql`:

```
['request_model', 'input_tokens', 'output_tokens', 'input_cost_usd', 'output_cost_usd', 'total_cost_usd']
('gemma4:12b-mlx', 185, 458, 2.7749999999999997e-05, 0.0002748, 0.00030255)
```

**The rates in this query ($0.15 / 1M input tokens, $0.60 / 1M output
tokens) are illustrative stand-ins, not real pricing for `gemma4:12b-mlx`
or any other model or provider.** They exist only so the arithmetic is
checkable by hand: 185 ÷ 1,000,000 × 0.15 = 0.00002775;
458 ÷ 1,000,000 × 0.60 = 0.0002748; their sum is 0.00030255 (the tiny
`...997e-05` tail above is ordinary floating-point representation, not a
different number). Do not read these numbers as what this run actually
cost — `gemma4:12b-mlx` runs locally on Ollama and costs nothing to run.
Presenting invented numbers as real pricing would be worse than showing the
`NULL`s above; that is why this file exists as a separate, clearly-labelled
query rather than a fallback baked into `cost_per_run.sql` itself.

### The payoff

Cost is a query-time concern, not something baked into the Python at export
time. Nothing in `agent.py`, `recipe.py`, or `telemetry.py` mentions price.
Whether the model changes, a provider's rates change, or `prices` goes from
empty to populated, `cost_per_run.sql` is the only thing that ever needs to
change — never the instrumentation, and never a re-run of the recipe. That
is also this task's correction to the source article: Step 3 presents token
usage and cost as something that "come for free" once tokens are captured,
but on a stock IceGate deployment there is no price data at all — cost
needs a populated price source before it comes from anywhere.

## Content capture (opt-in) and redaction

Off by default — prompts and responses routinely contain PII, so nothing is
captured unless you opt in. The toggle is
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, and `.env.example`
ships it set to `NO_CONTENT`.

**This is an enum, not a boolean.** Valid values: `NO_CONTENT`, `SPAN_ONLY`,
`EVENT_ONLY`, `SPAN_AND_EVENT` (case-insensitive). The source article's Step
4 says to set it `=true` — that is not a recognized value. Under
`gen_ai_latest_experimental`, this instrumentation parses the variable with
`ContentCapturingMode[value.upper()]`; `true` isn't a member, so the lookup
raises internally, the library catches it, logs one warning, and falls back
to `NO_CONTENT` — silently capturing nothing. Confirmed directly: running
with `=false` reproduced exactly this failure (the warning, the fallback,
nothing captured); `=true` was not separately run but fails through the
same lookup for the same reason. The fix: use `SPAN_ONLY`.

To run with capture on:

    cd python/openai-ollama \
      && OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_ONLY uv run python -m recipe

then verify the printed run id against the capture-specific expectations
file (not the default `expectations.yaml`, which asserts the *opposite* —
that no content leaks):

    cd verify && uv run python -m assert_runner \
      ../python/openai-ollama/expectations.capture.yaml <run_id>

Confirmed on a real run (`SPAN_ONLY`): `gen_ai.input.messages` and
`gen_ai.output.messages` do appear on both `chat` spans, and IceGate's typed
`operations.input_messages` / `operations.output_messages` columns are
populated too — the article's claim that content lands on span attributes
holds for `opentelemetry-instrumentation-openai-v2` 2.4b0; only its `=true`
value was wrong.

**Redaction (`redaction.py`) runs before any of this** — inside `agent.py`,
before `client.chat.completions.create()` is ever called, not afterward on
the span. `recipe.py`'s prompt carries a deliberately-planted email address
(`ops-oncall@example.com`) so this is observable: with capture on, the
captured `gen_ai.input.messages` contains the marker `[REDACTED_EMAIL]` and
never the raw address — `expectations.capture.yaml` asserts both
(`contains_attributes` for the marker, `not_contains_attributes` for the raw
value; the second is the one that actually matters, since a marker could in
principle be present *alongside* a leaked original). See "Divergences" below
for why redaction happens here instead of at a Collector, and what that
trade-off costs.

## Conversation grouping

The article never mentions this. IceGate reads
`operations.conversation_id` from the `gen_ai.conversation.id` **span**
attribute
(`crates/icegate-ingest/src/transform/operations/otel.rs:47` in the sibling
`icegate` repo — OpenInference's equivalent is `session.id`), and its schema
comment says this column "groups a multi-turn conversation's operations
**across traces**." A single turn is a single trace and cannot demonstrate
that at all — which is why `recipe.py` runs the agent twice under one
conversation id instead of once.

**Why a span processor, not a resource attribute.** Span attributes don't
inherit, so setting `gen_ai.conversation.id` only on the manual
`invoke_agent` span would leave every auto-instrumented `chat` span NULL —
exactly the gap that would defeat the grouping. A resource attribute would
cover every span, but resources are process-wide and immutable once the
`TracerProvider` is built, while conversations are neither — that would
teach the wrong pattern in a reference recipe. `conversation.py` instead
stamps the attribute from `ConversationSpanProcessor.on_start`, reading a
`ContextVar` that `agent.run()` activates for the duration of each turn.
`on_start` runs while the span is still live, so `span.set_attribute()` is
ordinary public API — no private OpenTelemetry internals involved (contrast
with `redaction.py`/Task 5, which could not use this approach, since it
needs to change outbound content before the provider ever sees it, not
annotate a span after the fact).

**Confirmed in the data, not just in the code.** A real two-turn run
(`ICEGATE_RUN_ID=577f5bae3c884a5c89dc47b30c6d0e02`,
`ICEGATE_CONVERSATION_ID=conv-bc3ceb84b4124ce190db2d39e1baad52`) queried
directly against `operations`, joined to `spans` for the run-id filter:

```sql
SELECT o.operation_name, o.trace_id, o.conversation_id
FROM iceberg.icegate.operations o
JOIN iceberg.icegate.spans s ON o.span_id = s.span_id
WHERE array_element(
          map_extract(s.resource_attributes, 'icegate.run_id'), 1
      ) = $1
ORDER BY o.trace_id, o.operation_name
```

```
('chat',         'd1161d75cae6b17e5771e4cf3ca9685c', 'conv-bc3ceb84b4124ce190db2d39e1baad52')
('chat',         'd1161d75cae6b17e5771e4cf3ca9685c', 'conv-bc3ceb84b4124ce190db2d39e1baad52')
('execute_tool', 'd1161d75cae6b17e5771e4cf3ca9685c', 'conv-bc3ceb84b4124ce190db2d39e1baad52')
('invoke_agent', 'd1161d75cae6b17e5771e4cf3ca9685c', 'conv-bc3ceb84b4124ce190db2d39e1baad52')
('chat',         'e0fd6bbe563df30af5c91e862fdfc67f', 'conv-bc3ceb84b4124ce190db2d39e1baad52')
('chat',         'e0fd6bbe563df30af5c91e862fdfc67f', 'conv-bc3ceb84b4124ce190db2d39e1baad52')
('execute_tool', 'e0fd6bbe563df30af5c91e862fdfc67f', 'conv-bc3ceb84b4124ce190db2d39e1baad52')
('invoke_agent', 'e0fd6bbe563df30af5c91e862fdfc67f', 'conv-bc3ceb84b4124ce190db2d39e1baad52')
```

Two distinct `trace_id`s (`d1161d75...`, `e0fd6bbe...`), one shared
`conversation_id` — including on the `chat` rows, which are the
auto-instrumented spans this recipe's own code never sets
`gen_ai.conversation.id` on directly. That is the actual proof the design
works: the raw `span_attributes` map carries the same key on all four `chat`
spans too, confirmed by querying `spans` directly, not only the typed
`operations` projection.

## Expected trace tree

Two turns, two traces, one conversation:

```
invoke_agent travel-concierge        (turn 1 — trace A)
├─ chat gemma4:12b-mlx                  (planning call — tool_choice="required")
├─ execute_tool get_weather
└─ chat gemma4:12b-mlx                  (final call — returns the answer)

invoke_agent travel-concierge        (turn 2 — trace B, same conversation)
├─ chat gemma4:12b-mlx                  (planning call — tool_choice="required")
├─ execute_tool get_weather
└─ chat gemma4:12b-mlx                  (final call — returns the answer)
```

Every span above carries `gen_ai.conversation.id`, the same value on both
trees. `expectations.yaml` asserts `count: 2` on `invoke_agent`/`execute_tool`
and `count: 4` on `chat` so a nesting or turn-count bug on any of them is
caught, `parent: invoke_agent travel-concierge` is checked for all six
children against either valid parent (not assumed from the code), and
`present_attributes: [gen_ai.conversation.id]` is asserted on every span
expectation, `chat` included.

A real verified run: 8 spans (`invoke_agent` ×2, `execute_tool` ×2, `chat`
×4), 8 `operations` rows — one per span, confirmed no join fan-out. Same
shape with content capture on (`SPAN_ONLY`, `expectations.capture.yaml`):
still 8 spans, 8 operations rows — capture adds attributes to the existing
`chat` spans, it doesn't add new spans. Both turns' prompts plant the
same `ops-oncall@example.com` marker specifically so the capture file's
redaction checks (`contains_attributes`/`not_contains_attributes`) stay
valid against all four `chat` spans, not just the first turn's.

## Divergences from the article

- **Endpoint and protocol.** The article pairs port `4318` with
  `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` — an internally contradictory
  pairing. `4317` is the gRPC port; this recipe (`telemetry.py`) speaks gRPC
  to `4317` throughout, and `OTEL_SEMCONV_STABILITY_OPT_IN` is verified
  present *before* the OpenTelemetry instrumentation loads (it's read at
  import time, so setting it from Python after import is too late — the
  recipe fails loudly instead of silently emitting the old attribute names).

- **`gen_ai.provider.name`, not `gen_ai.system`.** Under
  `gen_ai_latest_experimental`, the current attribute is
  `gen_ai.provider.name`; the article uses the older `gen_ai.system`. We
  tested this directly: temporarily emitting `gen_ai.system` instead of
  `gen_ai.provider.name` on the manual `invoke_agent` span, `make verify`
  correctly **failed** — the raw span never gets a `gen_ai.provider.name` key
  from `gen_ai.system` alone, which is what the span-level assertion checks.
  The surprising part: IceGate's *typed* `operations.provider_name` column
  was **not** NULL in that experiment — it still read `'openai'`, evidently
  falling back to the legacy `gen_ai.system` key when the new one is absent
  on that same span. So `gen_ai.provider.name` is still the attribute to
  emit (it's the current spec, and the only key that's actually present on
  the raw span for any consumer other than IceGate's lenient projection),
  but "does `operations.provider_name` end up NULL" is not, by itself, a
  reliable test for the correct attribute name in this IceGate build — the
  span-level check is what actually caught the regression.

- **Added ids the article omits.** `gen_ai.agent.id` on the `invoke_agent`
  span, and `gen_ai.tool.call.id` on the `execute_tool` span — Ollama's
  OpenAI-compatible endpoint returns a real tool-call id (e.g.
  `call_38bdvo2a`), and IceGate has typed columns for both
  (`operations.agent_id`, `operations.tool_call_id`), confirmed non-NULL on
  a real run.

- **`tool_choice="required"` on the planning call only.** The article's
  pseudocode doesn't address this. Setting it on the *final* call too would
  force the model to request another tool call every time, looping forever
  — `agent.py` sets it only on the first `client.chat.completions.create()`
  call, and omits it on the second.

- **Sampling parameters and the sampling columns.** Neither
  `client.chat.completions.create()` call passes `temperature`, `top_p`, or
  `top_k` — Ollama applies the model's own defaults. Confirmed on a real
  run: `operations.temperature`/`top_p`/`top_k` are all `NULL` on both
  `chat` rows. This is the correct result of sending nothing, not a gap —
  but read precisely: these columns record what the *client* sent, so NULL
  is not evidence about what sampling configuration Ollama actually applied
  server-side. The sampling configuration in force is not observable from
  telemetry when it comes from server-side (e.g. Modelfile) defaults;
  recording it in `operations` requires setting it explicitly on the
  request, which means accepting an override of the model's own defaults.

- **`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` is an enum, not a
  boolean — the article's Step 4 value (`=true`) captures nothing.** Under
  `gen_ai_latest_experimental`, this instrumentation parses the variable
  with `ContentCapturingMode[value.upper()]`; only `NO_CONTENT`, `SPAN_ONLY`,
  `EVENT_ONLY`, and `SPAN_AND_EVENT` are valid. `true`/`false` raise a
  `KeyError` internally, which the library catches, logs a warning, and
  falls back to `NO_CONTENT` — silently capturing nothing. The fix is
  `SPAN_ONLY`, not `true`. What the article gets right, confirmed on a real
  run: with the correct enum value, content genuinely lands as span
  attributes (`gen_ai.input.messages`/`gen_ai.output.messages`), not only as
  log events. The captured shape is a `parts`-based envelope, not the flat
  `{role, content}` shape the Chat Completions API itself uses — e.g.
  `gen_ai.input.messages` captures as
  `[{"role":"user","parts":[{"content":"...","type":"text"}]}]`.

- **Redaction happens in-process, before the model call — not at a
  Collector.** The article redacts at the OpenTelemetry Collector, after
  telemetry is already emitted. This recipe exports straight to IceGate with
  no Collector in the path, so `redaction.py` sanitises `user_message`
  inside `agent.py` *before* `client.chat.completions.create()` is called:
  the raw value never reaches the provider or any telemetry representation,
  and nothing needs to reach into private OpenTelemetry internals (no
  `span._attributes` access). The trade-off is real and deliberately
  narrower than the article's approach: this cannot scrub content the
  *model itself returns* (only the outbound prompt is sanitised), and it
  only protects this one process — it does not redact for every service
  emitting telemetry the way a shared Collector processor would. For either
  of those, a Collector-based redaction processor is still the right
  production answer; this recipe's approach is a narrower, dependency-free
  alternative that happens to be sufficient for a single recipe's outbound
  prompt.

- **Conversation grouping is not in the article at all.** The article never
  mentions `gen_ai.conversation.id` or multi-turn grouping, despite IceGate's
  schema explicitly supporting it. `recipe.py`/`conversation.py` add it as a
  pure addition — see "Conversation grouping" above.
