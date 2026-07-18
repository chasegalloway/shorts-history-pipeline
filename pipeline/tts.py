"""Voiceover synthesis: Kokoro-82M locally, edge-tts as fallback."""
import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf

from .common import get_logger

log = get_logger("tts")

_kokoro_pipeline = None


def synthesize(text: str, out_wav: Path, cfg: dict) -> Path:
    engine = cfg["tts"]["engine"]
    if engine == "kokoro":
        try:
            return _kokoro(text, out_wav, cfg)
        except Exception as e:  # model download failure, unsupported hw, etc.
            log.warning("kokoro failed (%s), falling back to edge-tts", e)
    return _edge(text, out_wav, cfg)


def _kokoro(text: str, out_wav: Path, cfg: dict) -> Path:
    global _kokoro_pipeline
    from kokoro import KPipeline

    if _kokoro_pipeline is None:
        log.info("Loading Kokoro-82M (first run downloads ~330MB)...")
        _kokoro_pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    chunks = []
    for _, _, audio in _kokoro_pipeline(
        text, voice=cfg["tts"]["voice"], speed=cfg["tts"]["speed"]
    ):
        chunks.append(audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio))
    if not chunks:
        raise RuntimeError("kokoro produced no audio")
    # short breath pause between generated segments
    pause = np.zeros(int(24000 * 0.25), dtype=np.float32)
    audio = np.concatenate([np.concatenate([c, pause]) for c in chunks])[: -len(pause)]
    sf.write(out_wav, audio, 24000)
    log.info("Kokoro voiceover: %.1fs -> %s", len(audio) / 24000, out_wav.name)
    return out_wav


def _edge(text: str, out_wav: Path, cfg: dict) -> Path:
    import edge_tts

    mp3 = out_wav.with_suffix(".mp3")

    async def run():
        await edge_tts.Communicate(text, cfg["tts"]["edge_voice"]).save(str(mp3))

    asyncio.run(run())
    # normalize to wav so downstream (whisper/ffmpeg) sees one format
    import subprocess

    from .common import load_config

    ff = load_config()["paths"]["ffmpeg"]
    subprocess.run([ff, "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(out_wav)],
                   check=True, capture_output=True)
    mp3.unlink(missing_ok=True)
    log.info("edge-tts voiceover -> %s", out_wav.name)
    return out_wav
