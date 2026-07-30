# typescript/openai-ollama

## What it proves

The same claim as `python/openai-ollama`, from the Node/TypeScript side: an
agent's real structure — not just one auto-instrumented API call — shows up
correctly in IceGate as nested spans and a typed `operations` projection,
using the OpenTelemetry GenAI semantic conventions against a local Ollama
model through its OpenAI-compatible surface.

The agent shape mirrors the Python recipe exactly: same agent name
(`travel-concierge`), same one tool (`get_weather`, fixed/deterministic
result), same span names, same trace tree — run twice, as two turns of one
conversation (see "Conversation grouping" below):

```
invoke_agent travel-concierge        (turn 1 — trace A)
├─ chat gemma4:12b-mlx                  (planning call — tool_choice: 'required')
├─ execute_tool get_weather
└─ chat gemma4:12b-mlx                  (final call — returns the answer)

invoke_agent travel-concierge        (turn 2 — trace B, same conversation)
├─ chat gemma4:12b-mlx                  (planning call — tool_choice: 'required')
├─ execute_tool get_weather
└─ chat gemma4:12b-mlx                  (final call — returns the answer)
```

`invoke_agent`/`execute_tool` are manual spans
(`tracer.startActiveSpan(...)`); both `chat` spans in each tree come from
`@opentelemetry/instrumentation-openai` auto-patching
`OpenAI.Chat.Completions.prototype.create`.

## Prerequisites

