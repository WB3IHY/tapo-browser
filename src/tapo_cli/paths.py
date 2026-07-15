"""Filesystem layout + per-OS binary path resolution.

All runtime state lives next to the project root (the parent of the ``src``
directory) so the whole app is self-contained and portable: unzip the folder,
run it, and ``bin/`` + ``data/`` are created alongside the code.
"""

from __future__ import annotations

import platform
from pathlib import Path

# project_root/
#   src/tapo_cli/paths.py   <- this file
#   bin/   data/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = PROJECT_ROOT / "bin"
DATA_DIR = PROJECT_ROOT / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
PLAYBACK_DIR = DATA_DIR / "playback"

DB_PATH = DATA_DIR / "tapo.db"

IS_WINDOWS = platform.system().lower().startswith("win")


def ensure_dirs() -> None:
    """Create the runtime directories if they don't yet exist."""
    for d in (BIN_DIR, DATA_DIR, DOWNLOADS_DIR, THUMBNAILS_DIR, PLAYBACK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def exe_name(stem: str) -> str:
    """Return the platform-specific executable filename for ``stem``."""
    return f"{stem}.exe" if IS_WINDOWS else stem


def bin_path(stem: str) -> Path:
    """Absolute path to a bundled binary in ``bin/`` (adds .exe on Windows)."""
    return BIN_DIR / exe_name(stem)


def ffmpeg_path() -> Path:
    return bin_path("ffmpeg")


def ffprobe_path() -> Path:
    return bin_path("ffprobe")
