// Live view: mount go2rtc's <video-stream> web component into the live dialog.
import { api } from "./api.js";

const dialog = document.getElementById("live-dialog");
const mount = document.getElementById("live-mount");
const title = document.getElementById("live-title");
const controlsEl = document.getElementById("live-controls");

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

  controlsEl.innerHTML = "";
  dialog.showModal();
  try {
    const info = await api(`/api/cameras/${cam.id}/stream/info`);
    el.src = info.ws_src; // component turns this into ws://<origin>/...
    // Self-correcting hint: hide as soon as real video is decoding; only show it
    // if the stream is still stuck after a grace period (this firmware can be slow
    // to deliver the first keyframe).
    clearInterval(hintTimer);
    const startedAt = Date.now();
    hintTimer = setInterval(() => {
      const v = el.querySelector("video");
      const playing = v && v.readyState >= 2 && v.videoWidth >= 320;
      if (playing) hint.hidden = true;
      else if (Date.now() - startedAt > 15000) hint.hidden = false;
    }, 2000);
  } catch (err) {
    mount.innerHTML = `<div class="empty">Could not start stream: ${err.message}</div>`;
  }
  renderControls(cam);
}

async function renderControls(cam) {
  controlsEl.innerHTML = `<div class="ctrl-loading"><span class="spinner"></span> Loading controls…</div>`;
  let list;
  try {
    list = await api(`/api/cameras/${cam.id}/controls`);
  } catch (err) {
    controlsEl.innerHTML = `<div class="ctrl-err">Controls unavailable: ${err.message}</div>`;
    return;
  }
  controlsEl.innerHTML = "";
  if (!list.length) return;
  // Group by capability category, preserving first-seen order.
  const order = [];
  const byGroup = new Map();
  for (const c of list) {
    const g = c.group || "Controls";
    if (!byGroup.has(g)) { byGroup.set(g, []); order.push(g); }
    byGroup.get(g).push(c);
  }
  for (const g of order) {
    const section = document.createElement("div");
    section.className = "ctrl-group";
    const h = document.createElement("div");
    h.className = "ctrl-group-title";
    h.textContent = g;
    const row = document.createElement("div");
    row.className = "ctrl-row";
    for (const c of byGroup.get(g)) row.appendChild(controlEl(cam, c));
    section.append(h, row);
    controlsEl.appendChild(section);
  }
}

function controlEl(cam, c) {
  const wrap = document.createElement("div");
  wrap.className = "ctrl";
  if (c.help) wrap.title = c.help;
  const status = document.createElement("span");
  status.className = "ctrl-status";

  const send = async (value, revert) => {
    status.textContent = "…";
    try {
      await api(`/api/cameras/${cam.id}/controls/${c.key}`, {
        method: "POST",
        body: JSON.stringify({ value }),
      });
      status.textContent = "✓";
      setTimeout(() => (status.textContent = ""), 1200);
    } catch (err) {
      status.textContent = "✕";
      status.title = err.message;
      if (revert) revert();
    }
  };

  if (c.type === "toggle") {
    const id = `ctrl-${c.key}`;
    const sw = document.createElement("label");
    sw.className = "switch";
    sw.innerHTML = `<input type="checkbox" id="${id}" ${c.value ? "checked" : ""}><span class="slider"></span>`;
    const cb = sw.querySelector("input");
    cb.addEventListener("change", () => send(cb.checked, () => (cb.checked = !cb.checked)));
    const lab = document.createElement("label");
    lab.className = "ctrl-label";
    lab.htmlFor = id;
    lab.textContent = c.label;
    wrap.append(sw, lab, status);
  } else if (c.type === "select") {
    const lab = document.createElement("span");
    lab.className = "ctrl-label";
    lab.textContent = c.label;
    const sel = document.createElement("select");
    for (const o of c.options || []) {
      const opt = document.createElement("option");
      opt.value = o;
      opt.textContent = o;
      if (o === c.value) opt.selected = true;
      sel.appendChild(opt);
    }
    let prev = c.value;
    sel.addEventListener("change", () => {
      const v = sel.value;
      send(v, () => (sel.value = prev));
      prev = v;
    });
    wrap.append(lab, sel, status);
  }
  return wrap;
}

// Tear down the player when the dialog closes so the stream stops.
dialog.addEventListener("close", () => {
  clearInterval(hintTimer);
  mount.innerHTML = "";
  controlsEl.innerHTML = "";
});
