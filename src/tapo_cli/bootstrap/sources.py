"""Pinned download sources for the helper binaries (ffmpeg + go2rtc).

Keyed by (os, arch). ``os`` ∈ {windows, linux, darwin}; ``arch`` ∈ {amd64, arm64}.

``kind`` tells the extractor what the downloaded file is:
  - "raw"    : the file itself is the executable
  - "zip"    : a .zip archive containing the executable somewhere inside
  - "tar.xz" : a .tar.xz archive containing the executable somewhere inside

Notes / known compromises:
  - macOS ffmpeg (evermeet) ships an x86_64 build; on Apple Silicon it runs under
    Rosetta 2. The friend's machines are Windows/Linux, so this only affects local dev.
  - Windows-on-ARM has no native ffmpeg build here; it falls back to the x64 build,
    which Windows can run under emulation.
  - gyan.dev / evermeet URLs float to the latest release, so we don't pin checksums;
    every downloaded binary is sanity-checked by executing it with ``-version``.
"""

from __future__ import annotations

GO2RTC_VERSION = "v1.9.14"
_GO2RTC_BASE = f"https://github.com/AlexxIT/go2rtc/releases/download/{GO2RTC_VERSION}"

GO2RTC_SOURCES: dict[tuple[str, str], tuple[str, str]] = {
    ("windows", "amd64"): (f"{_GO2RTC_BASE}/go2rtc_win64.zip", "zip"),
    ("windows", "arm64"): (f"{_GO2RTC_BASE}/go2rtc_win_arm64.zip", "zip"),
    ("linux", "amd64"): (f"{_GO2RTC_BASE}/go2rtc_linux_amd64", "raw"),
    ("linux", "arm64"): (f"{_GO2RTC_BASE}/go2rtc_linux_arm64", "raw"),
    ("darwin", "amd64"): (f"{_GO2RTC_BASE}/go2rtc_mac_amd64.zip", "zip"),
    ("darwin", "arm64"): (f"{_GO2RTC_BASE}/go2rtc_mac_arm64.zip", "zip"),
}

_GYAN = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
_JVS = "https://johnvansickle.com/ffmpeg/releases"
_EVERMEET_FFMPEG = "https://evermeet.cx/ffmpeg/getrelease/zip"
_EVERMEET_FFPROBE = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"

# ffmpeg AND ffprobe ship in the same Windows/Linux archive (downloaded once and
# cached); macOS serves them as two separate evermeet downloads.
FFMPEG_SOURCES: dict[tuple[str, str], tuple[str, str]] = {
    ("windows", "amd64"): (_GYAN, "zip"),
    ("windows", "arm64"): (_GYAN, "zip"),  # x64 build under Windows emulation
    ("linux", "amd64"): (f"{_JVS}/ffmpeg-release-amd64-static.tar.xz", "tar.xz"),
    ("linux", "arm64"): (f"{_JVS}/ffmpeg-release-arm64-static.tar.xz", "tar.xz"),
    ("darwin", "amd64"): (_EVERMEET_FFMPEG, "zip"),
    ("darwin", "arm64"): (_EVERMEET_FFMPEG, "zip"),  # x86_64 build under Rosetta
}

FFPROBE_SOURCES: dict[tuple[str, str], tuple[str, str]] = {
    ("windows", "amd64"): (_GYAN, "zip"),
    ("windows", "arm64"): (_GYAN, "zip"),
    ("linux", "amd64"): (f"{_JVS}/ffmpeg-release-amd64-static.tar.xz", "tar.xz"),
    ("linux", "arm64"): (f"{_JVS}/ffmpeg-release-arm64-static.tar.xz", "tar.xz"),
    ("darwin", "amd64"): (_EVERMEET_FFPROBE, "zip"),
    ("darwin", "arm64"): (_EVERMEET_FFPROBE, "zip"),
}
