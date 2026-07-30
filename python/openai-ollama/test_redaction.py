import redaction


def test_redacts_email_in_content_attribute():
    assert redaction.redact_text("mail me at a.b@example.com now") \
        == "mail me at [REDACTED_EMAIL] now"


def test_redacts_long_digit_runs():
    assert redaction.redact_text("card 4111111111111111 ok") \
        == "card [REDACTED_NUMBER] ok"


def test_leaves_ordinary_text_alone():
    assert redaction.redact_text("weather in Dubai") == "weather in Dubai"


def test_redact_messages_scrubs_content():
    messages = [{"role": "user", "content": "email me at a.b@example.com"}]
    assert redaction.redact_messages(messages) == [
        {"role": "user", "content": "email me at [REDACTED_EMAIL]"}
    ]


def test_redact_messages_does_not_mutate_input():
    messages = [{"role": "user", "content": "a.b@example.com"}]
    redaction.redact_messages(messages)
    assert messages[0]["content"] == "a.b@example.com"


def test_redact_messages_leaves_non_string_content_alone():
    messages = [{"role": "assistant", "content": None, "tool_calls": []}]
    assert redaction.redact_messages(messages) == messages
