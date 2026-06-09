# Tapo Camera Manager

A small local web app to manage TP-Link **Tapo** cameras on your network. From your own
computer you can:

- **Add multiple cameras** (stored in a local database).
- **Watch the live stream** of a camera in your browser.
- **Browse and download** the video recordings stored on each camera's microSD card, saved
  as normal `.mp4` files.

It runs entirely on your own machine — nothing is sent to the cloud, and the web page is
only reachable from the same computer (`http://127.0.0.1:8077`).

---

## 1. Install `uv` (one time)

This app uses [`uv`](https://docs.astral.sh/uv/), a tiny tool that automatically sets up
Python and everything else. Install it once:

- **Windows** — open *PowerShell* and paste:
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
- **macOS / Linux** — open a *Terminal* and paste:
  ```sh
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

Close and reopen the terminal afterwards so `uv` is found.

## 2. Run the app

- **Windows:** double-click **`run.bat`**.
- **macOS / Linux:** run **`./run.sh`**.

The **first launch takes a minute** (it downloads Python, the dependencies, and the
`ffmpeg` + `go2rtc` helper programs into a local `bin/` folder), then opens your browser at
**http://127.0.0.1:8077**. After that it starts quickly.

> Needs internet on the first run only (to download those pieces). After that it works on
> your local network.

---

## 3. Add a camera

Click **“+ Add camera”** and fill in:

| Field | What to enter |
|---|---|
| **Name** | Anything, e.g. “Front Door”. |
| **Host / IP address** | The camera's address on your network, e.g. `192.168.1.50`. |
| **TP-Link account password** | The password for your **Tapo / TP-Link app account** (the one you log into the app with). |
| **Control port** | Leave blank (defaults to 443). |

You do **not** enter a username — modern Tapo cameras use the fixed local user `admin`
together with your TP-Link account password for everything this app does (live view,
recordings, downloads).

Click **“Test connection”** to confirm — it shows the camera model, firmware, and SD-card
status when it connects.

---

## 4. Use it

- **Live view** — click a camera's thumbnail or **▶ Live**. The stream plays in the page,
  with a **camera controls** panel below it, grouped by category: image & display (night
  vision, anti-flicker, flip, HDR, distortion), light & LED, audio (speaker/mic volume),
  detection (motion, person, vehicle, pet, sound detections, line-crossing, package, PIR),
  movement (pan/tilt + auto-track on PTZ models), alerts (alarm, siren), privacy, and a
  reboot. **Only the controls your specific camera supports are shown** — each with a
  hover ⓘ explaining what it does.
- **Recordings** — click **Recordings**, choose a date range, click **Find days with
  footage**, pick a day, then download any segment. Progress is shown live; finished files
  are saved under `data/downloads/<camera>/<date>/` and via the **Save file** button.

---

## Troubleshooting

- **“Login failed”** — wrong TP-Link account password. Use the password for your Tapo app
  login (username is always `admin`, handled automatically).
- **“The camera temporarily blocked logins…”** — Tapo locks out logins after several wrong
  attempts. Wait the stated time, or **reboot the camera** to clear it immediately.
- **No recordings / “no SD card”** — the camera needs a working microSD card inserted and
  recording enabled in the Tapo app. Recordings can only be downloaded if footage exists.
- **Live video doesn't start (you see the hint box)** — on **macOS**, allow the
  *Local Network* access prompt the first time you run it (or enable it under System
  Settings → Privacy & Security → Local Network), then reopen Live and give it a few
  seconds. If a particular camera's firmware still won't stream, enabling **RTSP** in the
  Tapo app is the fallback — see *Known limitations*. Control, recordings and downloads work
  regardless.
- **Port already in use** — start on another port: `TAPO_PORT=9000 ./run.sh`.

## Known limitations

- **Live view** streams via go2rtc's native `tapo://` source and is verified working at
  full 2K on a Tapo **C520WS** (firmware 1.3.4). On macOS the helper needs *Local Network*
  permission (granted on first prompt). On some firmware go2rtc reconnects the source
  periodically (a brief hiccup), but playback continues. If a specific camera won't stream
  this way at all, enabling **RTSP** in the Tapo app is the fallback (an explicit RTSP
  source could be added as an option).

## Notes

- The web UI is bound to `127.0.0.1` (this computer only); the TP-Link account password is
  stored **unencrypted** in `data/tapo.db`. Run it on a machine you trust.
- Tested with a Tapo **C520WS**. Other Tapo models that support the local API and SD
  recording should work for management/recordings too.

## Configuration (optional)

| Variable | Default | Meaning |
|---|---|---|
| `TAPO_PORT` | `8077` | Web UI port. |
| `TAPO_GO2RTC_PORT` | `1984` | Internal streaming port (auto-bumps if taken). |
| `TAPO_MAX_DOWNLOADS` | `2` | Max simultaneous recording downloads. |
| `TAPO_OPEN_BROWSER` | `1` | Set to `0` to not auto-open the browser. |

## How it works

FastAPI backend + a plain-HTML/JS frontend. [`pytapo`](https://github.com/JurajNyiri/pytapo)
talks to the cameras (control, recordings list, downloads) as `admin` + the account
password. [`go2rtc`](https://github.com/AlexxIT/go2rtc) bridges each camera's stream into a
browser-playable WebRTC/HLS stream. `ffmpeg` converts downloaded recordings to `.mp4`.
Camera list and download history live in a local SQLite database. Inspired by the excellent
[HomeAssistant-Tapo-Control](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control).
