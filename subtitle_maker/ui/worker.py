"""Runs the pipeline on a background thread. Signals must be connected to methods of a QObject,
never to lambdas: a lambda would run on this thread and touch widgets (crashes Qt6)."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..core import export
from ..core.pipeline import StepRunner
from ..core.settings import Settings
from ..core.translate import OllamaTranslator, TranslatorConfig, extract_terms


class Worker(QThread):
    progress = Signal(str, float)  # step, 0..1
    file_started = Signal(str)
    file_done = Signal(str, str)  # video, message
    file_failed = Signal(str, str)  # video, error

    def __init__(self, settings: Settings, videos: list[str], steps: list[str] | None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.videos = videos
        self.steps = steps
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _emit_progress(self, step: str, f: float) -> None:
        self.progress.emit(step, f)

    def run(self) -> None:
        runner = StepRunner(self.settings, self._emit_progress, lambda: self._stop)
        for v in self.videos:
            if self._stop:
                break
            self.file_started.emit(v)
            try:
                p, written = runner.run(v, self.steps)
                self.file_done.emit(v, f"{len(p.segments)} บรรทัด" + (f" → {written[0].name}" if written else ""))
            except Exception as e:  # one broken file must not stop the queue
                self.file_failed.emit(v, str(e))


class RetranslateWorker(QThread):
    """Translate again just some cues (the ones selected in the list)."""
    done = Signal(object)  # {index: translation}
    failed = Signal(str)

    def __init__(self, settings: Settings, texts: list[str], current: list[str], indices: list[int], src: str, parent=None):
        super().__init__(parent)
        self.s, self.texts, self.current, self.indices, self.src = settings, texts, current, indices, src

    def run(self) -> None:
        cfg = TranslatorConfig(url=self.s.ollama_url, model=self.s.ollama_model, keep_alive=self.s.ollama_keep_alive,
                               glossary=self.s.glossary, notes=self.s.story_notes)
        tr = OllamaTranslator(cfg)
        try:
            self.done.emit(tr.translate_some(self.texts, self.current, self.indices, self.src, self.s.target_lang))
        except Exception as e:
            self.failed.emit(str(e))


class TermsWorker(QThread):
    """Find names and special terms in the whole transcript."""
    progress = Signal(float)
    done = Signal(object)  # [(source, translation)]
    failed = Signal(str)

    def __init__(self, settings: Settings, texts: list[str], src: str, parent=None):
        super().__init__(parent)
        self.s, self.texts, self.src = settings, texts, src

    def _emit(self, f: float) -> None:
        self.progress.emit(f)

    def run(self) -> None:
        cfg = TranslatorConfig(url=self.s.ollama_url, model=self.s.ollama_model, keep_alive=self.s.ollama_keep_alive)
        tr = OllamaTranslator(cfg)
        try:
            self.done.emit(extract_terms(tr, self.texts, self.src, self.s.target_lang, self.s.glossary, progress=self._emit))
        except Exception as e:
            self.failed.emit(str(e))


class ExportWorker(QThread):
    """Burn the subtitles into the video, or add them as a track (ffmpeg, minutes for a long video)."""
    progress = Signal(float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, mode: str, settings: Settings, video: str, srt_file: str, out: str, cues: list | None = None, parent=None):
        super().__init__(parent)
        self.mode, self.s, self.video, self.srt_file, self.out, self.cues = mode, settings, video, srt_file, out, cues or []
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _emit(self, f: float) -> None:
        self.progress.emit(f)

    def run(self) -> None:
        try:
            if self.mode == "burn":
                path = export.burn_in(self.video, self.cues, self.out, self.s, self._emit, lambda: self._stop)
            else:
                path = export.soft_sub(self.video, self.srt_file, self.out, progress=self._emit, should_stop=lambda: self._stop)
            self.done.emit(str(path))
        except Exception as e:
            self.failed.emit(str(e))
