"""Speech to text with faster-whisper. The model is freed after each run so Ollama gets the VRAM (8 GB card)."""
from __future__ import annotations

import gc
import os
import sys
from pathlib import Path
from typing import Callable

from .paths import WHISPER_DIR
from . import segment
from .project import Seg


os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Windows without Developer Mode has no symlinks


class AsrError(RuntimeError):
    pass


def _add_cuda_dll_dirs() -> None:
    """ctranslate2 needs cuBLAS/cuDNN DLLs: from the nvidia-*-cu12 wheels (site-packages/nvidia/*/bin)."""
    if sys.platform != "win32":
        return
    for base in sys.path:
        for lib in sorted((Path(base) / "nvidia").glob("*/bin")) if (Path(base) / "nvidia").is_dir() else []:
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")


def read_wav(path: str | Path):
    """16-bit mono wav (as written by audio.extract) -> float32 array in [-1, 1]."""
    import wave

    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise AsrError("ไฟล์เสียงต้องเป็น 16-bit mono (ใช้ audio.extract)")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


SENTENCE_END = "。？！?!…"


def chunk_words(words, max_gap: float = 0.9, max_dur: float = 6.5, pad: float = 0.2) -> list[Seg]:
    """Cut one Whisper segment into cues from its word timings. Whisper's own segment end often runs on
    through silence or music up to the next line (a 9-character line shown for 118 s), so the cue starts at
    its first word and ends at its last. New cue at a pause longer than max_gap, after sentence-ending
    punctuation (once there is a little text), or when the cue would pass max_dur."""
    out: list[Seg] = []
    cur: list = []

    def flush() -> None:
        if not cur:
            return
        text = "".join(w.word for w in cur).strip()
        if text:
            out.append(Seg(start=float(cur[0].start), end=float(cur[-1].end) + pad, text=text))
        cur.clear()

    for w in words:
        if cur and (w.start - cur[-1].end > max_gap or w.end - cur[0].start > max_dur):
            flush()
        cur.append(w)
        if w.word.strip()[-1:] in SENTENCE_END and len("".join(x.word for x in cur).strip()) >= 6:
            flush()
    flush()
    for a, b in zip(out, out[1:]):  # padding must not run into the next cue
        a.end = min(a.end, max(b.start - 0.04, a.start + 0.2))
    return out


def _load(model: str, use_gpu: bool):
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    if use_gpu:
        try:
            return WhisperModel(model, device="cuda", compute_type="float16", download_root=str(WHISPER_DIR)), "cuda"
        except Exception as e:  # CUDA missing or too old for this card
            print(f"[asr] โหลดบน GPU ไม่ได้ ({e}); ใช้ CPU แทน", file=sys.stderr)
    return WhisperModel(model, device="cpu", compute_type="int8", download_root=str(WHISPER_DIR)), "cpu"


def find_gaps(cues: list[Seg], samples, rate: int = 16000, min_gap: float = 4.0, loud: float = 0.5) -> list[tuple[float, float]]:
    """Stretches of at least `min_gap` seconds with no cue but with sound about as loud as the speech that was found."""
    import numpy as np
    total = len(samples) / rate

    def rms(a: float, b: float) -> float:
        seg = samples[int(a * rate):int(b * rate)]
        return float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0

    spoken = [rms(c.start, c.end) for c in cues if c.end > c.start]
    floor = loud * (float(np.median(spoken)) if spoken else 0.01)
    gaps, prev = [], 0.0
    for c in sorted(cues, key=lambda x: x.start):
        if c.start - prev >= min_gap and rms(prev, c.start) >= floor:
            gaps.append((prev, c.start))
        prev = max(prev, c.end)
    if total - prev >= min_gap and rms(prev, total) >= floor:
        gaps.append((prev, total))
    return gaps


def _collect(seg_iter, offset: float = 0.0, strict: bool = False, should_stop=None, on_segment=None) -> list[Seg]:
    """Turn Whisper segments into cues. Junk is dropped per segment, before the segment is cut into cues
    (cut up, 'ご視聴ありがとうございました' no longer looks like itself). strict = also drop segments Whisper itself
    doubts (used on the VAD-less pass over music/noise, where it hallucinates most)."""
    out: list[Seg] = []
    for sg in seg_iter:
        if should_stop and should_stop():
            raise AsrError("หยุดโดยผู้ใช้")
        if on_segment:
            on_segment(sg)
        text = sg.text.strip()
        if not text or segment.is_junk(text):
            continue
        if strict and (sg.no_speech_prob > 0.6 or sg.avg_logprob < -1.0):
            continue
        cues = chunk_words(sg.words) if sg.words else [Seg(float(sg.start), float(sg.end), text)]
        for c in cues:
            c.start += offset
            c.end += offset
        out += cues
    return out


def transcribe(wav: str | Path, model: str = "large-v3-turbo", language: str = "auto", use_gpu: bool = True,
               vad: bool = True, progress: Callable[[float], None] | None = None,
               should_stop: Callable[[], bool] | None = None, fill_gaps: bool = True) -> tuple[list[Seg], str]:
    """Returns (segments, language). language 'auto' lets Whisper detect it from the first 30 s.
    fill_gaps: after the VAD pass, transcribe again, without VAD, the loud stretches that got no cue
    (opening songs, speech under music): the VAD drops those."""
    wm, device = _load(model, use_gpu)
    try:
        # read our own 16 kHz mono wav instead of letting faster-whisper decode it with PyAV
        # (PyAV 19 rejects the metadata_errors argument faster-whisper 1.2 passes)
        samples = read_wav(wav)
        segs_iter, info = wm.transcribe(
            samples, language=None if language in ("", "auto") else language,
            vad_filter=vad, vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5, condition_on_previous_text=False, word_timestamps=True,  # stops one hallucination repeating down the file
        )
        total = max(info.duration, 0.001)
        share = 0.85 if (fill_gaps and vad) else 1.0

        def on_seg(sg) -> None:
            if progress:
                progress(min(share, sg.end / total * share))

        out = _collect(segs_iter, should_stop=should_stop, on_segment=on_seg)

        if fill_gaps and vad:
            gaps = find_gaps(out, samples)
            extra: list[Seg] = []
            for n, (a, b) in enumerate(gaps):
                it, _ = wm.transcribe(samples[int(a * 16000):int(b * 16000)], language=info.language, vad_filter=False,
                                      beam_size=5, condition_on_previous_text=False, word_timestamps=True)
                extra += _collect(it, offset=a, strict=True, should_stop=should_stop)
                if progress:
                    progress(share + (1 - share) * (n + 1) / len(gaps))
            # keep only what does not run into a cue the first pass already found
            extra = [c for c in extra if not any(c.start < o.end - 0.3 and c.end > o.start + 0.3 for o in out)]
            out = sorted(out + segment.drop_junk(extra), key=lambda c: c.start)
        for a, b in zip(out, out[1:]):  # a cue must not run into the next one
            a.end = min(a.end, max(b.start - 0.04, a.start + 0.2))
        return out, info.language
    finally:
        del wm
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
