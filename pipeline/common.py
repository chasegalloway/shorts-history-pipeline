"""Shared config loading, paths, and logging for the pipeline."""
import logging
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "pipeline.log", encoding="utf-8"),
    ],
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Resolve ffmpeg: configured path first, then PATH.
    for key, exe in (("ffmpeg", "ffmpeg"), ("ffprobe", "ffprobe")):
        p = cfg["paths"].get(key)
        if not p or not Path(p).exists():
            found = shutil.which(exe)
            if not found:
                raise FileNotFoundError(f"{exe} not found (config paths.{key} or PATH)")
            cfg["paths"][key] = found
    return cfg


def out_dir(cfg: dict, video_id: str) -> Path:
    d = ROOT / cfg["paths"]["output"] / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d
