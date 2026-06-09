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

// Cache the last controls list per camera so reopening paints instantly while
// fresh values load in the background.
const controlsCache = new Map();

async function renderControls(cam) {
  const cached = controlsCache.get(cam.id);
  if (cached) paintControls(cam, cached);
  else controlsEl.innerHTML = `<div class="ctrl-loading"><span class="spinner"></span> Loading controls…</div>`;

  let list;
  try {
    list = await api(`/api/cameras/${cam.id}/controls`);
  } catch (err) {
    if (!cached) controlsEl.innerHTML = `<div class="ctrl-err">Controls unavailable: ${err.message}</div>`;
    return;
  }
  // Only repaint if something actually changed (avoids flicker / disrupting a toggle).
  if (JSON.stringify(list) !== JSON.stringify(cached)) paintControls(cam, list);
  controlsCache.set(cam.id, list);
}

function paintControls(cam, list) {
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
  const wide = c.type === "number" || c.type === "ptz";
  const wrap = document.createElement("div");
  wrap.className = "ctrl" + (wide ? " ctrl-wide" : "") + (c.type === "ptz" ? " ctrl-ptz" : "");

  const status = document.createElement("span");
  status.className = "ctrl-status";
  const flash = (txt, ok = true) => {
    status.textContent = txt;
    status.className = "ctrl-status" + (ok ? "" : " err");
    status.title = "";
    if (txt === "✓") setTimeout(() => { if (status.textContent === "✓") status.textContent = ""; }, 1200);
  };
  const send = async (value, revert) => {
    flash("…");
    try {
      await api(`/api/cameras/${cam.id}/controls/${c.key}`, { method: "POST", body: JSON.stringify({ value }) });
      flash("✓");
      return true;
    } catch (err) {
      flash("✕", false);
      status.title = err.message;
      if (revert) revert();
      return false;
    }
  };

  // label + info-tooltip
  const head = () => {
    const h = document.createElement("span");
    h.className = "ctrl-label";
    h.textContent = c.label;
    if (c.desc) {
      const i = document.createElement("span");
      i.className = "ctrl-info";
      i.textContent = "ⓘ";
      i.dataset.tip = c.desc;
      h.appendChild(i);
    }
    return h;
  };

  if (c.type === "toggle") {
    const sw = document.createElement("label");
    sw.className = "switch";
    sw.innerHTML = `<input type="checkbox" ${c.value ? "checked" : ""}><span class="slider"></span>`;
    const cb = sw.querySelector("input");
    cb.addEventListener("change", () => send(cb.checked, () => (cb.checked = !cb.checked)));
    wrap.append(sw, head(), status);
  } else if (c.type === "select") {
    const sel = document.createElement("select");
    for (const o of c.options || []) {
      const opt = document.createElement("option");
      opt.value = o; opt.textContent = o;
      if (o === String(c.value)) opt.selected = true;
      sel.appendChild(opt);
    }
    let prev = String(c.value);
    sel.addEventListener("change", async () => { if (await send(sel.value, () => (sel.value = prev))) prev = sel.value; });
    wrap.append(head(), sel, status);
  } else if (c.type === "number") {
    const range = document.createElement("input");
    range.type = "range";
    range.min = c.min ?? 0; range.max = c.max ?? 100; range.step = c.step ?? 1;
    range.value = c.value ?? 0;
    const num = document.createElement("span");
    num.className = "ctrl-num";
    num.textContent = range.value;
    range.addEventListener("input", () => (num.textContent = range.value));
    let prev = range.value;
    range.addEventListener("change", async () => {
      if (await send(Number(range.value), () => { range.value = prev; num.textContent = prev; })) prev = range.value;
    });
    const row = document.createElement("div");
    row.className = "ctrl-numrow";
    row.append(range, num);
    wrap.append(head(), row, status);
  } else if (c.type === "action") {
    const btn = document.createElement("button");
    btn.className = "small" + (c.danger ? " danger" : "");
    btn.textContent = c.label;
    btn.addEventListener("click", () => {
      if (c.danger && !confirm(`${c.label}?\n\n${c.desc || ""}`)) return;
      send(null);
    });
    wrap.append(btn);
    if (c.desc) {
      const i = document.createElement("span");
      i.className = "ctrl-info"; i.textContent = "ⓘ"; i.dataset.tip = c.desc;
      wrap.append(i);
    }
    wrap.append(status);
  } else if (c.type === "ptz") {
    const pad = document.createElement("div");
    pad.className = "ptz-pad";
    const glyph = { up: "▲", down: "▼", left: "◀", right: "▶" };
    for (const d of ["up", "left", "right", "down"]) {
      const b = document.createElement("button");
      b.dataset.dir = d; b.textContent = glyph[d]; b.title = d;
      b.addEventListener("click", () => send(d));
      pad.appendChild(b);
    }
    wrap.append(head(), pad, status);
  }
  return wrap;
}

// Tear down the player when the dialog closes so the stream stops.
dialog.addEventListener("close", () => {
  clearInterval(hintTimer);
  mount.innerHTML = "";
  controlsEl.innerHTML = "";
});
