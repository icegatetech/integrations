// OTLP gRPC wiring for IceGate. Instrumentation must be registered before the
// OpenAI client module is imported, so index.ts imports this file first and
// only then dynamically imports the agent.
import { randomUUID } from 'node:crypto';

import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-grpc';
import { registerInstrumentations } from '@opentelemetry/instrumentation';
import { OpenAIInstrumentation } from '@opentelemetry/instrumentation-openai';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';

import { ConversationSpanProcessor } from './conversation.js';

export const RUN_ID = process.env.ICEGATE_RUN_ID ?? randomUUID().replace(/-/g, '');

export function setup(): NodeTracerProvider {
  const provider = new NodeTracerProvider({
    resource: resourceFromAttributes({
      'service.name': process.env.OTEL_SERVICE_NAME ?? 'openai-ollama-typescript',
      'icegate.run_id': RUN_ID,
    }),
    spanProcessors: [
      // ConversationSpanProcessor wraps the batch exporter (not the other
      // way around) so it sees every span -- manual and auto-instrumented
      // alike -- before that span is ever handed to the exporter. See
      // conversation.ts for why this has to be a span processor rather than
      // a resource attribute.
      new ConversationSpanProcessor(
        new BatchSpanProcessor(
          new OTLPTraceExporter({
            url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? 'http://localhost:4317',
          }),
        ),
      ),
    ],
  });

  provider.register();
  registerInstrumentations({ instrumentations: [new OpenAIInstrumentation()] });
  return provider;
}
