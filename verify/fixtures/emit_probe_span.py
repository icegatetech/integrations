"""Emits a known synthetic span tree so the harness can be tested without a
model or a recipe. Prints ICEGATE_RUN_ID=<hex> as its last line."""

import os
import uuid

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

RUN_ID = uuid.uuid4().hex
ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

provider = TracerProvider(
    resource=Resource.create({
        "service.name": "icegate-verify-probe",
        "icegate.run_id": RUN_ID,
    })
)
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=ENDPOINT, insecure=True))
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("icegate.verify.probe")

with tracer.start_as_current_span(
    "invoke_agent probe-agent",
    attributes={
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "probe-agent",
        "gen_ai.provider.name": "openai",
    },
):
    with tracer.start_as_current_span(
        "execute_tool probe_tool",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "probe_tool",
            "gen_ai.tool.type": "function",
        },
    ):
        pass

provider.shutdown()
print(f"ICEGATE_RUN_ID={RUN_ID}")
