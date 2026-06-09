// Live view: mount go2rtc's <video-stream> web component into the live dialog.
import { api } from "./api.js";

const dialog = document.getElementById("live-dialog");
const mount = document.getElementById("live-mount");
const title = document.getElementById("live-title");

let hintTimer = null;

export async function openLive(cam) {
  title.textContent = `Live — ${cam.name}`;
  mount.innerHTML = "";
  const el = document.createElement("video-stream");
  // WebRTC first (lowest latency), then MSE, then HLS — all via the same-origin proxy.
  el.setAttribute("mode", "webrtc,mse,hls");
  el.setAttribute("background", "true");
  mount.appendChild(el);

  const hint = document.createElement("div");
  hint.className = "live-hint";
  hint.hidden = true;
  hint.innerHTML =
    "Live video isn't starting. This camera's firmware may not be compatible with the " +
    "streaming bridge, or it may need RTSP enabled in the Tapo app. " +
    "<strong>Recordings and downloads still work.</strong>";
  mount.appendChild(hint);

  dialog.showModal();
  try {
    const info = await api(`/api/cameras/${cam.id}/stream/info`);
    el.src = info.ws_src; // component turns this into ws://<origin>/...
    // If no real video frame has decoded after a while, show a helpful hint.
    clearTimeout(hintTimer);
    hintTimer = setTimeout(() => {
      const v = el.querySelector("video");
      // Not really playing: no element, not enough data buffered, or only the
      // tiny placeholder resolution decoded (real Tapo streams are >= 640px wide).
      if (!v || v.readyState < 2 || v.videoWidth < 320) hint.hidden = false;
    }, 12000);
  } catch (err) {
    mount.innerHTML = `<div class="empty">Could not start stream: ${err.message}</div>`;
  }
}

// Tear down the player when the dialog closes so the stream stops.
dialog.addEventListener("close", () => {
  clearTimeout(hintTimer);
  mount.innerHTML = "";
});
