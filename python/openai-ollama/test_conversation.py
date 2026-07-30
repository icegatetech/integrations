import pytest

import conversation


def test_current_id_is_none_outside_any_conversation():
    assert conversation.current_id() is None


def test_current_id_returns_the_id_inside_a_conversation():
    with conversation.conversation("conv-1"):
        assert conversation.current_id() == "conv-1"


def test_current_id_restores_the_previous_value_on_exit():
    with conversation.conversation("conv-1"):
        pass
    assert conversation.current_id() is None


def test_current_id_restores_the_previous_value_even_on_exception():
    with pytest.raises(RuntimeError):
        with conversation.conversation("conv-1"):
            raise RuntimeError("boom")
    assert conversation.current_id() is None


def test_nested_conversations_restore_the_outer_id():
    with conversation.conversation("outer"):
        assert conversation.current_id() == "outer"
        with conversation.conversation("inner"):
            assert conversation.current_id() == "inner"
        assert conversation.current_id() == "outer"
    assert conversation.current_id() is None


class _FakeSpan:
    """Stand-in for opentelemetry.sdk.trace.Span: just enough to prove
    set_attribute was (or wasn't) called, without a real TracerProvider."""

    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _RecordingProcessor:
    """Stand-in inner SpanProcessor. Records every call so tests can assert
    ConversationSpanProcessor delegates instead of swallowing them."""

    def __init__(self):
        self.started = []
        self.ended = []
        self.shutdown_calls = 0
        self.flush_calls = []

    def on_start(self, span, parent_context=None):
        self.started.append((span, parent_context))

    def on_end(self, span):
        self.ended.append(span)

    def shutdown(self):
        self.shutdown_calls += 1

    def force_flush(self, timeout_millis=30000):
        self.flush_calls.append(timeout_millis)
        return True


def test_on_start_stamps_conversation_id_when_one_is_active():
    inner = _RecordingProcessor()
    processor = conversation.ConversationSpanProcessor(inner)
    span = _FakeSpan()

    with conversation.conversation("conv-42"):
        processor.on_start(span)

    assert span.attributes == {"gen_ai.conversation.id": "conv-42"}


def test_on_start_does_not_stamp_when_no_conversation_is_active():
    inner = _RecordingProcessor()
    processor = conversation.ConversationSpanProcessor(inner)
    span = _FakeSpan()

    processor.on_start(span)

    assert span.attributes == {}


def test_on_start_always_delegates_to_the_inner_processor():
    inner = _RecordingProcessor()
    processor = conversation.ConversationSpanProcessor(inner)
    span = _FakeSpan()

    processor.on_start(span)

    assert inner.started == [(span, None)]


def test_on_end_delegates_to_the_inner_processor():
    inner = _RecordingProcessor()
    processor = conversation.ConversationSpanProcessor(inner)
    span = _FakeSpan()

    processor.on_end(span)

    assert inner.ended == [span]


def test_shutdown_delegates_to_the_inner_processor():
    inner = _RecordingProcessor()
    processor = conversation.ConversationSpanProcessor(inner)

    processor.shutdown()

    assert inner.shutdown_calls == 1


def test_force_flush_delegates_to_the_inner_processor_and_returns_its_result():
    inner = _RecordingProcessor()
    processor = conversation.ConversationSpanProcessor(inner)

    result = processor.force_flush(1234)

    assert result is True
    assert inner.flush_calls == [1234]
