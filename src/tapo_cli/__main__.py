"""Entry point: `python -m tapo_cli`.

Prepends the bundled ``bin/`` to PATH (so pytapo and go2rtc find ffmpeg),
optionally opens the browser, then runs the uvicorn server bound to localhost.
"""

from __future__ import annotations

import logging
import os
import threading
import webbrowser

import uvicorn

from . import paths
from .config import Settings


def _prepend_bin_to_path() -> None:
    bin_dir = str(paths.BIN_DIR)
    current = os.environ.get("PATH", "")
    if bin_dir not in current.split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + current


def _open_browser_later(url: str, delay: float = 1.5) -> None:
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = Settings.load()
    paths.ensure_dirs()
    _prepend_bin_to_path()

    if settings.open_browser:
        _open_browser_later(settings.base_url)

    print(f"\n  Tapo Camera Manager  ->  {settings.base_url}\n")
    uvicorn.run(
        "tapo_cli.app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
