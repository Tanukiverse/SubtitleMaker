"""SubRip (.srt) reading and writing."""
from __future__ import annotations

import re
from pathlib import Path


def fmt_ts(t: float) -> str:
    ms = max(0, round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_ts(s: str) -> float:
    h, m, rest = s.strip().split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


POS_TAGS = {"top": "{\\an8}", "middle": "{\\an5}"}
_LEAD_TAG = re.compile(r"^\{\\an([1-9])\}")


def with_pos_tag(text: str, pos: str) -> str:
    return POS_TAGS.get(pos, "") + text


def split_pos_tag(text: str) -> tuple[str, str]:
    """'{\\an8}hello' -> ('hello', 'top'). Other alignments (bottom, or none) -> ''."""
    m = _LEAD_TAG.match(text)
    if not m:
        return text, ""
    return text[m.end():], {"7": "top", "8": "top", "9": "top", "4": "middle", "5": "middle", "6": "middle"}.get(m.group(1), "")


def to_srt(cues: list[tuple[float, float, str]]) -> str:
    """cues: [(start, end, text)]. Empty cues are dropped and the rest renumbered."""
    out = []
    n = 0
    for start, end, text in cues:
        text = text.strip()
        if not text:
            continue
        n += 1
        out.append(f"{n}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{text}\n")
    return "\n".join(out)


def write_srt(path: str | Path, cues: list[tuple[float, float, str]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_srt(cues), encoding="utf-8", newline="\n")
    return path


_BLOCK = re.compile(r"(\d+:\d\d:\d\d[,.]\d+)\s*-->\s*(\d+:\d\d:\d\d[,.]\d+)\s*\n(.*?)(?:\n\s*\n|\Z)", re.S)


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    return [(parse_ts(a), parse_ts(b), body.strip()) for a, b, body in _BLOCK.findall(text)]
