// Conversation grouping via `gen_ai.conversation.id`.
//
// Mirrors python/openai-ollama/conversation.py — see that file's docstring
// for the full rationale. Short version: IceGate reads
// `operations.conversation_id` from the `gen_ai.conversation.id` **span**
// attribute (crates/icegate-ingest/src/transform/operations/otel.rs:47 in
// the sibling `icegate` repo). Span attributes don't inherit, so setting it
// only on the manual `invoke_agent` span would leave every
// auto-instrumented `chat` span NULL. A resource attribute would cover
// every span, but resources are process-wide and immutable once the
// `NodeTracerProvider` is built, while conversations are neither — that
// would teach the wrong pattern here. So this stamps the attribute from
// `SpanProcessor.onStart`, reading the active conversation id from an
// `AsyncLocalStorage` — Node's context-propagation primitive, the async
// equivalent of Python's `ContextVar`, and what OpenTelemetry's own Node
// context manager (@opentelemetry/context-async-hooks) is built on
// internally. `onStart` runs while the span is still live, so
// `span.setAttribute()` is ordinary public API — no private OpenTelemetry
// internals involved.

import { AsyncLocalStorage } from 'node:async_hooks';

import type { Context } from '@opentelemetry/api';
import type { ReadableSpan, Span, SpanProcessor } from '@opentelemetry/sdk-trace-base';

const storage = new AsyncLocalStorage<string | undefined>();

/** The active conversation id, or undefined outside any `withConversation()` call. */
export function currentId(): string | undefined {
  return storage.getStore();
}

/**
 * Run `fn` with `conversationId` as the active conversation.
 *
 * Every span started anywhere during `fn` — including ones created by
 * auto-instrumentation deep inside a library call, not just spans this
 * process creates directly — is eligible to be stamped by
 * `ConversationSpanProcessor`. `AsyncLocalStorage.run` restores the previous
 * store on return (including when `fn` throws), so nested calls behave like
 * a stack: the inner id applies only for the inner call's duration.
 */
export function withConversation<T>(conversationId: string | undefined, fn: () => T): T {
  return storage.run(conversationId, fn);
}

/**
 * Stamps `gen_ai.conversation.id` on every span started while a conversation
 * is active, then delegates to `inner` for everything else (export,
 * batching, shutdown).
 *
 * Registering this as the outermost processor on the `NodeTracerProvider`
 * used by both the recipe's manual spans and the auto-instrumented `chat`
 * spans is what makes coverage uniform: every span this process creates,
 * from wherever it's created, passes through the same `onStart` hook.
 */
export class ConversationSpanProcessor implements SpanProcessor {
  constructor(private readonly inner: SpanProcessor) {}

  onStart(span: Span, parentContext: Context): void {
    const conversationId = currentId();
    if (conversationId !== undefined) {
      span.setAttribute('gen_ai.conversation.id', conversationId);
    }
    this.inner.onStart(span, parentContext);
  }

  onEnd(span: ReadableSpan): void {
    this.inner.onEnd(span);
  }

  shutdown(): Promise<void> {
    return this.inner.shutdown();
  }

  forceFlush(): Promise<void> {
    return this.inner.forceFlush();
  }
}
