"""Compose the final 9:16 short with ffmpeg:
stills -> Ken Burns motion -> film grain + vignette -> burned captions,
voiceover + ambient music bed with sidechain ducking.
"""
import math
import random
import subprocess
from pathlib import Path

from .common import ROOT, get_logger

log = get_logger("assemble")


def probe_duration(path: Path, cfg: dict) -> float:
    out = subprocess.run(
        [cfg["paths"]["ffprobe"], "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _kb_preset(i: int, frames: int) -> str:
    """Rotate Ken Burns motion so consecutive shots feel different."""
    presets = [
        # slow zoom in, centered
        "z='min(1+0.0011*on,1.28)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        # slow zoom out
        "z='max(1.28-0.0011*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        # pan right at slight zoom
        f"z='1.14':x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)'",
        # pan down at slight zoom
        f"z='1.14':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*on/{frames}'",
    ]
    return presets[i % len(presets)]


def _filter_path(p: Path) -> str:
    # Windows path escaping for ffmpeg filter arguments
    return str(p).replace("\\", "/").replace(":", "\\:")


def ensure_music(cfg: dict) -> Path:
    """Pick a track from assets/music; generate ambient drone beds if empty."""
    mdir = ROOT / cfg["paths"]["music"]
    mdir.mkdir(parents=True, exist_ok=True)
    tracks = [p for p in mdir.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")]
    if tracks:
        return random.choice(tracks)
    log.info("No music tracks found — generating ambient drone beds...")
    variants = [(55.0, 55.6, 220), (49.0, 49.5, 196), (61.7, 62.2, 247)]
    for i, (f1, f2, f3) in enumerate(variants):
        out = mdir / f"generated_drone_{i}.wav"
        fc = (
            f"[0][1]amix=inputs=2,lowpass=f=320,tremolo=f=0.11:d=0.55[dr];"
            f"[2]lowpass=f=240,volume=0.5[ns];"
            f"[3]lowpass=f=900,volume=0.10,tremolo=f=0.13:d=0.7[hi];"
            f"[dr][ns][hi]amix=inputs=3,volume=1.4,afade=t=in:d=3[out]"
        )
        subprocess.run(
            [cfg["paths"]["ffmpeg"], "-y",
             "-f", "lavfi", "-i", f"sine=frequency={f1}:duration=120",
             "-f", "lavfi", "-i", f"sine=frequency={f2}:duration=120",
             "-f", "lavfi", "-i", "anoisesrc=color=brown:duration=120:amplitude=0.06:seed=%d" % (42 + i),
             "-f", "lavfi", "-i", f"sine=frequency={f3}:duration=120",
             "-filter_complex", fc, "-map", "[out]", "-ar", "48000", str(out)],
            check=True, capture_output=True,
        )
    tracks = sorted(mdir.glob("generated_drone_*.wav"))
    return random.choice(tracks)


def assemble(images: list[Path], voice_wav: Path, ass_file: Path, out_mp4: Path, cfg: dict) -> float:
    v = cfg["video"]
    fps = v["fps"]
    voice_dur = probe_duration(voice_wav, cfg)
    # never cut narration; shorts may run past target length (limit is 3 min)
    total = voice_dur + 1.2
    if total > v["max_seconds"]:
        log.warning("Narration runs %.1fs — over the %ds target", total, v["max_seconds"])
    n = len(images)
    per = total / n
    frames = math.ceil(per * fps)
    music = ensure_music(cfg)
    log.info("Assembling: %d images, voice %.1fs, total %.1fs, music=%s",
             n, voice_dur, total, music.name)

    cmd = [cfg["paths"]["ffmpeg"], "-y"]
    for img in images:
        # single-frame input: zoompan expands it to `frames` output frames
        cmd += ["-i", str(img)]
    cmd += ["-i", str(voice_wav)]
    cmd += ["-stream_loop", "-1", "-i", str(music)]
    vi, mi = n, n + 1

    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale=1512:2688:force_original_aspect_ratio=increase,"
            f"crop=1512:2688,zoompan={_kb_preset(i, frames)}:d={frames}:"
            f"s=1080x1920:fps={fps},setsar=1[v{i}]"
        )
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vcat]")
    parts.append(
        f"[vcat]noise=alls=4:allf=t,vignette=angle=PI/4.5,"
        f"ass=filename='{_filter_path(ass_file)}',format=yuv420p[vout]"
    )
    parts.append(
        f"[{vi}:a]aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"apad=pad_dur=1.2,asplit=2[vm][vk]"
    )
    fade_start = max(total - 2.0, 0)
    parts.append(
        f"[{mi}:a]aresample=48000,atrim=0:{total:.3f},volume={v['music_volume']},"
        f"afade=t=out:st={fade_start:.3f}:d=2[mu]"
    )
    parts.append("[mu][vk]sidechaincompress=threshold=0.02:ratio=10:attack=25:release=400[mud]")
    parts.append("[vm][mud]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.97[aout]")

    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{total:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr[-2000:]}")
    log.info("Rendered %s (%.1fs)", out_mp4.name, total)
    return total
