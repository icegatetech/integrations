// Telemetry first: instrumentation must patch the OpenAI module before it loads.
import { randomUUID } from 'node:crypto';

import { RUN_ID, setup } from './telemetry.js';

const provider = setup();

const { default: OpenAI } = await import('openai');
const { run } = await import('./agent.js');

const client = new OpenAI({
  baseURL: process.env.OPENAI_BASE_URL ?? 'http://localhost:11434/v1',
  apiKey: process.env.OPENAI_API_KEY ?? 'ollama',
});

const model = process.env.OLLAMA_MODEL ?? 'gemma4:12b-mlx';

// Two turns, one conversation: IceGate's schema comment says conversation_id
// "groups a multi-turn conversation's operations across traces." One turn is
// one trace and would demonstrate nothing about cross-trace grouping.
// Deliberately distinct from icegate.run_id -- run_id scopes this one
// process invocation for verification purposes, while conversationId is the
// separate, product-level concept of a multi-turn exchange.
const conversationId = `conv-${randomUUID().replace(/-/g, '')}`;

const first = await run(
  client,
  model,
  "What's the weather in Dubai right now?",
  conversationId,
);
console.log(first);

const second = await run(
  client,
  model,
  "Now check Riyadh too — I'm flying there right after Dubai.",
  conversationId,
);
console.log(second);

await provider.shutdown();
console.log(`ICEGATE_CONVERSATION_ID=${conversationId}`);
console.log(`ICEGATE_RUN_ID=${RUN_ID}`);