- [Ollama](https://ollama.com) running locally with `ollama pull gemma4:12b-mlx`
- IceGate running locally (OTLP gRPC on `4317`, Flight SQL on `8815`)
- Node 26 (developed and verified against `v26.0.0`)

Check Ollama/IceGate at once from the repo root:

    make doctor

## Running

    cd typescript/openai-ollama
    npm install
    cp .env.example .env
    set -a && . ./.env && set +a
    npm start

Prints each turn's answer in order, then `ICEGATE_CONVERSATION_ID=<id>` and
`ICEGATE_RUN_ID=<hex>` as the last two lines. A harmless `ExperimentalWarning`
about `--experimental-loader` appears first, on stderr — see "The ESM
instrumentation gotcha" below for why that flag is there; it does not affect
the run id line or the harness's ability to parse it
(`scripts/run_and_verify.sh` takes the *last* line matching
`^ICEGATE_RUN_ID=`, so extra warning noise elsewhere in the stream is fine).

`npm test` runs `conversation.ts`'s unit tests
(`src/conversation.test.ts`, Node's built-in test runner — no new
dependency) without needing Ollama or IceGate at all: they exercise
`currentId`/`withConversation`/`ConversationSpanProcessor` against fakes,
the same way `python/openai-ollama/test_conversation.py` does.

## Verifying

From the repo root, in one step (runs `doctor`, then `npm start`, then
asserts the telemetry that landed in IceGate against `expectations.yaml`):

    make verify RECIPE=typescript/openai-ollama

Real output, two turns:

    8 spans, 4 operations rows for run 3e441d7ec1054854ab58fa812be9fd87
    PASSED

**The `4 operations rows` (not `8`) is IceGate ingestion lag, not a bug —
confirmed, not assumed.** `verify/assert_runner.py` polls for spans
(`wait_for_spans`) but queries `operations` exactly once, with no retry
(`icegate_query.query_operations`, called straight after the span poll
succeeds). `operations` is IceGate's typed projection *derived from* spans,
and that derivation trails span visibility by a few seconds under load.
Doubling the span count via two-turn conversations makes this pre-existing
harness gap far more visible than it was for the original one-turn, 4-span
recipe (Task 8 saw `4 spans, 4 operations` cleanly, twice). Re-querying
`operations` for this exact `run_id` a few seconds later, with no code
change and no re-run, returned all 8 rows, correctly split 4-and-4 across
the two `trace_id`s (see "Conversation grouping" below for that query and
its output). `make verify` still reports `PASSED` either way: `assert_spans.py`
checks that every `operations` row present is valid, not that an exact
count of rows has arrived yet, so a slow-arriving row never produces a false
pass on wrong data — only a possibly-incomplete one at the moment of
querying. `verify/` is out of scope for this task to change; noted here so
a reader isn't left thinking two turns silently regressed something.

## The ESM instrumentation gotcha (load-bearing, not cosmetic)

This recipe is `"type": "module"` — plain ESM, as the brief specified. Two
separate problems had to be fixed before a single `chat` span would land;
neither is visible as a TypeScript error, and both fail *silently* (exit 0,
no error, no warning), which makes them easy to ship broken.

**1. `openai` must be a version `@opentelemetry/instrumentation-openai`
actually recognizes.** The installed instrumentation's Chat Completions patch
is registered as:

```js
new InstrumentationNodeModuleDefinition('openai', ['>=4.19.0 <7'], module => { ... })
```

(`node_modules/@opentelemetry/instrumentation-openai/build/src/instrumentation.js`).
`@opentelemetry/instrumentation`'s node-module patcher checks this range with
`semver.satisfies()` before ever calling the patch function
(`node_modules/@opentelemetry/instrumentation/build/src/platform/node/instrumentation.js`,
`isSupported(...)`) — if the installed version doesn't match, the module
loads completely unpatched, with **no log, no warning, no error**. `openai`
`7.1.0` (the version in the task brief's original `package.json`) is outside
`<7`, so with that version installed, zero `chat` spans are ever created —
confirmed empirically: `invoke_agent`/`execute_tool` (manual spans) landed,
both `chat` spans (auto-instrumented) did not, on every run tried with
`openai@7.1.0`. **Fix applied here: `openai` is pinned to `6.49.0`**, the
newest release satisfying `>=4.19.0 <7` (`npm view openai versions`). This is
a real, necessary divergence from the brief's `package.json` — not the kind
of "aligning stable/experimental OTel package numbers" the brief warned
against leaving alone; it's a genuine incompatible pin, found by reading the
installed instrumentation's own source after the recipe ran clean but
produced only 2 of the expected 4 spans.

**2. The ESM loader hook must be registered at process start, before any
module resolves — a plain function call at runtime is too late.** OpenTelemetry's
Node auto-instrumentation patches modules by intercepting `require`/`import`
resolution (`require-in-the-middle` for CommonJS, `import-in-the-middle` for
ESM). `openai` ships as CommonJS (`"type": "commonjs"` in its own
`package.json`), but this application is ESM, and `src/index.ts` reaches it
via a dynamic `await import('openai')` *after* `setup()` runs — exactly the
ordering the brief's self-review checklist calls for, to dodge static-import
hoisting. That ordering fix is necessary but was **not sufficient**: even
with the `openai` version corrected and `setup()` running first, a plain
`tsx src/index.ts` (the brief's literal `npm start` script) still produced
only 2 spans, never the two `chat` spans. The installed
`@opentelemetry/instrumentation` package's own README says why
(`node_modules/@opentelemetry/instrumentation/README.md`, "Instrumentation
for ECMAScript Modules (ESM) in Node.js"): ESM module interception requires
a loader hook registered at Node startup —
`--experimental-loader=@opentelemetry/instrumentation/hook.mjs` — because
`import-in-the-middle` has to be wired into Node's module-resolution pipeline
before that pipeline runs for the first time; calling `registerInstrumentations()`
at any point after the process has started is too late to retroactively hook
future ESM `import()` calls. Confirmed by direct A/B test: `openai@6.49.0`
plus plain `tsx src/index.ts` (no loader flag) still landed only 2 spans;
adding the loader flag alongside the corrected `openai` version is what
produced all 4. **Fix applied here:** `package.json`'s `start` script is

    node --import tsx --experimental-loader=@opentelemetry/instrumentation/hook.mjs src/index.ts

instead of the brief's `tsx src/index.ts`. This still satisfies
`CONVENTIONS.md`'s requirement (`npm start`, unchanged as the fixed
entrypoint) — only the script's own definition changed, not the contract
`scripts/run_and_verify.sh` relies on. Node prints one
`ExperimentalWarning` about the flag's future removal on every run; it is
noise, not a failure (see "Running" above).

Both fixes were necessary; neither alone was sufficient. Tested as an
explicit 2x2 during this task: `openai@7.1.0` + no loader flag → 2 spans;
`openai@7.1.0` + loader flag → 2 spans; `openai@6.49.0` + no loader flag → 2
spans; `openai@6.49.0` + loader flag → 4 spans, `make verify` PASSED.

## The semconv opt-in question — answered

The task this recipe implements existed specifically to answer: does
`@opentelemetry/instrumentation-openai` 0.19.0 honor
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`?

**No — confirmed two ways, not just observed once.**

1. **Static:** `OTEL_SEMCONV_STABILITY_OPT_IN` does not appear anywhere in
   the installed package's source
   (`grep -rn OTEL_SEMCONV_STABILITY_OPT_IN node_modules/@opentelemetry/instrumentation-openai/build/src/`
   — zero hits, across `instrumentation.js`, `responses.js`, `semconv.js`,
   `utils.js`, everything). The package simply does not read this variable.
2. **Dynamic:** with the variable set to `gen_ai_latest_experimental` in
   `.env` (confirmed loaded into the process environment before every run),
   the real `chat gemma4:12b-mlx` span attributes are:

   ```
   gen_ai.operation.name = chat
   gen_ai.request.model = gemma4:12b-mlx
   gen_ai.response.finish_reasons = [stop]          (or [tool_calls] on the planning call)
   gen_ai.response.id = chatcmpl-725
   gen_ai.response.model = gemma4:12b-mlx
   gen_ai.system = openai                            <- not gen_ai.provider.name
   gen_ai.usage.input_tokens = 79
   gen_ai.usage.output_tokens = 94
   server.address = localhost
   server.port = 11434
   ```

   `gen_ai.provider.name` is absent; `gen_ai.system` is present instead —
   the opposite of what `gen_ai_latest_experimental` specifies, and the
   opposite of what the Python recipe's instrumentation
   (`opentelemetry-instrumentation-openai-v2` 2.4b0) does under the same
   env var. `expectations.yaml` asserts this directly:
   `gen_ai.system: openai` under `required_attributes`, and
   `gen_ai.provider.name` under `forbidden_attributes` on the `chat` span —
   so the divergence is pinned down as a checked absence, not an unchecked
   possibility.

Reading why in the source explains the asymmetry precisely: this package
version has two GenAI-instrumented code paths, and only one of them was
migrated. `_startChatCompletionsSpan` (`instrumentation.js`, what this recipe
calls, since Ollama's OpenAI-compatible surface only exposes
`/v1/chat/completions`) hardcodes:

```js
const commonAttrs = {
  [ATTR_GEN_AI_OPERATION_NAME]: GEN_AI_OPERATION_NAME_VALUE_CHAT,
  [ATTR_GEN_AI_REQUEST_MODEL]: params.model,
  [ATTR_GEN_AI_SYSTEM]: GEN_AI_PROVIDER_NAME_VALUE_OPENAI,   // 'gen_ai.system'
};
```

while `_startResponsesSpan` (same file, the newer `/v1/responses` API path,
not used by this recipe or by Ollama) already uses:

```js
const commonAttrs = Object.assign({
  [ATTR_GEN_AI_OPERATION_NAME]: GEN_AI_OPERATION_NAME_VALUE_CHAT,
  [ATTR_GEN_AI_REQUEST_MODEL]: params.model,
  [ATTR_GEN_AI_PROVIDER_NAME]: GEN_AI_PROVIDER_NAME_VALUE_OPENAI,  // 'gen_ai.provider.name'
}, ...);
```

Neither path branches on `OTEL_SEMCONV_STABILITY_OPT_IN` at all — the
`Responses` path just always emits the new name, and the `Chat Completions`
path just always emits the old one. This reads as a package mid-migration,
not a deliberate opt-in gate. **This is a JS-instrumentation limitation, not
a choice made in this recipe's code** — nothing in `agent.ts` or
`telemetry.ts` sets `gen_ai.system` anywhere; the manual `invoke_agent` span
sets `gen_ai.provider.name` explicitly (matching Python, and matching this
task's constraint #4), and that attribute is exactly what lands on that
span, unaffected by the auto-instrumentation's behavior on the sibling
`chat` spans.

**The typed `operations.provider_name` column is unaffected — populated
`'openai'` on both `chat` rows regardless, confirmed by direct query.** This
is IceGate's documented provider-name fallback
(`gen_ai.provider.name` → `gen_ai.system` → `llm.system`), the same
mechanism the Python recipe's finding already established (see
`python/openai-ollama/README.md`, "Divergences from the article").
Exactly as in that finding, this makes
`operations.provider_name IS NOT NULL` an unreliable signal for *which*
attribute name was actually emitted — `expectations.yaml` still asserts it
(it's true and worth checking), but the span-level
`required_attributes`/`forbidden_attributes` checks on the raw
`gen_ai.system`/`gen_ai.provider.name` keys are what actually distinguish
this recipe's behavior from Python's.

## The known API risk that did *not* materialize

The brief flagged `telemetry.ts`'s use of `resourceFromAttributes(...)` and
a `spanProcessors: [...]` constructor option as a possible breakage point,
since these API shapes changed across OpenTelemetry JS releases. Checked
directly against the installed 2.10.0/0.221.0 packages
(`npx tsc --noEmit`, and by reading
`node_modules/@opentelemetry/resources/build/esm/index.d.ts` and
`node_modules/@opentelemetry/sdk-trace/build/src/types.d.ts`): both shapes
are exactly what the brief wrote. `resourceFromAttributes` is exported
directly from `@opentelemetry/resources` 2.10.0, and
`spanProcessors?: SpanProcessor[]` is a real field on `TracerProviderOptions`
(re-exported through `@opentelemetry/sdk-trace-base`'s compatibility shim).
`telemetry.ts` is unmodified from the brief. `npx tsc --noEmit` is clean
with zero errors — this is a real, checked result, not an assumption from
skipping the check.

## Content capture

Off by default, same toggle and same enum as Python:
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` in
`.env.example`. `expectations.yaml` asserts `gen_ai.input.messages` and
`gen_ai.output.messages` are both absent from the `chat` spans, matching
capture-off. This recipe does not implement the capture-on / redaction path
(`redaction.py`'s equivalent) — that was Python-recipe scope (Task 5) and is
out of scope for this port, which mirrors Task 4's agent/tool-span scope.

## Conversation grouping

Not in the article at all. IceGate reads `operations.conversation_id` from
the `gen_ai.conversation.id` **span** attribute
(`crates/icegate-ingest/src/transform/operations/otel.rs:47` in the sibling
`icegate` repo — OpenInference's equivalent is `session.id`), and its schema
comment says this column "groups a multi-turn conversation's operations
**across traces**." A single turn is a single trace and cannot demonstrate
that at all — which is why `index.ts` runs the agent twice under one
conversation id instead of once (see "What it proves" above).

**Why a span processor, not a resource attribute.** Span attributes don't
inherit, so setting `gen_ai.conversation.id` only on the manual
`invoke_agent` span would leave every auto-instrumented `chat` span NULL —
exactly the gap that would defeat the grouping. A resource attribute would
cover every span, but resources are process-wide and immutable once the
`NodeTracerProvider` is built, while conversations are neither. `conversation.ts`
instead stamps the attribute from `ConversationSpanProcessor.onStart`,
reading the active conversation id from an `AsyncLocalStorage` that
`agent.ts`'s `run()` activates for the duration of each turn — Node's
context-propagation primitive, the async equivalent of Python's `ContextVar`
used in `conversation.py`, and what OpenTelemetry's own Node context manager
(`@opentelemetry/context-async-hooks`) is built on internally. `onStart` runs
while the span is still live, so `span.setAttribute()` is ordinary public
API — no private OpenTelemetry internals involved.

**Confirmed in the data, not just in the code.** A real two-turn run
(`ICEGATE_RUN_ID=3e441d7ec1054854ab58fa812be9fd87`,
`ICEGATE_CONVERSATION_ID=conv-87536293d0d74d65b2a8714913fd635e`) queried
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
('chat',         '17b734248fb28c43a3d735677233d232', 'conv-87536293d0d74d65b2a8714913fd635e')
('chat',         '17b734248fb28c43a3d735677233d232', 'conv-87536293d0d74d65b2a8714913fd635e')
('execute_tool', '17b734248fb28c43a3d735677233d232', 'conv-87536293d0d74d65b2a8714913fd635e')
('invoke_agent', '17b734248fb28c43a3d735677233d232', 'conv-87536293d0d74d65b2a8714913fd635e')
('chat',         '54425b3284f92bb3f8b1248f17c660d7', 'conv-87536293d0d74d65b2a8714913fd635e')
('chat',         '54425b3284f92bb3f8b1248f17c660d7', 'conv-87536293d0d74d65b2a8714913fd635e')
('execute_tool', '54425b3284f92bb3f8b1248f17c660d7', 'conv-87536293d0d74d65b2a8714913fd635e')
('invoke_agent', '54425b3284f92bb3f8b1248f17c660d7', 'conv-87536293d0d74d65b2a8714913fd635e')
```

Two distinct `trace_id`s (`17b73424...`, `54425b32...`), one shared
`conversation_id` — including on the `chat` rows, which are the
auto-instrumented spans this recipe's own code never sets
`gen_ai.conversation.id` on directly (and which, per the semconv-opt-in
finding above, carry `gen_ai.system` rather than `gen_ai.provider.name` —
the conversation-grouping mechanism is entirely independent of that
divergence, confirmed by the same query naming `chat` rows explicitly).
That is the actual proof the design works: the raw `span_attributes` map
carries the same key on all four `chat` spans too, confirmed by querying
`spans` directly, not only the typed `operations` projection.

## Differences from the Python recipe

- **`gen_ai.system`, not `gen_ai.provider.name`, on the auto-instrumented
  `chat` spans.** See "The semconv opt-in question" above for the full
  finding. This is the headline divergence the task set out to determine,
  and it is real: `@opentelemetry/instrumentation-openai` 0.19.0 does not
  honor `OTEL_SEMCONV_STABILITY_OPT_IN` for the Chat Completions path,
  unlike `opentelemetry-instrumentation-openai-v2` 2.4b0 (Python), which
  does. `expectations.yaml` reflects this directly rather than forcing
  parity with the Python file.

- **`openai` pinned to `6.49.0`, not `7.1.0`.** Necessary so
  `@opentelemetry/instrumentation-openai` 0.19.0 recognizes the package at
  all (`supportedVersions: ['>=4.19.0 <7']`) — see "The ESM instrumentation
  gotcha" above. Python has no equivalent constraint; `openai` (the Python
  package) and `opentelemetry-instrumentation-openai-v2` version
  independently there.

- **`npm start` runs a `node` invocation with two loader flags
  (`--import tsx --experimental-loader=@opentelemetry/instrumentation/hook.mjs`),
  not a bare `tsx src/index.ts`.** ESM instrumentation in Node needs a
  loader hook registered before the process's module resolution pipeline
  runs; Python has no analogous requirement (`sitecustomize`/import hooks
  work differently, and the Python recipe's manual-instrumentation path
  doesn't rely on any loader flag — only its separate zero-code path,
  `app_zerocode.py`, uses a launcher at all, and that launcher
  is `opentelemetry-instrument`, unrelated to this issue).

- **No cost-query / zero-code-instrumentation coverage in this recipe.**
  The Python recipe grew those in later tasks (6 and 7) on top of the same
  agent/tool-span base this TypeScript recipe implements. Both are
  reasonable follow-ups for a future task, not attempted here.

- **No redaction path.** Python's `redaction.py` (Task 5) has no port here —
  this recipe's turn-2 prompt has nothing to redact, unlike Python's, which
  deliberately plants an email marker in both turns. Content capture stays
  off by default in both languages regardless (see "Content capture" above).

- **Conversation grouping matches exactly, design and mechanism both** —
  the one piece of this recipe that is *not* a divergence. Same span-processor
  approach (`ConversationSpanProcessor.onStart` stamping
  `gen_ai.conversation.id` from context, wrapping the batch exporter), same
  two-turns-one-conversation shape in `index.ts`/`recipe.py`, same reason for
  choosing a span processor over a resource attribute. The only difference is
  the context primitive Node vs. Python idioms call for: `AsyncLocalStorage`
  here, `ContextVar` there — both are their language's standard mechanism for
  exactly this (ambient, async-safe, per-call context), not a workaround.

- **Everything else matches:** agent name, agent id, tool name/schema/result,
  the same weather question opening turn 1 (Python's additionally plants a
  redaction marker; see above), `tool_choice: 'required'` on the planning
  call only, no sampling parameters, gRPC export to `4317`, `icegate.run_id`
  resource attribute scoping, and the manually-created
  `invoke_agent`/`execute_tool` spans use identical attribute names and
  values to Python (including `gen_ai.provider.name` on `invoke_agent`, since
  that attribute is set by this recipe's own code, not by the
  auto-instrumentation).

Full findings, including the exact commands run and both A/B test results,
are documented above under "The ESM instrumentation gotcha" and "The
semconv opt-in question — answered": the article's steps are
Python-specific, and a Node/TypeScript reader hits three problems it never
mentions.
