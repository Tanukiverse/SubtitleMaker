"""App settings stored in settings.json at the project root."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .paths import ROOT, SETTINGS_PATH
from .project import atomic_write_text

ASR_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]


@dataclass
class Settings:
    work_dir: str = str(ROOT / "work")
    asr_model: str = "large-v3-turbo"
    source_lang: str = "auto"  # auto | ja | en | ...
    target_lang: str = "th"
    use_gpu: bool = True
    vad: bool = True
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "hf.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive:Q4_K_M"
    ollama_keep_alive: str = "5m"
    glossary: dict[str, str] = field(default_factory=dict)
    story_notes: str = ""
    # subtitle layout
    max_chars_per_line: int = 40  # Thai characters per line
    max_lines: int = 2
    max_cps: float = 17.0  # reading speed: characters per second
    min_duration: float = 1.0
    out_dir: str = ""  # empty = next to the video
    write_source_srt: bool = False  # also write the untranslated .srt
    ui_font_size: int = 11
    volume: int = 80  # player volume, 0-100
    table_font_size: int = 11  # cue list
    edit_font_size: int = 13  # text boxes under the list
    # subtitle look: the same on the video preview and in burned-in video (percent of the picture height)
    sub_size_pct: float = 6.0
    sub_bottom_pct: float = 5.0
    sub_color: str = "#ffffff"
    sub_outline: str = "#000000"
    burn_font: str = "Leelawadee UI"  # a Windows font that has Thai

    @classmethod
    def load(cls) -> "Settings":
        if not SETTINGS_PATH.exists():
            return cls()
        data = json.loads(SETTINGS_PATH.read_text("utf-8"))
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        atomic_write_text(SETTINGS_PATH, json.dumps(asdict(self), ensure_ascii=False, indent=2))
