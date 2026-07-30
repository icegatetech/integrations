import assert from 'node:assert/strict';
import { setTimeout as sleep } from 'node:timers/promises';
import test from 'node:test';

import type { Context } from '@opentelemetry/api';
import type { ReadableSpan, Span, SpanProcessor } from '@opentelemetry/sdk-trace-base';

import { ConversationSpanProcessor, currentId, withConversation } from './conversation.js';

test('currentId is undefined outside any conversation', () => {
  assert.equal(currentId(), undefined);
});

test('currentId returns the id inside a conversation', () => {
  withConversation('conv-1', () => {
    assert.equal(currentId(), 'conv-1');
  });
});

test('currentId restores the previous value after the call returns', () => {
  withConversation('conv-1', () => {});
  assert.equal(currentId(), undefined);
});

test('currentId restores the previous value even when fn throws', () => {
  assert.throws(() => {
    withConversation('conv-1', () => {
      throw new Error('boom');
    });
  }, /boom/);
  assert.equal(currentId(), undefined);
});

test('nested conversations restore the outer id', () => {
  withConversation('outer', () => {
    assert.equal(currentId(), 'outer');
    withConversation('inner', () => {
      assert.equal(currentId(), 'inner');
    });
    assert.equal(currentId(), 'outer');
  });
  assert.equal(currentId(), undefined);
});

test('the conversation id survives real async continuations, not just the synchronous call', async () => {
  // This is the shape agent.ts actually uses: withConversation wrapping an
  // async function full of awaits (client calls, nested spans). A ContextVar
  // equivalent that only worked synchronously would be useless here.
  await withConversation('conv-async', async () => {
    assert.equal(currentId(), 'conv-async');
    await sleep(1);
    assert.equal(currentId(), 'conv-async');
  });
  assert.equal(currentId(), undefined);
});

class FakeSpan {
  attributes: Record<string, unknown> = {};

  setAttribute(key: string, value: unknown) {
    this.attributes[key] = value;
    return this;
  }
}

class RecordingProcessor implements SpanProcessor {
  started: Array<[Span, Context]> = [];
  ended: ReadableSpan[] = [];
  shutdownCalls = 0;
  flushCalls = 0;

  onStart(span: Span, parentContext: Context): void {
    this.started.push([span, parentContext]);
  }

  onEnd(span: ReadableSpan): void {
    this.ended.push(span);
  }

  shutdown(): Promise<void> {
    this.shutdownCalls += 1;
    return Promise.resolve();
  }

  forceFlush(): Promise<void> {
    this.flushCalls += 1;
    return Promise.resolve();
  }
}

test('onStart stamps conversation.id when one is active', () => {
  const inner = new RecordingProcessor();
  const processor = new ConversationSpanProcessor(inner);
  const span = new FakeSpan();

  withConversation('conv-42', () => {
    processor.onStart(span as unknown as Span, {} as Context);
  });

  assert.deepEqual(span.attributes, { 'gen_ai.conversation.id': 'conv-42' });
});

test('onStart does not stamp when no conversation is active', () => {
  const inner = new RecordingProcessor();
  const processor = new ConversationSpanProcessor(inner);
  const span = new FakeSpan();

  processor.onStart(span as unknown as Span, {} as Context);

  assert.deepEqual(span.attributes, {});
});

test('onStart always delegates to the inner processor', () => {
  const inner = new RecordingProcessor();
  const processor = new ConversationSpanProcessor(inner);
  const span = new FakeSpan() as unknown as Span;
  const context = {} as Context;

  processor.onStart(span, context);

  assert.equal(inner.started.length, 1);
  assert.equal(inner.started[0][0], span);
  assert.equal(inner.started[0][1], context);
});

test('onEnd delegates to the inner processor', () => {
  const inner = new RecordingProcessor();
  const processor = new ConversationSpanProcessor(inner);
  const span = {} as ReadableSpan;

  processor.onEnd(span);

  assert.deepEqual(inner.ended, [span]);
});

test('shutdown delegates to the inner processor', async () => {
  const inner = new RecordingProcessor();
  const processor = new ConversationSpanProcessor(inner);

  await processor.shutdown();

  assert.equal(inner.shutdownCalls, 1);
});

test('forceFlush delegates to the inner processor', async () => {
  const inner = new RecordingProcessor();
  const processor = new ConversationSpanProcessor(inner);

  await processor.forceFlush();

  assert.equal(inner.flushCalls, 1);
});
