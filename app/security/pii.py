"""PII masking for traces and audit logs."""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CARD = re.compile(r"(?<![\d\-])(?:\d[ \-]?){12,18}\d(?![\d\-])")
_PHONE_RU = re.compile(r"(?<![\w])(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")
_PHONE_INTL = re.compile(r"(?<![\w])\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3}[\s\-]?\d{2,4}(?:[\s\-]?\d{2,4})?(?!\d)")
_PASSPORT = re.compile(r"(?<![\d\-])(?:\d{4}\s\d{6}|\d{2}\s\d{2}\s\d{6})(?![\d\-])")
_PASSPORT_KW = re.compile(r"(паспорт[^\d]{0,30})(\d{10})(?!\d)", re.IGNORECASE)


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _mask_card(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if 13 <= len(digits) <= 19 and _luhn(digits):
        return "[CARD]"
    return match.group(0)


def mask_pii(text: str | None) -> str:
    if not text:
        return text or ""
    out = _EMAIL.sub("[EMAIL]", text)
    out = _CARD.sub(_mask_card, out)
    out = _PASSPORT_KW.sub(lambda m: m.group(1) + "[PASSPORT]", out)
    out = _PHONE_RU.sub("[PHONE]", out)
    out = _PHONE_INTL.sub("[PHONE]", out)
    out = _PASSPORT.sub("[PASSPORT]", out)
    return out


def mask_obj(value):
    """Recursively mask all strings inside dicts/lists (for JSON audit payloads)."""
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: mask_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_obj(v) for v in value]
    return value
