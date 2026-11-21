"""Defence against prompt injection planted inside indexed documents.

Document text is DATA. Lines that try to give instructions to the model are removed
before they reach the LLM context and reported in the trace.
"""
from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above) (instructions|prompts?|rules)",
    r"forget (everything|all) (above|before|previous)",
    r"(reveal|show|print|leak) (me )?(your |the )?(system|hidden) prompt",
    r"you are now (a |an |in )?\w+",
    r"act as (a |an )?(different|new|unrestricted)",
    r"\bjailbreak\b",
    r"игнорируй(те)? (все |любые )?(предыдущие|прежние|прошлые|выше\w*) (инструкции|указания|правила|команды)",
    r"забудь(те)? (все |всё )?(предыдущие|прежние|выше\w*|что было)",
    r"(покажи|раскрой|выведи|напиши) (мне )?(свой |системный |скрытый )?(системный )?промпт",
    r"ты (теперь|больше не) (\w+ ){0,3}(ассистент|бот|модель|помощник)",
    r"не (следуй|выполняй) (предыдущим|прежним|системным) (инструкциям|правилам)",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    r"\[\s*(system|inst)\s*\]",
]
_RE = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)
REMOVED = "[удалено: подозрение на prompt-инъекцию]"


def sanitize_chunk(text: str) -> tuple[str, int]:
    """Return (clean_text, number_of_removed_lines)."""
    removed = 0
    lines = []
    for line in text.splitlines():
        if _RE.search(line):
            removed += 1
            lines.append(REMOVED)
        else:
            lines.append(line)
    return "\n".join(lines), removed


def looks_like_injection(text: str) -> bool:
    return bool(_RE.search(text or ""))
