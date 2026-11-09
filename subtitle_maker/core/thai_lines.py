"""Break Thai (and mixed) subtitle text into lines at word boundaries."""
from __future__ import annotations

import re
from functools import lru_cache

_THAI = re.compile(r"[฀-๿]+")
# marks that must stay with the letter before them
_NO_LEAD = set("ๆฯ.,!?:;)”’…ฺ์็่้๊๋ํ")


@lru_cache(maxsize=1)
def _tokenizer():
    from pythainlp.tokenize import word_tokenize
    return word_tokenize


def tokenize(text: str) -> list[str]:
    """Words, never splitting inside a Thai word. Thai runs are cut by pythainlp, the rest by spaces.
    Punctuation is kept out of the Thai run: newmm turns 'เหรอ...' into 'เห|รอ.' otherwise."""
    tokens: list[str] = []
    pos = 0
    for m in _THAI.finditer(text):
        tokens += _split_plain(text[pos:m.start()])
        tokens += [w for w in _tokenizer()(m.group(), engine="newmm", keep_whitespace=False) if w]
        pos = m.end()
    tokens += _split_plain(text[pos:])
    # glue marks that landed at the start of a token onto the previous one
    out: list[str] = []
    for t in tokens:
        if out and t[0] in _NO_LEAD:
            out[-1] += t
        else:
            out.append(t)
    return out


def _split_plain(s: str) -> list[str]:
    return re.findall(r"\S+", s)


def _needs_space(a: str, b: str) -> bool:
    """Thai is written without spaces between words; Latin words and numbers still need one."""
    return not (_THAI.match(a[-1]) or _THAI.match(b[0])) if a and b else False


def wrap(text: str, max_chars: int = 40, max_lines: int = 2) -> str:
    """Break into at most max_lines lines of about equal length. If the text is too long for that, the
    last line simply runs longer: a subtitle must never lose words."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    words = tokenize(text)
    if not words:
        return text
    lines_needed = min(max_lines, -(-len(text) // max_chars))
    target = -(-len(text) // lines_needed)  # balanced lines look better than a long line plus a stub
    lines: list[str] = []
    cur = ""
    for w in words:
        joined = (cur + (" " if _needs_space(cur, w) else "") + w) if cur else w
        if cur and len(joined) > target and len(lines) < lines_needed - 1:
            lines.append(cur)
            cur = w
        else:
            cur = joined
    lines.append(cur)
    return "\n".join(lines)


def cps(text: str, start: float, end: float) -> float:
    return len(text.replace("\n", "")) / max(end - start, 0.001)
