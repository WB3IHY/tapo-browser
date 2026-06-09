#!/usr/bin/env sh
# Tapo Camera Manager launcher (macOS / Linux).
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "──────────────────────────────────────────────────────────────"
  echo " This app needs 'uv' (a small Python tool) to run."
  echo " Install it once with:"
  echo ""
  echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo ""
  echo " Then open a new terminal and run ./run.sh again."
  echo " More info: https://docs.astral.sh/uv/"
  echo "──────────────────────────────────────────────────────────────"
  exit 1
fi

# uv provisions Python 3.13 + dependencies automatically on first run.
exec uv run --python 3.13 python -m tapo_cli
