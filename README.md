# IceGate Integrations

Runnable, verified recipes for sending LLM/AI telemetry to an OTLP endpoint —
generally, and to [IceGate](https://github.com/icegatetech/icegate) specifically.

Every recipe here is *verified*: it runs against a local model, exports to a
local IceGate, and a shared harness queries the data back over Arrow Flight SQL
to assert the telemetry actually landed with the right shape.

## Catalog

| Recipe | Language | Provider | Status | What it proves |
|---|---|---|---|---|
| [`python/openai-ollama`](python/openai-ollama) | Python 3.14 | Ollama (OpenAI-compatible) | Verified — `make verify` PASSED | Auto-instrumented `chat` spans, manual `invoke_agent`/`execute_tool` nesting, token capture, opt-in content capture with redaction |
| [`typescript/openai-ollama`](typescript/openai-ollama) | Node 26 | Ollama (OpenAI-compatible) | Verified — `make verify` PASSED (semconv opt-in not honored by the JS instrumentation; see recipe README) | The same, via the JS instrumentation |

Status is filled in as each recipe lands.

## Prerequisites

- [Ollama](https://ollama.com) with `ollama pull gemma4:12b-mlx`
- IceGate running locally — clone [icegatetech/icegate](https://github.com/icegatetech/icegate) as a sibling directory and run `make run-docker-core-release`

Check everything at once:

    make doctor

## Running a recipe

    make verify RECIPE=python/openai-ollama

## Adding a recipe

Read [CONVENTIONS.md](CONVENTIONS.md).


## License

[MIT](LICENSE) — copy these recipes into your own projects freely.
