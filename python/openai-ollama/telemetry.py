"""OTLP gRPC wiring for IceGate.

Two things the source article gets wrong are fixed here:

1. The article pairs endpoint 4318 with OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf.
   4317 is the gRPC port; the two settings contradict each other. This module
   speaks gRPC to 4317.
2. OTEL_SEMCONV_STABILITY_OPT_IN is read when the instrumentation loads, so
   setting it from Python after import is too late. We fail loudly instead of
   silently emitting old attribute names.

`ConversationSpanProcessor` wraps the batch exporter (not the other way
around) so it sees every span — manual and auto-instrumented alike — before
that span is ever handed to the exporter. See conversation.py for why this
has to be a span processor rather than a resource attribute.
"""

from __future__ import annotations

import os
import sys
import uuid

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from conversation import ConversationSpanProcessor

RUN_ID = os.environ.get("ICEGATE_RUN_ID") or uuid.uuid4().hex

_REQUIRED_OPT_IN = "gen_ai_latest_experimental"


def _check_opt_in():
    value = os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN", "")
    if _REQUIRED_OPT_IN not in value:
        sys.exit(
            f"OTEL_SEMCONV_STABILITY_OPT_IN must contain {_REQUIRED_OPT_IN!r} "
            f"before this process starts (got {value!r}).\n"
            f"Without it the instrumentation emits conventions frozen around "
            f"v1.30 — notably `gen_ai.system` instead of `gen_ai.provider.name`, "
            f"which leaves IceGate's operations.provider_name NULL.\n"
            f"Fix: export it in your shell or .env, then re-run."
        )


def setup(service_name=None):
    """Wire OTLP gRPC export and instrument the OpenAI SDK.

    Returns (provider, run_id). Callers must call provider.shutdown() so spans
    flush before exit — otherwise the verifier finds nothing.
    """
    _check_opt_in()

    service_name = service_name or os.environ.get(
        "OTEL_SERVICE_NAME", "openai-ollama-python"
    )
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    provider = TracerProvider(
        resource=Resource.create({
            "service.name": service_name,
            "icegate.run_id": RUN_ID,
        })
    )
    provider.add_span_processor(
        ConversationSpanProcessor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
    )
    trace.set_tracer_provider(provider)

    OpenAIInstrumentor().instrument()
    return provider, RUN_ID
