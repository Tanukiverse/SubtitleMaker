"""Translate subtitle lines with a local Ollama model, a chunk at a time with the previous lines as context.
Request handling follows MangaTranslator's translate.py: Gemma sometimes answers empty under a forced
JSON schema, so a free-form retry reads the JSON (or one line per cue) out of the reply."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

import requests

LANG_NAMES = {"ja": "Japanese", "en": "English", "th": "Thai", "zh": "Chinese", "ko": "Korean"}
LANG_NATIVE = {"th": "ภาษาไทย", "ja": "日本語", "zh": "中文", "ko": "한국어", "en": "English"}
SCRIPT_RANGES = {"th": [("฀", "๿")], "ja": [("぀", "ヿ"), ("一", "鿿")],
                 "zh": [("一", "鿿")], "ko": [("가", "힯")]}


def in_target_script(text: str, tgt: str) -> bool:
    if not any(c.isalpha() for c in text):
        return True
    ranges = SCRIPT_RANGES.get(tgt)
    if ranges is None:
        return any("a" <= c.lower() <= "z" for c in text)
    return any(lo <= c <= hi for c in text for lo, hi in ranges)


@dataclass
class TranslatorConfig:
    url: str = "http://localhost:11434"
    model: str = "hf.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive:Q4_K_M"
    temperature: float = 0.3
    keep_alive: str = "5m"
    timeout: float = 300.0
    glossary: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    chunk: int = 25  # cues per request
    context: int = 4  # earlier cues shown as context with their translations


class TranslationError(RuntimeError):
    pass


class FatalTranslationError(TranslationError):
    """Retrying cannot help (Ollama unreachable, user pressed stop): abort instead of skipping the chunk."""


SCHEMA = {"type": "object", "properties": {"translations": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
    "required": ["id", "text"]}}}, "required": ["translations"]}


def build_messages(cues: list[tuple[int, str]], context: list[tuple[str, str]], src: str, tgt: str,
                   cfg: TranslatorConfig) -> list[dict]:
    src_name, tgt_name = LANG_NAMES.get(src, src or "the source language"), LANG_NAMES.get(tgt, tgt)
    system = (f"You are a professional subtitle translator. Translate video subtitles from {src_name} into natural, "
              f"conversational {tgt_name}. The cues are consecutive lines of one video, so use the surrounding "
              "lines as context for who is speaking and what is meant. Keep each translation short enough to read "
              "on screen in a couple of seconds. Do not add explanations, notes, speaker names or romanization. "
              "Translate every id exactly once.")
    if tgt == "th":
        system += (" Use Thai particles (ครับ, ค่ะ, นะ) only where they fit; never write alternatives with a slash "
                   "such as ค่ะ/ครับ. If the speaker's gender is unclear, leave the particle out.")
    if cfg.glossary:
        system += "\nAlways use these fixed translations:\n" + "\n".join(f"- {k} => {v}" for k, v in cfg.glossary.items())
    if cfg.notes:
        system += f"\nVideo context: {cfg.notes}"
    target = f"{tgt_name} ({LANG_NATIVE.get(tgt, tgt_name)})"
    payload: dict = {"translate_into": target}
    if context:
        payload["previous_lines_for_context_only"] = [{"source": a, "translation": b} for a, b in context]
    payload["cues"] = [{"id": i, "text": t} for i, t in cues]
    user = json.dumps(payload, ensure_ascii=False) + f"\n\nAnswer in {target} only."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def strip_id(answer: str, i: int) -> str:
    """In a free-form reply the model often numbers its lines with the cue id ('262 รุ่นพี่', '262. ...', '[262]')."""
    return re.sub(rf"^\s*[\[(]?{i}[\])]?\s*[.):\-–]?\s*", "", answer.strip())


_ALT = re.compile(r"\s*\(?\s*(ครับ|ค่ะ|คะ|ผม|ฉัน|ดิฉัน|นะครับ|นะคะ)\s*/\s*(ครับ|ค่ะ|คะ|ผม|ฉัน|ดิฉัน|นะครับ|นะคะ)\s*\)?")


_ARABIC = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def arabic_digits(text: str) -> str:
    return text.translate(_ARABIC)


def pick_one(text: str) -> str:
    """One speaker per cue ('ค่ะ/ครับ' -> 'ค่ะ'); also writes any Thai digits as Arabic ones."""
    return arabic_digits(_ALT.sub(lambda m: m.group(1), text))


def is_degenerate(text: str) -> bool:
    """A runaway loop like 'ฮั่ ฮั่ ฮั่ ฮั่' is not a translation."""
    words = text.split()
    return len(words) >= 4 and len(set(words)) / len(words) <= 0.34


def parse_response(content: str) -> dict[int, str]:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    if not content.startswith("{") and "{" in content and "}" in content:
        content = content[content.index("{"):content.rindex("}") + 1]
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise TranslationError(f"model returned invalid JSON: {content[:200]}") from e
    if not isinstance(data, dict):
        raise TranslationError(f"model returned no JSON object: {content[:200]}")
    out: dict[int, str] = {}
    if "translations" not in data:
        for k, v in data.items():
            if str(k).isdigit() and isinstance(v, str):
                out[int(k)] = pick_one(" ".join(v.split()))
        return out
    for item in data.get("translations", []):
        try:
            out[int(item["id"])] = pick_one(" ".join(str(item["text"]).split()))
        except (KeyError, TypeError, ValueError):
            continue
    return out


class OllamaTranslator:
    def __init__(self, cfg: TranslatorConfig | None = None, session: requests.Session | None = None):
        self.cfg = cfg or TranslatorConfig()
        self.http = session or requests.Session()
        self._think: bool | None = False
        self.wrong_language: list[int] = []
        self.failed: list[int] = []  # cue indices the model could not translate even one by one

    def list_models(self) -> list[str]:
        r = self.http.get(f"{self.cfg.url}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def _chat(self, messages, think, fmt=SCHEMA) -> str:
        # num_predict caps runaway loops ("ฮั่ ฮั่ ฮั่ ..."): a malformed reply then fails fast and is retried smaller
        body = {"model": self.cfg.model, "messages": messages, "stream": False,
                "keep_alive": self.cfg.keep_alive,
                "options": {"temperature": self.cfg.temperature, "repeat_penalty": 1.1,
                            "num_predict": 1024 + 200 * len(messages[-1]["content"].split('"id"'))}}
        if fmt is not None:
            body["format"] = fmt
        if think is not None:
            body["think"] = think
        try:
            r = self.http.post(f"{self.cfg.url}/api/chat", json=body, timeout=self.cfg.timeout)
        except requests.ConnectionError as e:
            raise FatalTranslationError(f"เชื่อมต่อ Ollama ไม่ได้ที่ {self.cfg.url} (เปิด Ollama แล้วหรือยัง?)") from e
        except requests.Timeout as e:
            raise TranslationError(f"Ollama ตอบช้าเกิน {self.cfg.timeout:.0f} วินาที") from e
        if r.status_code >= 400:
            raise TranslationError(f"Ollama {r.status_code}: {r.text[:300]}")
        return r.json()["message"]["content"]

    def _ask(self, messages, fmt=SCHEMA) -> str:
        try:
            return self._chat(messages, think=self._think, fmt=fmt)
        except TranslationError as e:
            if self._think is None or "think" not in str(e).lower():
                raise
            self._think = None
            return self._chat(messages, think=None, fmt=fmt)

    def _translate_chunk(self, cues: list[tuple[int, str]], context: list[tuple[str, str]], src: str, tgt: str) -> dict[int, str]:
        messages = build_messages(cues, context, src, tgt, self.cfg)
        try:
            result = parse_response(self._ask(messages))
        except TranslationError:
            free = self._ask(messages, fmt=None)
            try:
                result = parse_response(free)
            except TranslationError:
                answers = [a.strip().strip('"“”').strip() for a in free.strip().splitlines() if a.strip()]
                if len(cues) == 1 and answers:
                    answers = [" ".join(answers)]
                if len(answers) != len(cues) or any(is_degenerate(a) for a in answers):
                    raise
                result = {i: pick_one(" ".join(strip_id(a, i).split())) for (i, _), a in zip(cues, answers)}
        wrong = [i for i, t in result.items() if not in_target_script(t, tgt)]
        if wrong:  # some finetunes answer in the source language: ask once more in plain JSON mode
            again = messages[:-1] + [{**messages[-1], "content": messages[-1]["content"] +
                                      f"\n\nImportant: write every translation in {LANG_NAMES.get(tgt, tgt)} only. Answer as JSON."}]
            try:
                for i, t in parse_response(self._ask(again, fmt="json")).items():
                    if i in wrong and in_target_script(t, tgt):
                        result[i] = t
            except FatalTranslationError:
                raise
            except TranslationError:
                pass  # keep what we have; the lines stay in wrong_language
        self.wrong_language += [i for i, t in result.items() if not in_target_script(t, tgt)]
        for i, t in [(i, t) for i, t in cues if not result.get(i, "").strip()]:
            single = parse_response(self._ask(build_messages([(i, t)], context, src, tgt, self.cfg)))
            if single:
                result[i] = next(iter(single.values()))
        return result

    def _translate_robust(self, cues: list[tuple[int, str]], context: list[tuple[str, str]], src: str, tgt: str) -> dict[int, str]:
        """One bad reply must not abort the whole video: on failure split the chunk in half and retry, down to a
        single cue; a cue that still fails is recorded in self.failed and left untranslated."""
        try:
            return self._translate_chunk(cues, context, src, tgt)
        except FatalTranslationError:
            raise
        except TranslationError:
            if len(cues) == 1:
                try:  # one more try, a bit warmer, so a deterministic loop can break
                    old, self.cfg.temperature = self.cfg.temperature, min(1.0, self.cfg.temperature + 0.4)
                    try:
                        return self._translate_chunk(cues, context, src, tgt)
                    finally:
                        self.cfg.temperature = old
                except FatalTranslationError:
                    raise
                except TranslationError:
                    self.failed.append(cues[0][0])
                    return {}
            mid = len(cues) // 2
            left = self._translate_robust(cues[:mid], context, src, tgt)
            ctx = context + [(t, left[i]) for i, t in cues[:mid] if left.get(i)]
            right = self._translate_robust(cues[mid:], ctx[-self.cfg.context:], src, tgt)
            return {**left, **right}

    def translate(self, texts: list[str], src: str, tgt: str, keep: dict[int, str] | None = None,
                  progress: Callable[[float], None] | None = None,
                  should_stop: Callable[[], bool] | None = None) -> list[str]:
        """texts in order; returns translations in the same order. `keep` = {index: text} for cues that must not
        be translated again (hand-edited): they are used as context only."""
        keep = keep or {}
        out = [keep.get(i, "") for i in range(len(texts))]
        todo = [i for i, t in enumerate(texts) if i not in keep and t.strip()]
        self.wrong_language = []
        self.failed = []
        done = 0
        for k in range(0, len(todo), self.cfg.chunk):
            if should_stop and should_stop():
                raise FatalTranslationError("หยุดโดยผู้ใช้")
            chunk = todo[k:k + self.cfg.chunk]
            first = chunk[0]
            ctx = [(texts[j], out[j]) for j in range(max(0, first - self.cfg.context), first) if out[j]]
            res = self._translate_robust([(i, texts[i]) for i in chunk], ctx, src, tgt)
            for i in chunk:
                out[i] = res.get(i, "")
            done += len(chunk)
            if progress:
                progress(done / len(todo))
        return out

    def translate_some(self, texts: list[str], current: list[str], indices: list[int], src: str, tgt: str,
                       progress: Callable[[float], None] | None = None) -> dict[int, str]:
        """Translate only the cues in `indices` again. The lines before each chunk (with their current
        translations) go along as context. Returns {index: new translation}."""
        todo = sorted(i for i in set(indices) if 0 <= i < len(texts) and texts[i].strip())
        cur = list(current)
        out: dict[int, str] = {}
        self.wrong_language = []
        self.failed = []
        for k in range(0, len(todo), self.cfg.chunk):
            chunk = todo[k:k + self.cfg.chunk]
            first = chunk[0]
            ctx = [(texts[j], cur[j]) for j in range(max(0, first - self.cfg.context), first) if cur[j]]
            res = self._translate_robust([(i, texts[i]) for i in chunk], ctx, src, tgt)
            for i in chunk:
                if res.get(i, "").strip():
                    out[i] = cur[i] = res[i]
            if progress:
                progress(min(1.0, (k + len(chunk)) / len(todo)))
        return out

    def unload(self) -> None:
        try:
            self.http.post(f"{self.cfg.url}/api/generate", json={"model": self.cfg.model, "keep_alive": 0}, timeout=30)
        except requests.RequestException:
            pass


def parse_terms(content: str) -> list[tuple[str, str]]:
    """[(source term, suggested translation)] from a reply like {"terms":[{"source":"..","thai":".."}]}."""
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    for a, b in (("{", "}"), ("[", "]")):
        if not content.startswith(a) and a in content and b in content:
            content = content[content.index(a):content.rindex(b) + 1]
            break
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    items = data.get("terms", []) if isinstance(data, dict) else data
    out: list[tuple[str, str]] = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            src = str(it.get("source", "")).strip()
            dst = str(it.get("thai", it.get("translation", ""))).strip()
            if src and dst and src != dst:
                out.append((src, dst))
    return out


def extract_terms(tr: "OllamaTranslator", texts: list[str], src: str, tgt: str, known: dict[str, str] | None = None,
                  per_request: int = 120, progress: Callable[[float], None] | None = None) -> list[tuple[str, str]]:
    """Ask the model for the proper names and special terms in the subtitles so the user can fix their
    translation once, before the whole video is translated. Skips terms already in `known`."""
    known = known or {}
    src_name, tgt_name = LANG_NAMES.get(src, src or "the source language"), LANG_NAMES.get(tgt, tgt)
    system = (f"You read {src_name} subtitles. List the character names, place names, organization names and special terms "
              f"(attack names, item names, titles) that must be translated the same way every time. For each give its "
              f"{tgt_name} form. Skip ordinary words and honorific-only words. Answer with JSON only: "
              '{"terms":[{"source":"...","thai":"..."}]}. At most 30 terms.')
    found: dict[str, str] = {}
    chunks = [texts[i:i + per_request] for i in range(0, len(texts), per_request)] or [[]]
    for n, chunk in enumerate(chunks):
        body = "\n".join(t for t in chunk if t.strip())
        if body:
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": body}]
            try:
                for a, b in parse_terms(tr._ask(msgs, fmt=None)):
                    if a not in known:
                        found.setdefault(a, b)
            except TranslationError:
                raise
        if progress:
            progress((n + 1) / len(chunks))
    return list(found.items())
