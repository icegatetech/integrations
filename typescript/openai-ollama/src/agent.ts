import { SpanKind, trace } from '@opentelemetry/api';
import type OpenAI from 'openai';

import { withConversation } from './conversation.js';
import { TOOL_SCHEMAS, dispatch } from './tools.js';

export const AGENT_NAME = 'travel-concierge';
export const AGENT_ID = 'travel-concierge-001';

const tracer = trace.getTracer('icegate.recipes.openai_ollama');

/**
 * Run one turn of the agent.
 *
 * `conversationId`, when given, is active for this entire call via
 * `withConversation()` -- so it covers not just the manual
 * `invoke_agent`/`execute_tool` spans below, but also the auto-instrumented
 * `chat` spans created inside `client.chat.completions.create()`. Passing
 * the same id across multiple calls (see index.ts) is what groups separate
 * turns -- separate traces -- into one conversation.
 */
export async function run(
  client: OpenAI,
  model: string,
  userMessage: string,
  conversationId?: string,
): Promise<string> {
  return withConversation(conversationId, () => runTurn(client, model, userMessage));
}

function runTurn(client: OpenAI, model: string, userMessage: string): Promise<string> {
  return tracer.startActiveSpan(
    `invoke_agent ${AGENT_NAME}`,
    {
      kind: SpanKind.INTERNAL,
      attributes: {
        'gen_ai.operation.name': 'invoke_agent',
        'gen_ai.agent.name': AGENT_NAME,
        'gen_ai.agent.id': AGENT_ID,
        'gen_ai.provider.name': 'openai',
      },
    },
    async (agentSpan) => {
      try {
        const messages: any[] = [{ role: 'user', content: userMessage }];

        const planning = await client.chat.completions.create({
          model,
          messages,
          tools: TOOL_SCHEMAS,
          tool_choice: 'required',
          // No sampling parameters: Ollama applies the model's own defaults.
        });

        const message = planning.choices[0].message;
        const toolCalls = message.tool_calls ?? [];
        if (toolCalls.length === 0) {
          throw new Error(
            "model returned no tool call despite tool_choice='required'; " +
              'failing here rather than letting the verifier report a missing span',
          );
        }

        messages.push({ role: 'assistant', tool_calls: toolCalls });

        for (const call of toolCalls) {
          const fn = (call as any).function;
          await tracer.startActiveSpan(
            `execute_tool ${fn.name}`,
            {
              attributes: {
                'gen_ai.operation.name': 'execute_tool',
                'gen_ai.tool.name': fn.name,
                'gen_ai.tool.type': 'function',
                'gen_ai.tool.call.id': call.id,
              },
            },
            async (toolSpan) => {
              try {
                const result = dispatch(fn.name, JSON.parse(fn.arguments));
                messages.push({
                  role: 'tool',
                  tool_call_id: call.id,
                  content: JSON.stringify(result),
                });
              } finally {
                toolSpan.end();
              }
            },
          );
        }

        const final = await client.chat.completions.create({
          model,
          messages,
        });
        return final.choices[0].message.content ?? '';
      } finally {
        agentSpan.end();
      }
    },
  );
}
