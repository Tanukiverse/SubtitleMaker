"""The steps in order: extract audio, transcribe, tidy, translate, lay out, export. Each step saves project.json."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import asr, audio, segment, srt, thai_lines
from .project import Project
from .settings import Settings
from .translate import OllamaTranslator, TranslatorConfig

STEPS = ["audio", "asr", "tidy", "translate", "export"]
STEP_LABELS = {"audio": "1. ดึงเสียง", "asr": "2. ถอดเสียง", "tidy": "3. จัดบรรทัด",
               "translate": "4. แปล", "export": "5. ส่งออก .srt"}

Progress = Callable[[str, float], None]  # (step, 0..1)


class StepRunner:
    def __init__(self, settings: Settings, progress: Progress | None = None, should_stop: Callable[[], bool] | None = None):
        self.s = settings
        self.progress = progress or (lambda step, f: None)
        self.should_stop = should_stop

    def work_dir(self, video: str | Path) -> Path:
        return Project.dir_for(self.s.work_dir, video)

    def srt_path(self, video: str | Path, lang: str) -> Path:
        out = Path(self.s.out_dir) if self.s.out_dir else Path(video).parent
        return out / f"{Path(video).stem}.{lang}.srt"

    # ---- steps ----
    def do_audio(self, p: Project) -> None:
        wav = self.work_dir(p.video) / "audio.wav"
        self.progress("audio", 0.0)
        audio.extract(p.video, wav)
        p.done["audio"] = True
        self.progress("audio", 1.0)

    def do_asr(self, p: Project) -> None:
        wav = self.work_dir(p.video) / "audio.wav"
        if not wav.exists():
            self.do_audio(p)
        segs, lang = asr.transcribe(wav, self.s.asr_model, self.s.source_lang, self.s.use_gpu, self.s.vad,
                                    progress=lambda f: self.progress("asr", f), should_stop=self.should_stop)
        # hand-edited cues survive a re-run only if they were kept; a fresh transcription replaces everything
        p.segments, p.lang = segs, lang
        p.done["asr"] = True
        p.done.pop("tidy", None), p.done.pop("translate", None)

    def do_tidy(self, p: Project) -> None:
        p.segments = segment.merge_short(segment.drop_junk(p.segments))
        p.done["tidy"] = True
        self.progress("tidy", 1.0)

    def do_translate(self, p: Project) -> None:
        cfg = TranslatorConfig(url=self.s.ollama_url, model=self.s.ollama_model, keep_alive=self.s.ollama_keep_alive,
                               glossary=self.s.glossary, notes=self.s.story_notes)
        tr = OllamaTranslator(cfg)
        keep = {i: s.th for i, s in enumerate(p.segments) if s.locked and s.th}
        res = tr.translate([s.text for s in p.segments], p.lang, self.s.target_lang, keep=keep,
                           progress=lambda f: self.progress("translate", f), should_stop=self.should_stop)
        for s, t in zip(p.segments, res):
            if not s.locked:
                s.th = t
        p.done["translate"] = True
        tr.unload()  # give the VRAM back

    def do_export(self, p: Project) -> list[Path]:
        for s in p.segments:  # lay out unlocked cues; a locked cue keeps the line breaks the user typed
            if not s.locked and s.th:
                s.th = thai_lines.wrap(s.th, self.s.max_chars_per_line, self.s.max_lines)
        segment.fix_timing(p.segments, self.s.min_duration, self.s.max_cps)
        if not p.done.get("translate"):  # nothing translated yet: export the transcript so the file is not empty
            return self._finish_export(p, [srt.write_srt(self.srt_path(p.video, p.lang or "src"),
                                                         [(s.start, s.end, s.text) for s in p.segments])])
        written = [srt.write_srt(self.srt_path(p.video, self.s.target_lang), [(s.start, s.end, s.th) for s in p.segments])]
        if self.s.write_source_srt:
            written.append(srt.write_srt(self.srt_path(p.video, p.lang or "src"), [(s.start, s.end, s.text) for s in p.segments]))
        return self._finish_export(p, written)

    def write_as_is(self, p: Project) -> list[Path]:
        """Save the .srt exactly as the cues stand (for the editor: no re-wrapping, no retiming)."""
        translated = p.done.get("translate") or any(s.th for s in p.segments)
        if translated:
            written = [srt.write_srt(self.srt_path(p.video, self.s.target_lang), [(s.start, s.end, s.th) for s in p.segments])]
            if self.s.write_source_srt:
                written.append(srt.write_srt(self.srt_path(p.video, p.lang or "src"), [(s.start, s.end, s.text) for s in p.segments]))
            return written
        return [srt.write_srt(self.srt_path(p.video, p.lang or "src"), [(s.start, s.end, s.text) for s in p.segments])]

    def _finish_export(self, p: Project, written: list[Path]) -> list[Path]:
        p.done["export"] = True
        self.progress("export", 1.0)
        return written

    # ---- run ----
    def run(self, video: str | Path, steps: list[str] | None = None) -> tuple[Project, list[Path]]:
        p = Project.open(self.s.work_dir, video)
        written: list[Path] = []
        fn = {"audio": self.do_audio, "asr": self.do_asr, "tidy": self.do_tidy, "translate": self.do_translate}
        for step in steps or STEPS:
            if self.should_stop and self.should_stop():
                break
            if step == "export":
                written = self.do_export(p)
            else:
                fn[step](p)
            p.save(self.s.work_dir)
        return p, written
