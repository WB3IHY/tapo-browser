# Tapo Camera Manager — Home Assistant add-on

This packages [tapo-browser](https://github.com/WB3IHY/tapo-browser) (my fork of
[machadolucas/tapo-browser](https://github.com/machadolucas/tapo-browser)) as a
Home Assistant Supervisor **local add-on**, so it runs as a normal
Supervisor-managed container on the same Pi as Home Assistant OS, instead of
as a manually-started script.

Nothing about the app itself changes — same FastAPI backend, same UI, same
features (live view + controls, on-demand SD-card recording downloads). This
is packaging only.

## Data persistence

Everything the app writes at runtime — the camera database (`tapo.db`),
downloaded recordings, and the `ffmpeg`/`go2rtc` helper binaries it fetches on
first run — normally lives in `data/` and `bin/` folders next to the app's
own code (see `src/tapo_cli/paths.py`). Inside a container, those folders are
`/app/data` and `/app/bin`, and **neither survives a container
restart or add-on update** — only Supervisor's persistent `/data` volume
does.

`run.sh` (the container entrypoint) handles this before the app starts:

1. Creates `/data/state/data` and `/data/state/bin` on the persistent volume.
2. Deletes whatever `/app/data` and `/app/bin` the image shipped with.
3. Symlinks `/app/data -> /data/state/data` and `/app/bin -> /data/state/bin`.

The app itself is unaware of any of this — it just sees `data/` and `bin/`
next to its code, as usual. `src/tapo_cli/` is not modified.

## Unencrypted password — read this before installing

This app stores your TP-Link cloud account password **unencrypted**, in
plain SQLite, by design — see the main repo's `CLAUDE.md` and README. That's
already true today when you run it manually.

What changes by running it as an add-on: the upstream tool binds to
`127.0.0.1` (reachable only from the machine it's running on). Inside this
container it's deliberately switched to `0.0.0.0` (`TAPO_HOST=0.0.0.0` in the
Dockerfile) because that's the only way anything outside the container can
reach it at all — Docker's port mapping doesn't work otherwise. The add-on's
`ports` mapping then exposes it at `<pi-ip>:8077`, so **anyone who can reach
your Pi on your LAN can reach this page**, not just the one computer it used
to be confined to. If your home network isn't fully trusted (guest Wi-Fi
sharing the same VLAN, IoT devices you don't trust, etc.), treat that as a
real, if small, increase in exposure — not just a restatement of the
upstream tool's existing risk.

## Manual install

Home Assistant Supervisor builds local add-ons from a folder under
`/addons/local/<slug>/` on the host — the Dockerfile's build context is
**exactly that folder**, not this whole repo. Since the Dockerfile `COPY`s
`pyproject.toml` and `src/` directly (see its top comment), those need to be
physically present alongside `config.yaml` in that folder — copying just this
`homeassistant-addon/` subfolder there isn't enough by itself.

`/addons/local/` only exists on the HAOS filesystem, not on the dev machine —
this repo lives on one machine and the Pi is another, so getting the merged
layout in place is a network copy (`scp`), not a local `cp`. From the dev
machine:

```sh
ssh root@HA_PI_HOST -p HA_SSH_PORT "mkdir -p /addons/local/tapo-browser"
scp -r -P HA_SSH_PORT \
  ~/development/tapo-browser/pyproject.toml \
  ~/development/tapo-browser/src \
  ~/development/tapo-browser/homeassistant-addon/* \
  root@HA_PI_HOST:/addons/local/tapo-browser/
```

Replace `HA_PI_HOST` and `HA_SSH_PORT` above with your Pi's actual LAN
address and SSH port before running this. They're left as placeholder tokens
here — not angle-bracket `<...>` placeholders, which some Markdown renderers
mistake for HTML tags and silently drop — and not hardcoded, since this file
is committed to the repo and this machine's pre-commit hooks block committing
home-network IPs/hosts.

That leaves `/addons/local/tapo-browser/` containing `config.yaml`,
`Dockerfile`, `run.sh`, this `README.md`, plus `pyproject.toml` and `src/` —
everything the build needs in one flat directory.

Then, in Home Assistant:

1. **Settings → Add-ons → Add-on Store → ⋮ → Check for updates** (or reload
   the page) to make Supervisor rescan `/addons/local/`.
2. The add-on appears under **Local add-ons** as "Tapo Camera Manager" —
   click it, then **Install**. First build pulls the `python:3.13-slim` base
   image and installs dependencies; expect it to take a few minutes.
3. **Start** it. On first start it also needs outbound internet once, to
   download the `go2rtc` binary (no system-package fallback exists for that
   one, unlike `ffmpeg`/`ffprobe` which are installed via `apt` in the
   Dockerfile and don't need to be downloaded).
4. Reach it at `http://<pi-ip>:8077`, or embed it in Lovelace with an
   `iframe` card pointed at that same URL — the same pattern as any other
   IP:port camera page. (Ingress is intentionally not used here — see below.)

## Why not ingress / MQTT / media_source

Out of scope for this pass — see the main task notes. `ingress: false` here
is deliberate: keeping this a plain port-mapped page avoids relative-path
issues in the app's static assets under HA's ingress proxy. Same pattern as
before: reach it directly by IP:port, optionally wrapped in an iframe card.

## Updating

To ship a new version: bump `version` in `config.yaml`, regenerate the
merged install directory as above, then **Rebuild** the add-on from its page
in Home Assistant. `/data/state/{data,bin}` is untouched by this — your
cameras, downloaded recordings, and fetched binaries persist across rebuilds
and updates.
