"""Editing operations on a list of cues with undo/redo. No Qt in here, so it is testable.
The list passed in is edited in place (it is the Project's own segments list)."""
from __future__ import annotations

import time
from dataclasses import replace

from .project import Seg

MIN_DUR = 0.1


def cps(seg: Seg, field: str = "th") -> float:
    text = (getattr(seg, field) or seg.text).replace("\n", "")
    return len(text) / max(seg.end - seg.start, 0.001)


class Editor:
    def __init__(self, segs: list[Seg], limit: int = 200):
        self.segs = segs
        self.limit = limit
        self._undo: list[list[Seg]] = []
        self._redo: list[list[Seg]] = []
        self._key = None
        self._t = 0.0

    # ---- undo
    def _snapshot(self) -> list[Seg]:
        return [replace(s) for s in self.segs]

    def push(self, key=None, coalesce: float = 1.5) -> None:
        """Remember the current state before a change. Edits with the same key within `coalesce` seconds
        (typing in one box) share one undo step."""
        now = time.monotonic()
        if key is not None and key == self._key and now - self._t < coalesce:
            self._t = now
            return
        self._undo.append(self._snapshot())
        del self._undo[:-self.limit]
        self._redo.clear()
        self._key, self._t = key, now

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self.segs[:] = self._undo.pop()
        self._key = None
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self.segs[:] = self._redo.pop()
        self._key = None
        return True

    def reset(self) -> None:
        self._undo.clear(); self._redo.clear(); self._key = None

    # ---- single cue
    def set_text(self, i: int, field: str, text: str) -> None:
        """field 'th' (translation: the cue becomes hand-edited, so automatic steps keep it) or 'text' (source)."""
        s = self.segs[i]
        if getattr(s, field) == text:
            return
        self.push((field, i))
        setattr(s, field, text)
        if field == "th":
            s.locked = True

    def set_times(self, i: int, start: float, end: float, push: bool = True) -> int:
        """Returns the cue's index afterwards (the list stays sorted by start)."""
        s = self.segs[i]
        start = max(0.0, start)
        end = max(end, start + MIN_DUR)
        if (s.start, s.end) == (start, end):
            return i
        if push:
            self.push(("time", i))
        s.start, s.end = start, end
        return self.sort(s)

    def sort(self, keep: Seg | None = None) -> int:
        self.segs.sort(key=lambda x: x.start)  # stable
        return next((k for k, x in enumerate(self.segs) if x is keep), -1) if keep is not None else -1

    # ---- structure
    def insert_after(self, i: int) -> int:
        """New empty cue after cue i (or the first cue when the list is empty). Returns its index."""
        self.push()
        if not self.segs:
            self.segs.append(Seg(0.0, 2.0, "", "", True))
            return 0
        i = max(0, min(i, len(self.segs) - 1))
        start = self.segs[i].end + 0.04
        end = start + 2.0
        if i + 1 < len(self.segs):
            end = min(end, max(self.segs[i + 1].start - 0.04, start + 0.3))
        self.segs.insert(i + 1, Seg(start, end, "", "", True))
        return i + 1

    def delete(self, indices: list[int]) -> None:
        if not indices:
            return
        self.push()
        for i in sorted(set(indices), reverse=True):
            if 0 <= i < len(self.segs):
                del self.segs[i]

    def merge_next(self, i: int) -> bool:
        if not 0 <= i < len(self.segs) - 1:
            return False
        self.push()
        a, b = self.segs[i], self.segs.pop(i + 1)
        a.text = _join(a.text, b.text, cjk_ok=True)
        a.th = _join(a.th, b.th)
        a.end = max(a.end, b.end)
        a.locked = a.locked or b.locked
        return True

    def split(self, i: int, pos: int, field: str = "th") -> int:
        """Split cue i at character `pos` of `field`. Time is split in proportion. Returns index of the second half or -1."""
        s = self.segs[i]
        text = getattr(s, field)
        if not 0 < pos < len(text):
            return -1
        self.push()
        frac = pos / len(text)
        mid = s.start + (s.end - s.start) * frac
        other = "text" if field == "th" else "th"
        o = getattr(s, other)
        opos = round(len(o) * frac)
        first, second = text[:pos].strip(), text[pos:].strip()
        o1, o2 = o[:opos].strip(), o[opos:].strip()
        b = Seg(mid + 0.02, s.end, "", "", True)
        s.end = mid
        s.locked = True
        setattr(s, field, first); setattr(b, field, second)
        setattr(s, other, o1); setattr(b, other, o2)
        self.segs.insert(i + 1, b)
        return i + 1

    # ---- whole list
    def import_cues(self, cues: list[tuple[float, float, str]], field: str = "th", keep_other: bool = False) -> None:
        """Replace the cue list with cues read from a .srt. field 'th' = they are the translation (hand-made
        subtitles: the cues are locked so automatic steps keep them), 'text' = they are the source language.
        keep_other: keep the other field of the old cue that starts at about the same time (re-timing an existing job)."""
        self.push()
        old = list(self.segs)
        new = []
        for start, end, text in cues:
            seg = Seg(float(start), float(end), "", "", field == "th")
            setattr(seg, field, text)
            if keep_other:
                other = "text" if field == "th" else "th"
                match = min(old, key=lambda o: abs(o.start - start), default=None)
                if match is not None and abs(match.start - start) < 1.0:
                    setattr(seg, other, getattr(match, other))
            new.append(seg)
        self.segs[:] = sorted(new, key=lambda x: x.start)

    def shift(self, delta: float, from_index: int = 0) -> None:
        self.push()
        for s in self.segs[from_index:]:
            s.start = max(0.0, s.start + delta)
            s.end = max(s.start + MIN_DUR, s.end + delta)

    def find(self, needle: str, field: str, start: int = 0, case: bool = False) -> int:
        """Index of the next cue (from `start`, wrapping round) whose field contains needle, or -1."""
        if not needle or not self.segs:
            return -1
        n = needle if case else needle.lower()
        for k in range(len(self.segs)):
            i = (start + k) % len(self.segs)
            t = getattr(self.segs[i], field)
            if n in (t if case else t.lower()):
                return i
        return -1

    def replace_in(self, i: int, needle: str, repl: str, field: str, case: bool = False) -> int:
        s = self.segs[i]
        new, n = _replace(getattr(s, field), needle, repl, case)
        if n:
            self.push()
            setattr(s, field, new)
            if field == "th":
                s.locked = True
        return n

    def replace_all(self, needle: str, repl: str, field: str, case: bool = False) -> int:
        if not needle:
            return 0
        changes = [(s, *_replace(getattr(s, field), needle, repl, case)) for s in self.segs]
        changes = [c for c in changes if c[2]]
        if not changes:
            return 0
        self.push()
        for s, new, _ in changes:
            setattr(s, field, new)
            if field == "th":
                s.locked = True
        return sum(c[2] for c in changes)


def _join(a: str, b: str, cjk_ok: bool = False) -> str:
    a, b = " ".join(a.split()), " ".join(b.split())
    if not a or not b:
        return a or b
    if cjk_ok and ord(a[-1]) > 0x2E80:
        return a + b
    return a + " " + b


def _replace(text: str, needle: str, repl: str, case: bool) -> tuple[str, int]:
    if case:
        return text.replace(needle, repl), text.count(needle)
    import re
    return re.subn(re.escape(needle), lambda _m: repl, text, flags=re.I)
