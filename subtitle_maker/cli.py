"""python -m subtitle_maker.cli video.mp4 [--from auto] [--to th] [--steps asr,tidy,export]"""
from __future__ import annotations

import argparse
import sys

from .core.pipeline import STEPS, StepRunner
from .core.settings import ASR_MODELS, Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ทำซับวิดีโออัตโนมัติ: ถอดเสียง -> แปล -> .srt")
    ap.add_argument("video", nargs="+")
    ap.add_argument("--from", dest="src", default=None, help="ภาษาเสียง (auto, ja, en, ...)")
    ap.add_argument("--to", dest="tgt", default=None, help="ภาษาซับ (ค่าเริ่มต้น th)")
    ap.add_argument("--model", choices=ASR_MODELS, default=None, help="โมเดล Whisper")
    ap.add_argument("--steps", default=",".join(STEPS), help="เช่น asr,tidy,export (ไม่แปล)")
    ap.add_argument("--cpu", action="store_true", help="ไม่ใช้ GPU")
    a = ap.parse_args(argv)

    s = Settings.load()
    if a.src:
        s.source_lang = a.src
    if a.tgt:
        s.target_lang = a.tgt
    if a.model:
        s.asr_model = a.model
    if a.cpu:
        s.use_gpu = False
    steps = [x for x in a.steps.split(",") if x]
    bad = [x for x in steps if x not in STEPS]
    if bad:
        ap.error(f"ไม่รู้จักขั้น: {bad} (ใช้ได้: {STEPS})")

    last = {"k": ""}

    def progress(step: str, f: float) -> None:
        k = f"{step} {int(f * 10) * 10}%"
        if k != last["k"]:
            last["k"] = k
            print(f"[{k}]", flush=True)

    runner = StepRunner(s, progress)
    rc = 0
    for v in a.video:
        try:
            p, written = runner.run(v, steps)
            print(f"{v}: {len(p.segments)} บรรทัด ภาษา={p.lang}")
            for w in written:
                print("  ->", w)
        except Exception as e:  # keep going with the next file
            rc = 1
            print(f"{v}: ล้มเหลว: {e}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
