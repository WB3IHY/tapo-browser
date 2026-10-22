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
let statusTimer = null;
let timeHandler = null;
let sessionListeners = null;
let watchdog = null;

// How long a session may go without delivering video before we call it failed.
// From experience a session that hasn't started playing within 10-15s never
// will, while one that has started always recovers from brief spinners (a few
// seconds, e.g. ~5s in). So startup is strict and mid-playback stalls lenient.
const STARTUP_TIMEOUT_MS = 15000;
const STALL_TIMEOUT_MS = 30000;
const WATCHDOG_TICK_MS = 2000;

function stopStatusPolling() {
  if (statusTimer) {
    clearInterval(statusTimer);
    statusTimer = null;
  }
}

// The camera can silently stop sending data mid-session (goes quiet, no
// error, no fatal hls.js event - see CLAUDE.md/playback.py) - hls.js just
// treats the playlist as a live stream that hasn't grown yet and waits
// forever. Poll the session's own status so a stalled/errored session shows
// a real message instead of an indefinite spinner.
function startStatusPolling(sessionId) {
  stopStatusPolling();
  statusTimer = setInterval(async () => {
    let status;
    try {
      status = await api(`/api/playback/${sessionId}/status`);
    } catch {
      return; // session gone or transient — next tick will catch it
    }
    if (status.error) {
      stopStatusPolling();
      stopCurrent().then(() => {
        playerEl.hidden = false;
        statusEl.hidden = false;
        statusEl.textContent = status.error;
      });
    }
  }, 3000);
}

function stopWatchdog() {
  if (watchdog) {
    clearInterval(watchdog);
    watchdog = null;
  }
}

// Detect the failure the status poll can't see: the session is "running" but
// no video ever arrives (camera busy/silent), or arrives and then stops. Tear
// the session down and say so, rather than leaving a spinner forever.
function startWatchdog() {
  stopWatchdog();
  const startedAt = Date.now();
  let everPlayed = false;
  let lastTime = -1;
  let lastProgressAt = startedAt;
  const fail = (message) => {
    stopCurrent().then(() => {
      playerEl.hidden = false;
      statusEl.hidden = false;
      statusEl.textContent = message;
    });
  };
  watchdog = setInterval(() => {
    const now = Date.now();
    if (videoEl.ended) return;
    if (!everPlayed) {
      if (videoEl.currentTime > 0 && !videoEl.paused) everPlayed = true;
      else if (now - startedAt > STARTUP_TIMEOUT_MS) {
        fail("The camera didn't start sending video within " + STARTUP_TIMEOUT_MS / 1000 +
             " seconds. It may be busy — wait a minute, then press Play again.");
      }
      return;
    }
    if (videoEl.paused || videoEl.currentTime !== lastTime) {
      lastTime = videoEl.currentTime;
      lastProgressAt = now;
    } else if (now - lastProgressAt > STALL_TIMEOUT_MS) {
      fail("Playback stalled — the camera stopped sending video. Press Play to try again.");
    }
  }, WATCHDOG_TICK_MS);
}

async function stopCurrent() {
  stopStatusPolling();
  stopWatchdog();
  if (timeHandler) {
    videoEl.removeEventListener("timeupdate", timeHandler);
    timeHandler = null;
  }
  if (sessionListeners) {
    for (const [name, fn] of Object.entries(sessionListeners)) videoEl.removeEventListener(name, fn);
    sessionListeners = null;
  }
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

// `onTime(seconds)` is called as playback advances, with seconds of video
// elapsed since this session's first frame (used to move the timeline bar).
export async function playSegment(cam, startTime, endTime, onTime = null) {
  await stopCurrent();
  if (onTime) {
    let base = null;
    timeHandler = () => {
      if (videoEl.paused && base === null) return;
      if (base === null) base = videoEl.currentTime;
      onTime(videoEl.currentTime - base);
    };
    videoEl.addEventListener("timeupdate", timeHandler);
  }
  playerEl.hidden = false;
  statusEl.hidden = false;
  statusEl.textContent = "Starting playback… this can take up to ~15s.";
  // Per-session listeners, removed again in stopCurrent() so they don't pile up.
  const onPlaying = () => { statusEl.hidden = true; };
  const onEnded = () => {
    stopStatusPolling();
    statusEl.hidden = false;
    statusEl.textContent = "Finished.";
  };
  videoEl.addEventListener("playing", onPlaying, { once: true });
  videoEl.addEventListener("ended", onEnded, { once: true });
  sessionListeners = { playing: onPlaying, ended: onEnded };

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
  startStatusPolling(session.session_id);
  startWatchdog();

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
      // Separate from manifestLoadPolicy, but must be at least as generous:
      // master.m3u8 (the "manifest") is written synchronously and always
      // exists immediately, so manifestLoadPolicy's budget is never really
      // exercised — the real ffmpeg cold-start wait (up to the server's own
      // 30s poll window, see api/playback.py's _PLAYLIST_WAIT_SECONDS)
      // lands on the *first* video.m3u8/audio.m3u8 fetch, which this policy
      // governs. Previously set to 20000ms, shorter than the server's 30s
      // wait — on any cold start landing in that 20-30s gap, hls.js would
      // time out and start retrying before the server would have answered,
      // sometimes without ever surfacing a fatal error the UI could catch
      // (looked like an indefinite hang past 20s, worse on longer clips
      // whose cold start is more likely to land past 20s). This policy also
      // governs the periodic reloads hls.js does throughout playback to
      // discover new segments as the playlist grows — a longer timeout
      // there is harmless (segments flow quickly once started) and was the
      // same reasoning as the comment this one replaces.
      playlistLoadPolicy: {
        default: {
          maxTimeToFirstByteMs: 35000,
          maxLoadTimeMs: 35000,
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

export function isPlayerOpen() {
  return !playerEl.hidden;
}

export async function closePlayer() {
  await stopCurrent();
  playerEl.hidden = true;
}

closeBtn.addEventListener("click", closePlayer);
