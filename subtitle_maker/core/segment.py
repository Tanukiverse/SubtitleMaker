"""Clean up Whisper segments before translation: drop hallucinated repeats, merge fragments, fix timings."""
from __future__ import annotations

import re

from .project import Seg

# Whisper tends to invent these on silence or music
_JUNK = re.compile(r"^[\s\.\,\!\?\-♪♫~…・。、]*$|ご視聴ありがとうございました|チャンネル登録|Thanks for watching|Subtitles by|"
                   r"字幕|Amara\.org", re.I)


def is_junk(text: str) -> bool:
    return bool(_JUNK.search(text))


def drop_junk(segs: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    repeats = 0
    for s in segs:
        if is_junk(s.text):
            continue
        # the same line three or more times in a row is a hallucination loop
        if out and s.text == out[-1].text:
            repeats += 1
            if repeats >= 2:
                continue
        else:
            repeats = 0
        out.append(s)
    return out


def merge_short(segs: list[Seg], gap: float = 0.3, max_dur: float = 6.0, max_chars: int = 80) -> list[Seg]:
    """Join a fragment into its neighbour when they are close in time and the result is still short."""
    out: list[Seg] = []
    for s in segs:
        if out:
            p = out[-1]
            short = len(p.text) < 8 or len(s.text) < 8
            if short and s.start - p.end <= gap and s.end - p.start <= max_dur and len(p.text) + len(s.text) <= max_chars \
                    and not p.locked and not s.locked:
                sep = "" if _cjk_end(p.text) else " "
                out[-1] = Seg(p.start, s.end, p.text + sep + s.text)
                continue
        out.append(Seg(s.start, s.end, s.text, s.th, s.locked))
    return out


def _cjk_end(text: str) -> bool:
    return bool(text) and ord(text[-1]) > 0x2E80


def fix_timing(segs: list[Seg], min_duration: float = 1.0, max_cps: float = 17.0) -> list[Seg]:
    """Give a cue enough time to read (up to the start of the next cue) and remove overlaps."""
    for i, s in enumerate(segs):
        nxt = segs[i + 1].start if i + 1 < len(segs) else float("inf")
        need = max(min_duration, len((s.th or s.text).replace("\n", "")) / max_cps if max_cps else 0)
        s.end = max(s.end, min(s.start + need, nxt - 0.04))
        if s.end > nxt:
            s.end = nxt
        if s.end <= s.start:
            s.end = s.start + 0.2
    return segs
