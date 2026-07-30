"""Article Step 2 — agent and tool structure.

Divergences from the article, all deliberate:

* The article sets `gen_ai.system`. Under gen_ai_latest_experimental the
  attribute is `gen_ai.provider.name`; emitting the old name contradicts
  the article's own Step 1 opt-in — a consistency fix, not a breakage
  fix. The raw span never gets `gen_ai.provider.name` from `gen_ai.system`
  alone, but this IceGate build's typed operations.provider_name still
  populates via a deprecation fallback. See "Divergences from the article"
  in README.md for the full detail.
* The article omits `gen_ai.agent.id` and `gen_ai.tool.call.id`. The provider
  returns a tool call id, and IceGate has typed columns for both.
* `tool_choice="required"` is set on the planning call only. Leaving it on the
  final call would force another tool call and loop forever.
"""

import json

from opentelemetry import trace

import conversation
import redaction
import tools

AGENT_NAME = "travel-concierge"
AGENT_ID = "travel-concierge-001"

tracer = trace.get_tracer("icegate.recipes.openai_ollama")


def run(client, model, user_message, conversation_id=None):
    """Run one turn of the agent.

    `conversation_id`, when given, is active for this entire call via
    `conversation.conversation()` — so it covers not just the manual
    `invoke_agent`/`execute_tool` spans below, but also the auto-instrumented
    `chat` spans created inside `client.chat.completions.create()`. Passing
    the same id across multiple calls (see recipe.py) is what groups
    separate turns — separate traces — into one conversation.
    """
    with conversation.conversation(conversation_id), tracer.start_as_current_span(
        f"invoke_agent {AGENT_NAME}",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.agent.id": AGENT_ID,
            "gen_ai.provider.name": "openai",
        },
    ):
        # Redact before anything leaves the process, so the raw value reaches
        # neither the provider nor the telemetry.
        messages = redaction.redact_messages(
            [{"role": "user", "content": user_message}]
        )

        planning = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools.TOOL_SCHEMAS,
            tool_choice="required",
        )
        message = planning.choices[0].message
        tool_calls = message.tool_calls or []
        if not tool_calls:
            raise RuntimeError(
                "model returned no tool call despite tool_choice='required'. "
                "Expectations require an execute_tool span; failing here rather "
                "than letting the verifier report a misleading missing span."
            )

        messages.append({
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ],
        })

        for call in tool_calls:
            with tracer.start_as_current_span(
                f"execute_tool {call.function.name}",
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": call.function.name,
                    "gen_ai.tool.type": "function",
                    "gen_ai.tool.call.id": call.id,
                },
            ):
                result = tools.dispatch(
                    call.function.name, json.loads(call.function.arguments)
                )
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result),
            })

        final = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return final.choices[0].message.content
