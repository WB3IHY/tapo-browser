// Direct playback: start a camera-side streaming session and play its
// growing HLS output via hls.js. Opening a session has a real, sometimes
// ~20s cold-start latency (see CLAUDE.md) — the loading state exists
// specifically for that. The server holds the manifest request open and
// waits for the first real HLS segment rather than returning early, so no
// special client-side retry configuration is needed here.
import { api } from "./api.js";

const playerEl = document.getElementById("player");
const videoEl = document.getElementById("player-video");
const statusEl = document.getElementById("player-status");
const closeBtn = document.getElementById("player-close");

let hls = null;
let currentSessionId = null;

async function stopCurrent() {
  if (hls) {
    hls.destroy();
    hls = null;
  }
  videoEl.pause();
  videoEl.removeAttribute("src");
  videoEl.load();
  if (currentSessionId) {
    const id = currentSessionId;
    currentSessionId = null;
    try {
      await api(`/api/playback/${id}`, { method: "DELETE" });
    } catch {
      // best-effort cleanup
    }
  }
}

export async function playSegment(cam, startTime, endTime) {
  await stopCurrent();
  playerEl.hidden = false;
  statusEl.hidden = false;
  statusEl.textContent = "Starting playback… this can take up to ~20s.";
  videoEl.addEventListener("playing", () => { statusEl.hidden = true; }, { once: true });
  videoEl.addEventListener("ended", () => {
    statusEl.hidden = false;
    statusEl.textContent = "Finished.";
  }, { once: true });

  let session;
  try {
    session = await api(`/api/cameras/${cam.id}/playback`, {
      method: "POST",
      body: JSON.stringify({ start_time: startTime, end_time: endTime }),
    });
  } catch (err) {
    statusEl.textContent = `Could not start playback: ${err.message}`;
    return;
  }
  currentSessionId = session.session_id;

  if (window.Hls && window.Hls.isSupported()) {
    hls = new window.Hls({
      // The server's manifest response can legitimately take up to ~30s
      // (it waits for the real file rather than erroring), so hls.js must
      // not give up on the request before that.
      manifestLoadPolicy: {
        default: {
          maxTimeToFirstByteMs: 35000,
          maxLoadTimeMs: 35000,
          timeoutRetry: { maxNumRetry: 2, retryDelayMs: 1000, maxRetryDelayMs: 2000 },
          errorRetry: { maxNumRetry: 3, retryDelayMs: 2000, maxRetryDelayMs: 4000 },
        },
      },
      // Separate from manifestLoadPolicy (which only covers the *first*
      // load): this governs the periodic reloads hls.js does throughout
      // playback to discover new segments as the playlist grows. Left at
      // its modest default, a single transient hiccup during one of those
      // reloads could make hls.js quietly stop polling and playback just
      // run out of buffered content early, with no dramatic error — a
      // plausible explanation for a clip stopping well short of its real
      // length.
      playlistLoadPolicy: {
        default: {
          maxTimeToFirstByteMs: 20000,
          maxLoadTimeMs: 20000,
          timeoutRetry: { maxNumRetry: 4, retryDelayMs: 1000, maxRetryDelayMs: 4000 },
          errorRetry: { maxNumRetry: 6, retryDelayMs: 1000, maxRetryDelayMs: 4000 },
        },
      },
    });
    hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
      videoEl.play().catch(() => {});
    });
    hls.on(window.Hls.Events.ERROR, (_evt, data) => {
      if (!data.fatal) return;
      const message = `Playback error: ${data.details}`;
      // A fatal error leaves the session running server-side (and its
      // camera connection open) unless we explicitly tear it down here —
      // don't just display the error and leave it dangling.
      stopCurrent().then(() => {
        playerEl.hidden = false;
        statusEl.hidden = false;
        statusEl.textContent = message;
      });
    });
    hls.loadSource(session.playlist_url);
    hls.attachMedia(videoEl);
  } else if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
    videoEl.src = session.playlist_url;
    videoEl.addEventListener("loadedmetadata", () => videoEl.play().catch(() => {}), { once: true });
  } else {
    statusEl.textContent = "This browser can't play HLS video.";
    await stopCurrent();
  }
}

export async function closePlayer() {
  await stopCurrent();
  playerEl.hidden = true;
}

closeBtn.addEventListener("click", closePlayer);
