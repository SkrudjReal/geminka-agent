"""Render conversational emoji only through the permitted Telegram catalog."""

import html
import re

import emoji

from app.services.harvester import load_default_emojis

_PROTECTED = re.compile(
    r'(```[\s\S]*?```|`[^`\n]*`|<pre\b[^>]*>[\s\S]*?</pre>|<code\b[^>]*>[\s\S]*?</code>'
    r'|<tg-emoji\b[^>]*>[\s\S]*?</tg-emoji>|</?[A-Za-z][^>]*>|https?://[^\s<>]+)', re.I)


def premium_only_text(text: str, *, catalog=None) -> str:
    catalog = load_default_emojis() if catalog is None else catalog
    allowed = {str(item['custom_emoji_id']) for item in catalog}
    replacements = {}
    for item in catalog:
        symbol = item.get('emoji', '').replace('\ufe0f', '').replace('\ufe0e', '')
        if symbol:
            replacements.setdefault(symbol, str(item['custom_emoji_id']))

    def convert(symbol, _data):
        cid = replacements.get(symbol.replace('\ufe0f', '').replace('\ufe0e', ''))
        return f'<tg-emoji emoji-id="{cid}">{symbol}</tg-emoji>' if cid else ''

    result = []
    for index, part in enumerate(_PROTECTED.split(text)):
        if index % 2:
            if part.lower().startswith('<tg-emoji'):
                identity = re.search(r'\bemoji-id\s*=\s*(?:"(\d+)"|\'(\d+)\'|(\d+))', part, re.I)
                cid = next((value for value in identity.groups() if value), None) if identity else None
                body = re.sub(r'^<tg-emoji\b[^>]*>|</tg-emoji>$', '', part, flags=re.I)
                if cid not in allowed:
                    part = emoji.replace_emoji(body, replace=convert)
                else:
                    part = f'<tg-emoji emoji-id="{cid}">{body}</tg-emoji>'
            result.append(part)
        else:
            result.append(emoji.replace_emoji(part, replace=convert))
    return ''.join(result)


def plain_without_emoji(text: str) -> str:
    """HTML delivery failed: never downgrade custom emoji into ordinary emoji."""
    return emoji.replace_emoji(html.unescape(re.sub(r'<[^>]*>', '', text)), replace='').strip() or '…'
