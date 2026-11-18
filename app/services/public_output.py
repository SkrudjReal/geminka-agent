"""Do not publish local filesystem references in public Telegram responses."""

import base64
import html
import logging
import os
import re
import unicodedata
from urllib.parse import unquote

SAFE_REPLY = 'Неа, это останется за кулисами)'
logger = logging.getLogger(__name__)
_SENSITIVE_NAME = re.compile(r'(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY|BROKER_KEY)', re.I)
_CREDENTIAL = re.compile(
    r'\b\d{6,12}:[A-Za-z0-9_-]{30,}\b|'
    r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
    r'sk-(?:proj-)?[A-Za-z0-9_-]{20,}|keen_[A-Za-z0-9_]{20,}|ya29\.[A-Za-z0-9_-]{20,})|'
    r'\bAKIA[A-Z0-9]{16}\b|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|'
    r'\b(?:Authorization\s*:\s*Bearer\s+|_pinterest_sess\s*=)[A-Za-z0-9_./+=%-]{12,}|'
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
    r'https?://[^\s/<>]+:[^\s/<>]+@', re.I)


def _contains_secret(text: str) -> bool:
    if _CREDENTIAL.search(text):
        return True
    for key, value in os.environ.items():
        if not _SENSITIVE_NAME.search(key) or len(value) < 12:
            continue
        # Catch common encodings without storing or logging the credential.
        forms = (value, base64.b64encode(value.encode()).decode(),
                 base64.urlsafe_b64encode(value.encode()).decode().rstrip('='),
                 value.encode().hex(), value.encode().hex().upper())
        if any(form in text for form in forms):
            return True
    return False
_LOCAL = re.compile(
    r'\bfile\s*:|(?:^|[\s\[\]("\'`=>:])(?:/(?:home|root|tmp|etc|var|proc|sys|mnt|srv|opt|usr|run|workspace|workspaces)(?:/|\b)'
    r'|[a-z]:[\\/]|~[/\\])|\.(?:geminka|gemini|codex)(?:[/\\]|\b)',
    re.IGNORECASE,
)


def safe_public_text(text: str) -> str:
    decoded = unicodedata.normalize('NFKC', text)
    for _ in range(4):
        decoded = html.unescape(unquote(decoded))
    decoded = re.sub(r'[\u200b-\u200f\u2060\ufeff]', '', decoded)
    decoded = decoded.replace('\\/', '/').replace('\\u002f', '/')
    if _LOCAL.search(decoded) or _contains_secret(decoded):
        logger.warning('Blocked sensitive public output; content omitted')
        return SAFE_REPLY
    return text


def protect_method(method):
    updates = {}
    for field in ('text', 'caption'):
        value = getattr(method, field, None)
        entities = getattr(method, 'entities' if field == 'text' else 'caption_entities', None) or []
        unsafe_link = any(safe_public_text(getattr(entity, 'url', '') or '') == SAFE_REPLY for entity in entities)
        if isinstance(value, str) and (safe_public_text(value) == SAFE_REPLY or unsafe_link):
            updates[field] = SAFE_REPLY
            updates['entities' if field == 'text' else 'caption_entities'] = None
    media = getattr(method, 'media', None)
    if isinstance(media, list):
        updates['media'] = [protect_method(item) for item in media]
    reply = getattr(method, 'reply_parameters', None)
    if reply and reply.quote and safe_public_text(reply.quote) == SAFE_REPLY:
        updates['reply_parameters'] = reply.model_copy(update={'quote': None, 'quote_entities': None, 'quote_position': None})
    for field in ('photo', 'video', 'animation', 'audio', 'document', 'sticker', 'media'):
        value = getattr(method, field, None)
        if isinstance(value, str) and value.startswith(('https://', 'http://')) and safe_public_text(value) == SAFE_REPLY:
            raise ValueError('Sensitive media URL blocked')
    markup = getattr(method, 'reply_markup', None)
    if hasattr(markup, 'inline_keyboard') and any(
        button.url and safe_public_text(button.url) == SAFE_REPLY
        for row in markup.inline_keyboard for button in row
    ):
        updates['reply_markup'] = None
    return method.model_copy(update=updates) if updates else method
