"""Runs the travel-concierge agent against Ollama, exporting to IceGate.

Two turns, one conversation: IceGate's schema comment says conversation_id
"groups a multi-turn conversation's operations across traces." One turn is
one trace and would demonstrate nothing about cross-trace grouping — so this
recipe places two calls to agent.run() under the same conversation_id. Each
call is its own root span (no active parent context between them), so each
gets its own trace_id automatically; sharing conversation_id across those two
trace_ids is the thing this recipe exists to prove. See conversation.py for
why.
"""

import os
import uuid

from openai import OpenAI

import agent
import telemetry


def main():
    provider, run_id = telemetry.setup()
    client = OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", "ollama"),
    )
    model = os.environ.get("OLLAMA_MODEL", "gemma4:12b-mlx")

    # Deliberately distinct from icegate.run_id: run_id scopes this one
    # process invocation for verification purposes, while conversation_id is
    # the separate, product-level concept of a multi-turn exchange — in a
    # real deployment the two would rarely be generated together like this.
    conversation_id = f"conv-{uuid.uuid4().hex}"

    first = agent.run(
        client,
        model,
        "What's the weather in Dubai right now? "
        "Send the summary to ops-oncall@example.com.",
        conversation_id=conversation_id,
    )
    print(first)

    second = agent.run(
        client,
        model,
        "Now check Riyadh too — I'm flying there right after Dubai. "
        "Loop in ops-oncall@example.com on that update as well.",
        conversation_id=conversation_id,
    )
    print(second)

    provider.shutdown()
    print(f"ICEGATE_CONVERSATION_ID={conversation_id}")
    print(f"ICEGATE_RUN_ID={run_id}")


if __name__ == "__main__":
    main()
