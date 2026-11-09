"""Put the subtitles into a video with ffmpeg: burned into the picture, or as a separate subtitle track."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .audio import NO_WINDOW, AudioError, _tool, duration
from .settings import Settings

# subtitles are converted to ASS at a 384x288 canvas; sizes below are in that unit
ASS_H = 288.0


class ExportError(RuntimeError):
    pass


def ass_color(hex_rgb: str) -> str:
    """'#RRGGBB' -> ASS '&H00BBGGRR&'."""
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        h = "ffffff"
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def force_style(s: Settings) -> str:
    return (f"FontName={s.burn_font},FontSize={s.sub_size_pct / 100 * ASS_H:.1f},PrimaryColour={ass_color(s.sub_color)},"
            f"OutlineColour={ass_color(s.sub_outline)},BorderStyle=1,Outline=2,Shadow=0,Alignment=2,"
            f"MarginV={s.sub_bottom_pct / 100 * ASS_H:.1f}")


def _run(cmd: list[str], total: float, progress: Callable[[float], None] | None, should_stop: Callable[[], bool] | None,
         cwd: str | None = None) -> None:
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", creationflags=NO_WINDOW)
    try:
        for line in p.stdout:  # -progress pipe:1 prints key=value lines
            if should_stop and should_stop():
                p.terminate()
                raise ExportError("หยุดโดยผู้ใช้")
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    done = int(line.split("=")[1]) / 1_000_000
                except ValueError:
                    continue
                if progress and total > 0:
                    progress(min(1.0, done / total))
        err = p.stderr.read()
        if p.wait() != 0:
            raise ExportError(f"ffmpeg ล้มเหลว:\n{err[-600:]}")
    finally:
        if p.poll() is None:
            p.kill()


def burn_in(video: str | Path, srt_file: str | Path, out: str | Path, s: Settings,
            progress: Callable[[float], None] | None = None, should_stop: Callable[[], bool] | None = None) -> Path:
    """Re-encode the video (H.264 + AAC) with the subtitles drawn into the picture."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = duration(video)
    with tempfile.TemporaryDirectory() as tmp:
        # the subtitles filter takes a path inside a filter string, where drive letters and quotes break it:
        # run in a temp folder and refer to a plain file name instead
        shutil.copyfile(srt_file, Path(tmp) / "sub.srt")
        vf = f"subtitles=sub.srt:force_style='{force_style(s)}'"
        cmd = [_tool("ffmpeg"), "-y", "-hide_banner", "-v", "error", "-nostats", "-progress", "pipe:1",
               "-i", str(Path(video).resolve()), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out.resolve())]
        _run(cmd, total, progress, should_stop, cwd=tmp)
    return out


def _subtitle_count(video: str | Path) -> int:
    r = subprocess.run([_tool("ffprobe"), "-v", "error", "-select_streams", "s", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(video)], capture_output=True, text=True, creationflags=NO_WINDOW)
    return len([x for x in r.stdout.split() if x.strip()])


def soft_sub(video: str | Path, srt_file: str | Path, out: str | Path, lang: str = "tha", title: str = "ไทย",
             progress: Callable[[float], None] | None = None, should_stop: Callable[[], bool] | None = None) -> Path:
    """Copy the streams (no re-encoding) and add the .srt as a subtitle track. .mkv keeps every original stream
    (other subtitle tracks and fonts too); .mp4 gets video, first audio and the new track as mov_text."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = duration(video)
    base = [_tool("ffmpeg"), "-y", "-hide_banner", "-v", "error", "-nostats", "-progress", "pipe:1",
            "-i", str(Path(video).resolve()), "-i", str(Path(srt_file).resolve())]
    if out.suffix.lower() == ".mkv":
        n = _subtitle_count(video)  # the new track comes after the existing ones
        cmd = base + ["-map", "0", "-map", "1:0", "-c", "copy", f"-metadata:s:s:{n}", f"language={lang}",
                      f"-metadata:s:s:{n}", f"title={title}", f"-disposition:s:{n}", "default"]
    elif out.suffix.lower() == ".mp4":
        cmd = base + ["-map", "0:v:0", "-map", "0:a:0?", "-map", "1:0", "-c:v", "copy", "-c:a", "copy",
                      "-c:s", "mov_text", "-metadata:s:s:0", f"language={lang}", "-metadata:s:s:0", f"title={title}"]
    else:
        raise ExportError("รองรับเฉพาะ .mkv และ .mp4")
    _run(cmd + [str(out.resolve())], total, progress, should_stop)
    return out
