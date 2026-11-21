"""Tokenisation, light stemming and term overlap shared by embeddings, FTS fallback and reranking."""
from __future__ import annotations

import re

_TOKEN = re.compile(r"\w+(?:[\-/.]\w+)*", re.UNICODE)
_STOP = set(
    """a an the and or of to in on at for by with from is are was were be been it this that these those as if
    what which who whom how when where why do does did can could should would will shall may might not no yes
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
    меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж
    вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто
    чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой
    тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот
    через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть
    том нельзя такой им более всегда конечно всю между какие каков какое какую каком каких какому каким сколько""".split()
)


def has_digit(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def stem(token: str) -> str:
    """Crude language-agnostic stem: codes are kept intact, words are cut to a 5-char prefix."""
    if has_digit(token) or len(token) <= 5:
        return token
    return token[:5]


def tokenize(text: str) -> list[str]:
    """Lower-cased tokens. Compound codes ('дг-2024/17') yield the whole token AND its parts."""
    out: list[str] = []
    for m in _TOKEN.finditer((text or "").lower()):
        tok = m.group(0)
        out.append(tok)
        if re.search(r"[\-/.]", tok):
            out.extend(p for p in re.split(r"[\-/.]", tok) if p)
    return out


def keyword_stems(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text) if t not in _STOP and len(t) > 1]


def code_tokens(text: str) -> list[str]:
    """Identifier-like tokens (articles, contract numbers): contain a digit and are >= 3 chars."""
    seen, out = set(), []
    for m in _TOKEN.finditer((text or "").lower()):
        tok = m.group(0)
        if has_digit(tok) and len(tok) >= 3 and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def query_units(query: str) -> list[str]:
    """Query keywords where a compound code ('дг-2024/17') counts as ONE unit (not split into parts)."""
    seen: list[str] = []
    for m in _TOKEN.finditer((query or "").lower()):
        tok = m.group(0)
        if tok in _STOP or len(tok) <= 1:
            continue
        s = stem(tok)
        if s not in seen:
            seen.append(s)
    return seen


def term_overlap(query: str, text: str) -> float:
    """Fraction (0..1) of distinct query keyword units found in `text`.

    An exact identifier match (article, contract number) is strong evidence on its own,
    so it lifts the score to at least 0.6."""
    units = query_units(query)
    if not units:
        return 0.0
    lowered = (text or "").lower()
    t = set(keyword_stems(text))
    hit = sum(1 for u in units if u in t or (has_digit(u) and u in lowered))
    score = hit / len(units)
    if any(c in lowered for c in code_tokens(query)):
        score = max(score, 0.6)
    return score
