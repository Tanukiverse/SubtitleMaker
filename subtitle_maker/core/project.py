"""A subtitle project: the segments of one video and the state of each step, saved as project.json."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@dataclass
class Seg:
    start: float
    end: float
    text: str  # source-language text
    th: str = ""  # translation
    locked: bool = False  # edited by hand: automatic steps never overwrite it


@dataclass
class Project:
    video: str
    lang: str = ""  # language detected or chosen for the audio
    segments: list[Seg] = field(default_factory=list)
    done: dict[str, bool] = field(default_factory=dict)  # step name -> finished

    @staticmethod
    def dir_for(work_dir: str | Path, video: str | Path) -> Path:
        name = re.sub(r'[<>:"/\\|?*]', "_", Path(video).stem).strip() or "video"
        return Path(work_dir) / name

    @classmethod
    def open(cls, work_dir: str | Path, video: str | Path) -> "Project":
        d = cls.dir_for(work_dir, video)
        f = d / "project.json"
        if f.exists():
            data = json.loads(f.read_text("utf-8"))
            p = cls(video=str(video), lang=data.get("lang", ""), done=data.get("done", {}))
            p.segments = [Seg(**s) for s in data.get("segments", [])]
            return p
        return cls(video=str(video))

    def save(self, work_dir: str | Path) -> None:
        d = self.dir_for(work_dir, self.video)
        atomic_write_text(d / "project.json", json.dumps(
            {"video": self.video, "lang": self.lang, "done": self.done,
             "segments": [asdict(s) for s in self.segments]}, ensure_ascii=False, indent=1))
