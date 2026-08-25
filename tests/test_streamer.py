from app.services.streamer import md_to_telegram_html, split_telegram_text


def test_html_inside_code_fence_is_escaped() -> None:
    rendered = md_to_telegram_html("```html\n<b>unsafe</b>\n```")
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in rendered
    assert "<b>unsafe</b>" not in rendered


def test_long_text_is_split_within_limit() -> None:
    chunks = split_telegram_text(("word " * 3_000).strip(), limit=500)
    assert " ".join(chunks).replace("  ", " ") == ("word " * 3_000).strip()
    assert all(len(chunk) <= 500 for chunk in chunks)
