"""The article's zero-code claim: no OpenTelemetry imports anywhere.

Run under the launcher:
    opentelemetry-instrument python app_zerocode.py

Note this file cannot stamp icegate.run_id itself — that would require the OTel
SDK. Pass it in via OTEL_RESOURCE_ATTRIBUTES instead; see the README.
"""

import os

from openai import OpenAI


def main():
    client = OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", "ollama"),
    )
    response = client.chat.completions.create(
        model=os.environ.get("OLLAMA_MODEL", "gemma4:12b-mlx"),
        messages=[{"role": "user", "content": "Summarize today's incidents."}],
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
