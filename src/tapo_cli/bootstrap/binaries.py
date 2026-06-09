"""Download + unpack the ffmpeg and go2rtc binaries into ``bin/`` on first run.

Idempotent: if a working binary is already present, it's left alone. The download
itself runs synchronously (in a worker thread when called from async code) because
it's a one-time first-launch cost and keeps the logic simple.
"""

from __future__ import annotations

import io
import logging
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import httpx

from .. import paths
from .sources import FFMPEG_SOURCES, FFPROBE_SOURCES, GO2RTC_SOURCES

log = logging.getLogger("tapo_cli.bootstrap")

# Transient per-startup cache so ffmpeg + ffprobe (same Windows/Linux archive)
# are downloaded only once. Cleared at the end of ensure_binaries().
_download_cache: dict[str, bytes] = {}


def platform_key() -> tuple[str, str]:
    """Return (os, arch) where os ∈ {windows,linux,darwin}, arch ∈ {amd64,arm64}."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system.startswith("win"):
        os_key = "windows"
    elif system == "darwin":
        os_key = "darwin"
    else:
        os_key = "linux"
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64", "x64"):
        arch = "amd64"
    else:
        arch = machine
    return os_key, arch


# --------------------------------------------------------------------------- #
# Download + extraction
# --------------------------------------------------------------------------- #
def _download_bytes(url: str, use_cache: bool = False) -> bytes:
    if use_cache and url in _download_cache:
        return _download_cache[url]
    log.info("Downloading %s", url)
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, read=300.0)) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.content
    if use_cache:
        _download_cache[url] = data
    return data


def _member_matches(name: str, stem: str) -> int:
    """Score an archive member as a candidate for the binary ``stem``.

    Higher is better; 0 means not a match. Lets us pick ``ffmpeg.exe`` over
    ``ffprobe.exe`` and a bare ``go2rtc`` over unrelated files.
    """
    base = Path(name).name.lower()
    if base in (stem, f"{stem}.exe"):
        return 3
    if Path(base).stem == stem:  # e.g. go2rtc_win64 -> stem 'go2rtc' won't match; exact only
        return 2
    if base.startswith(stem):  # e.g. go2rtc_win64.exe
        return 1
    return 0


def _extract_from_zip(data: bytes, stem: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        best, best_score = None, 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            score = _member_matches(info.filename, stem)
            if score > best_score:
                best, best_score = info, score
        if best is None:
            raise RuntimeError(f"No '{stem}' executable found in zip archive")
        return zf.read(best)


def _extract_from_tar(data: bytes, stem: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
        best, best_score = None, 0
        for member in tf.getmembers():
            if not member.isfile():
                continue
            score = _member_matches(member.name, stem)
            if score > best_score:
                best, best_score = member, score
        if best is None:
            raise RuntimeError(f"No '{stem}' executable found in tar archive")
        extracted = tf.extractfile(best)
        if extracted is None:
            raise RuntimeError(f"Could not read '{stem}' from tar archive")
        return extracted.read()


def _make_executable(path: Path) -> None:
    if not paths.IS_WINDOWS:
        st = path.stat()
        path.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _strip_macos_quarantine(path: Path) -> None:
    """macOS Gatekeeper tags downloaded files; strip it so the binary can run."""
    if platform.system().lower() != "darwin":
        return
    try:
        subprocess.run(
            ["xattr", "-d", "com.apple.quarantine", str(path)],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass


def _binary_works(path: Path, version_flag: str = "-version") -> bool:
    if not path.exists():
        return False
    try:
        result = subprocess.run(
            [str(path), version_flag],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001 — any failure means "not usable"
        return False


def _system_binary(stem: str, version_flag: str) -> Path | None:
    """Return a working copy of ``stem`` already on the system PATH, if any."""
    found = shutil.which(stem)
    if found:
        p = Path(found).resolve()
        if _binary_works(p, version_flag):
            return p
    return None


def _link_or_copy(src: Path, dest: Path) -> None:
    """Point ``dest`` (inside bin/) at an existing binary ``src``.

    Symlink on POSIX (cheap); copy on Windows where symlinks need privileges.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    if paths.IS_WINDOWS:
        shutil.copy2(src, dest)
    else:
        try:
            dest.symlink_to(src)
        except OSError:
            shutil.copy2(src, dest)
            _make_executable(dest)


def _install_binary(stem: str, url: str, kind: str, dest: Path, use_cache: bool = False) -> None:
    data = _download_bytes(url, use_cache=use_cache)
    if kind == "raw":
        payload = data
    elif kind == "zip":
        payload = _extract_from_zip(data, stem)
    elif kind == "tar.xz":
        payload = _extract_from_tar(data, stem)
    else:
        raise ValueError(f"Unknown archive kind: {kind}")

    # Write atomically: temp file in the same dir, then replace.
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{stem}-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        _make_executable(tmp)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    _strip_macos_quarantine(dest)


def _ensure(
    stem: str,
    sources: dict[tuple[str, str], tuple[str, str]],
    version_flag: str,
    prefer_system: bool = False,
    use_cache: bool = False,
) -> Path:
    dest = paths.bin_path(stem)
    if _binary_works(dest, version_flag):
        log.info("%s already present at %s", stem, dest)
        return dest

    # ffmpeg/ffprobe are commonly installed system-wide and large; reuse when present.
    if prefer_system:
        system = _system_binary(stem, version_flag)
        if system is not None:
            log.info("Using system %s at %s", stem, system)
            _link_or_copy(system, dest)
            return dest

    key = platform_key()
    if key in sources:
        url, kind = sources[key]
        try:
            _install_binary(stem, url, kind, dest, use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001 — fall through to system fallback
            log.warning("Download of %s failed: %s", stem, exc)
        else:
            if _binary_works(dest, version_flag):
                log.info("Installed %s -> %s", stem, dest)
                return dest
            log.warning("Downloaded %s but it failed to run (arch mismatch?)", stem)

    # Last resort: any working binary already on the system PATH.
    system = _system_binary(stem, version_flag)
    if system is not None:
        log.info("Falling back to system %s at %s", stem, system)
        _link_or_copy(system, dest)
        return dest

    raise RuntimeError(
        f"Could not provision a working '{stem}' for platform {key}. "
        f"Install {stem} and either add it to your PATH or place it in {paths.BIN_DIR}."
    )


def ensure_ffmpeg() -> Path:
    return _ensure("ffmpeg", FFMPEG_SOURCES, "-version", prefer_system=True, use_cache=True)


def ensure_ffprobe() -> Path:
    return _ensure("ffprobe", FFPROBE_SOURCES, "-version", prefer_system=True, use_cache=True)


def ensure_go2rtc() -> Path:
    return _ensure("go2rtc", GO2RTC_SOURCES, "--version")


def ensure_binaries() -> dict[str, Path]:
    """Ensure all helper binaries exist; return their resolved paths.

    ffmpeg and ffprobe come from the same Windows/Linux archive, so the cached
    download is reused across the two.
    """
    paths.ensure_dirs()
    try:
        return {
            "ffmpeg": ensure_ffmpeg(),
            "ffprobe": ensure_ffprobe(),
            "go2rtc": ensure_go2rtc(),
        }
    finally:
        _download_cache.clear()
