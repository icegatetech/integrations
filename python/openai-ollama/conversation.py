"""Conversation grouping via `gen_ai.conversation.id`.

IceGate reads `operations.conversation_id` from the `gen_ai.conversation.id`
**span** attribute (`crates/icegate-ingest/src/transform/operations/otel.rs:47`
in the sibling `icegate` repo; OpenInference's equivalent is `session.id`).
Span attributes do not inherit, so setting this only on the manual
`invoke_agent` span would leave every auto-instrumented `chat` span NULL,
defeating the grouping the moment it needed to cover more than one span.

A resource attribute would cover every span for free, but resources are
process-wide and immutable once a `TracerProvider` is built, while
conversations are neither: a single process may serve many conversations
(sequentially or concurrently), and a single conversation may span many
processes. Baking a conversation id into the resource would teach the wrong
pattern in a reference recipe.

The fix is a `SpanProcessor.on_start` hook that reads the active conversation
id from a `ContextVar` and stamps it directly onto the span. `on_start` runs
while the span is still live, so `span.set_attribute()` is ordinary public
API — this needs no private SDK internals. That is unlike the redaction case
in `redaction.py` (Task 5), which could not use this pattern (it needed to
change outbound *content* before the provider ever saw it, not annotate a
span after the fact) and was solved differently as a result.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry.sdk.trace import SpanProcessor

_current_conversation_id: ContextVar[str | None] = ContextVar(
    "icegate_conversation_id", default=None
)


def current_id() -> str | None:
    """The active conversation id, or None outside any `conversation()` block."""
    return _current_conversation_id.get()


@contextmanager
def conversation(conversation_id: str | None):
    """Make `conversation_id` the active conversation for the block.

    Every span started anywhere in this block — including ones created by
    auto-instrumentation deep inside a library call, not just spans this
    process creates directly — is eligible to be stamped by
    `ConversationSpanProcessor`. On exit (including via exception) the
    previous value is restored, so nesting behaves the way a stack would:
    the inner id applies only for the inner block's duration.
    """
    token = _current_conversation_id.set(conversation_id)
    try:
        yield conversation_id
    finally:
        _current_conversation_id.reset(token)


class ConversationSpanProcessor(SpanProcessor):
    """Stamps `gen_ai.conversation.id` on every span started while a
    conversation is active, then delegates to `inner` for everything else
    (export, batching, shutdown).

    Registering this as the outermost processor on the `TracerProvider` used
    by both the recipe's manual spans and the auto-instrumented `chat` spans
    is what makes coverage uniform: every span this process creates, from
    wherever it's created, passes through the same `on_start` hook.
    """

    def __init__(self, inner: SpanProcessor):
        self._inner = inner

    def on_start(self, span, parent_context=None):
        conversation_id = current_id()
        if conversation_id is not None:
            span.set_attribute("gen_ai.conversation.id", conversation_id)
        self._inner.on_start(span, parent_context=parent_context)

    def on_end(self, span):
        self._inner.on_end(span)

    def shutdown(self):
        self._inner.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self._inner.force_flush(timeout_millis=timeout_millis)
