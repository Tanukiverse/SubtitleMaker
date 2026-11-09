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


ASS_W = 384.0


def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    sec, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def build_ass(cues: list[tuple], s: Settings) -> str:
    """cues: [(start, end, text, pos)]. One style from the settings; every line gets an explicit position
    (\\pos) so the sideways shift and the top / middle / bottom choice per cue survive."""
    x = ASS_W / 2 + s.sub_x_pct / 100 * ASS_W
    margin = s.sub_bottom_pct / 100 * ASS_H
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 384\nPlayResY: 288\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{s.burn_font},{s.sub_size_pct / 100 * ASS_H:.1f},{ass_color(s.sub_color).rstrip('&')},&H000000FF,"
        f"{ass_color(s.sub_outline).rstrip('&')},&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,{margin:.1f},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for cue in cues:
        start, end, text = cue[:3]
        pos = cue[3] if len(cue) > 3 else ""
        text = text.strip()
        if not text:
            continue
        if pos == "top":
            tag = f"{{\\an8\\pos({x:.1f},{margin:.1f})}}"
        elif pos == "middle":
            tag = f"{{\\an5\\pos({x:.1f},{ASS_H / 2:.1f})}}"
        else:
            tag = f"{{\\an2\\pos({x:.1f},{ASS_H - margin:.1f})}}"
        body = text.replace("{", "(").replace("}", ")").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{tag}{body}")
    return head + "\n".join(lines) + "\n"


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


def burn_in(video: str | Path, cues: list[tuple], out: str | Path, s: Settings,
            progress: Callable[[float], None] | None = None, should_stop: Callable[[], bool] | None = None) -> Path:
    """Re-encode the video (H.264 + AAC) with the subtitles drawn into the picture.
    cues: [(start, end, text, pos)]."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = duration(video)
    with tempfile.TemporaryDirectory() as tmp:
        # the subtitles filter takes a path inside a filter string, where drive letters and quotes break it:
        # run in a temp folder and refer to a plain file name instead
        (Path(tmp) / "sub.ass").write_text(build_ass(cues, s), encoding="utf-8")
        cmd = [_tool("ffmpeg"), "-y", "-hide_banner", "-v", "error", "-nostats", "-progress", "pipe:1",
               "-i", str(Path(video).resolve()), "-map", "0:v:0", "-map", "0:a:0?", "-vf", "subtitles=sub.ass",
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
