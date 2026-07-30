# Recipe conventions

Every recipe in this repository follows this contract. It exists so a new
integration can be added without a design discussion.

## Structure

Recipes live at `<language>/<provider>-<runtime>/` — for example
`python/openai-ollama/`. A recipe is self-contained and copy-pasteable: it must
not import shared code from this repository. Duplication between recipes is
accepted deliberately, so that a reader can lift one directory and have
everything.

## Requirements

1. Runs in two commands or fewer from its own directory.
2. Has a fixed entrypoint command, since `scripts/run_and_verify.sh` invokes
   the recipe itself (not a human-chosen command) to capture its
   `ICEGATE_RUN_ID`: a Python recipe (`pyproject.toml` present) must be
   runnable as `uv run python -m recipe` from its own directory; a
   TypeScript recipe (`package.json` present) must be runnable as
   `npm start`. A recipe satisfying every other requirement here still
   breaks the script if it doesn't expose one of these two exact commands.
3. Reads all configuration from environment variables, and ships `.env.example`.
4. Exports OTLP **gRPC** to `OTEL_EXPORTER_OTLP_ENDPOINT`, default
   `http://localhost:4317`.
5. Sets a unique `OTEL_SERVICE_NAME` and stamps a per-invocation
   `icegate.run_id` resource attribute, printed to stdout as
   `ICEGATE_RUN_ID=<hex>`.
6. Pins every dependency to an exact version.
7. Ships `expectations.yaml` describing the telemetry it claims to emit —
   see `verify/README.md` for the schema.
8. Ships a README covering: what it proves, prerequisites, how to run, how to
   verify, and known divergences from the source article.

## Canonical environment variables

| Variable | Default | Meaning |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | IceGate OTLP gRPC ingest |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | Must match the port |
| `OTEL_SERVICE_NAME` | per recipe | Isolates the recipe in queries |
| `OTEL_SEMCONV_STABILITY_OPT_IN` | `gen_ai_latest_experimental` | Must be set before process start |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `NO_CONTENT` | Opt-in prompt/response capture. An **enum**, not a boolean: `NO_CONTENT`, `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT`. Any other value logs a warning and falls back to `NO_CONTENT`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama host **root** — probed by `scripts/doctor.sh` at `/api/tags`. Never append `/v1`. |
| `OPENAI_BASE_URL` | `http://localhost:11434/v1` | The OpenAI-compatible surface, used by recipes. Distinct from `OLLAMA_BASE_URL` on purpose; also the name the OpenAI SDK reads natively. |
| `OLLAMA_MODEL` | `gemma4:12b-mlx` | Model under test |
| `ICEGATE_FLIGHTSQL_URI` | `grpc://localhost:8815` | Verification query endpoint |
| `ICEGATE_TENANT` | `default` | `x-scope-orgid` value |

## Why `icegate.run_id`

Re-running a recipe accumulates spans in IceGate. A verifier filtering only on
service name would happily pass against a *previous* run's data — including
after the current run broke. A fresh run ID per invocation scopes every
assertion to the run that just happened.
