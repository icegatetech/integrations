"""Redaction applied before content reaches the model.

The article redacts at the OpenTelemetry Collector. This recipe exports straight
to IceGate with no Collector in the path, so it redacts at the source instead:
sanitising the input means sensitive values never reach the provider *or* the
telemetry, and it needs no private OpenTelemetry internals.

The trade-off is real and narrower than the article's approach: this cannot
scrub content the model *returns*. For that — and for redacting across many
services at once — use a Collector processor. See the README.
"""

from __future__ import annotations

import re

_PATTERNS = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{9,}\b"), "[REDACTED_NUMBER]"),
)


def redact_text(text):
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_messages(messages):
    """Return a copy of `messages` with string content redacted.

    Copies rather than mutating: the caller's list is often reused to build the
    next request, and silently rewriting it in place makes the data flow hard to
    follow.
    """
    redacted = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            redacted.append({**message, "content": redact_text(content)})
        else:
            redacted.append(message)
    return redacted
