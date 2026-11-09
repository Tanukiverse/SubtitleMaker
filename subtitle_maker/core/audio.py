"""Pull the audio out of a video with ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW on Windows


class AudioError(RuntimeError):
    pass


def _tool(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise AudioError(f"ไม่พบ {name} ใน PATH (ติดตั้ง ffmpeg ก่อน)")
    return exe


def duration(path: str | Path) -> float:
    r = subprocess.run([_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    try:
        return float(r.stdout.strip())
    except ValueError as e:
        raise AudioError(f"อ่านความยาวของไฟล์ไม่ได้: {path}\n{r.stderr[-300:]}") from e


def extract(video: str | Path, wav: str | Path) -> Path:
    """16 kHz mono wav, the format Whisper wants. Arguments are a list so odd characters in paths are safe."""
    wav = Path(wav)
    wav.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_tool("ffmpeg"), "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW)
    if r.returncode != 0 or not wav.exists():
        raise AudioError(f"ffmpeg ดึงเสียงไม่ได้: {r.stderr[-400:]}")
    return wav
