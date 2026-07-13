# Build context is expected to be a directory containing this Dockerfile
# alongside the project's pyproject.toml and src/ (see homeassistant-addon/README.md
# for how that layout gets assembled on the Home Assistant host — the add-on's
# own files alone are not a sufficient build context).
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

# Installed directly from pyproject.toml's [project.dependencies] rather than
# `pip install .` — a wheel install would relocate the package into
# site-packages, which breaks tapo_cli/paths.py's own relative path resolution.
RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "jinja2>=3.1" \
        "pytapo>=3.4,<4" \
        "aiofiles>=23.0" \
        "httpx>=0.27" \
        "pyyaml>=6.0"

COPY run.sh /run.sh
RUN chmod +x /run.sh

# Bound to 0.0.0.0 deliberately here (see config.py's comment: the app defaults
# to 127.0.0.1 by design because the account password is stored unencrypted) —
# inside a container, only 0.0.0.0 is reachable from outside it at all.
ENV PYTHONPATH=/app/src \
    TAPO_HOST=0.0.0.0 \
    TAPO_PORT=8077 \
    TAPO_OPEN_BROWSER=0

EXPOSE 8077

ENTRYPOINT ["/run.sh"]
