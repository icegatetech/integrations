"""The agent's one tool. Deterministic so verification never depends on
network weather."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                },
                "required": ["city"],
            },
        },
    }
]

_FIXED = {"temperature_c": 34, "conditions": "clear", "humidity_pct": 55}


def dispatch(name, arguments):
    if name != "get_weather":
        raise ValueError(f"unknown tool: {name}")
    return {"city": arguments.get("city", "unknown"), **_FIXED}
