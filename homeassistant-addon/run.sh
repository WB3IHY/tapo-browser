#!/bin/sh
# Container entrypoint: redirect the app's data/bin folders onto Supervisor's
# persistent /data volume, then start the app.
#
# tapo_cli/paths.py always computes data/ and bin/ as siblings of src/ (i.e.
# /app/data and /app/bin here). Neither survives an add-on restart or update —
# only /data does — so we replace those two paths with symlinks into
# /data/state before python ever imports paths.py.
set -e

STATE_DIR=/data/state
mkdir -p "$STATE_DIR/data" "$STATE_DIR/bin"

rm -rf /app/data /app/bin
ln -s "$STATE_DIR/data" /app/data
ln -s "$STATE_DIR/bin" /app/bin

cd /app
exec python -m tapo_cli
