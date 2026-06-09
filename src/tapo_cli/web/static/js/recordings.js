// Recordings browser + downloads with live SSE progress.
import { api } from "./api.js";

const dialog = document.getElementById("rec-dialog");
const daysEl = document.getElementById("rec-days");
const segsEl = document.getElementById("rec-segments");
const statusEl = document.getElementById("rec-status");
const downloadsEl = document.getElementById("downloads");
const startInput = document.getElementById("rec-start");
const endInput = document.getElementById("rec-end");

let cam = null;
const rows = new Map();      // download id -> { el, source }

export async function openRecordings(camera) {
  cam = camera;
  document.getElementById("rec-title").textContent = `Recordings — ${camera.name}`;
  daysEl.innerHTML = "";
  segsEl.innerHTML = `<div class="empty" style="padding:24px">Pick a day to list its recordings.</div>`;
  statusEl.textContent = "";

  const today = new Date();
  const weekAgo = new Date(today.getTime() - 6 * 86400000);
  endInput.value = isoDate(today);
  startInput.value = isoDate(weekAgo);

  dialog.showModal();
  await refreshDownloads();
}

// --------------------------------------------------------------------------- //
// Days + segments
// --------------------------------------------------------------------------- //
async function loadDays() {
  daysEl.innerHTML = `<div class="day"><span class="spinner"></span> Searching…</div>`;
  statusEl.textContent = "";
  try {
    const days = await api(
      `/api/cameras/${cam.id}/recordings/days?start=${startInput.value}&end=${endInput.value}`
    );
    if (!days.length) {
      daysEl.innerHTML = `<div class="empty" style="padding:18px">No footage in this range.</div>`;
      return;
    }
    daysEl.innerHTML = "";
    for (const d of days) {
      const el = document.createElement("div");
      el.className = "day";
      el.textContent = prettyDay(d.date);
      el.addEventListener("click", () => {
        daysEl.querySelectorAll(".day").forEach((x) => x.classList.remove("active"));
        el.classList.add("active");
        loadSegments(d.date);
      });
      daysEl.appendChild(el);
    }
  } catch (err) {
    daysEl.innerHTML = `<div class="empty" style="padding:18px;color:var(--danger)">${err.message}</div>`;
  }
}

async function loadSegments(date) {
  segsEl.innerHTML = `<div class="seg"><span class="spinner"></span> Loading…</div>`;
  try {
    const segs = await api(`/api/cameras/${cam.id}/recordings/segments?date=${date}`);
    if (!segs.length) {
      segsEl.innerHTML = `<div class="empty" style="padding:24px">No segments for this day.</div>`;
      return;
    }
    segsEl.innerHTML = "";
    for (const s of segs) {
      const el = document.createElement("div");
      el.className = "seg";
      el.innerHTML = `
        <div>
          <div class="seg-time">${s.start_label} – ${s.end_label}</div>
          <div class="seg-dur">${fmtDuration(s.duration_sec)}</div>
        </div>
        <button class="small primary">Download</button>`;
      el.querySelector("button").addEventListener("click", (ev) => {
        ev.target.disabled = true;
        startDownload(date, s);
      });
      segsEl.appendChild(el);
    }
  } catch (err) {
    segsEl.innerHTML = `<div class="empty" style="padding:24px;color:var(--danger)">${err.message}</div>`;
  }
}

// --------------------------------------------------------------------------- //
// Downloads
// --------------------------------------------------------------------------- //
async function startDownload(date, seg) {
  try {
    const dl = await api("/api/downloads", {
      method: "POST",
      body: JSON.stringify({
        camera_id: cam.id,
        date,
        start_time: seg.start_time,
        end_time: seg.end_time,
      }),
    });
    upsertRow(dl);
    attachEvents(dl.id);
  } catch (err) {
    alert(`Could not start download: ${err.message}`);
  }
}

async function refreshDownloads() {
  // close any previous SSE connections
  for (const { source } of rows.values()) source?.close();
  rows.clear();
  downloadsEl.innerHTML = "";

  const list = await api(`/api/downloads?camera_id=${cam.id}`);
  if (list.length) {
    const h = document.createElement("h3");
    h.textContent = "Downloads";
    h.style.cssText = "font-size:15px;margin:0 0 4px";
    downloadsEl.appendChild(h);
  }
  for (const dl of list) {
    upsertRow(dl);
    if (["queued", "running"].includes(dl.status)) attachEvents(dl.id);
  }
}

function attachEvents(id) {
  const existing = rows.get(id);
  if (existing?.source) return;
  const source = new EventSource(`/api/downloads/${id}/events`);
  source.onmessage = (e) => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data._done) { source.close(); if (rows.get(id)) rows.get(id).source = null; return; }
    upsertRow(data);
  };
  source.onerror = () => { /* browser auto-reconnects; final state is in the DB */ };
  if (rows.get(id)) rows.get(id).source = source;
  else rows.set(id, { el: null, source });
}

function upsertRow(dl) {
  let entry = rows.get(dl.id);
  if (!entry || !entry.el) {
    const el = document.createElement("div");
    el.className = "dl-row";
    el.innerHTML = `
      <div class="dl-label"></div>
      <div class="dl-bar"><div></div></div>
      <div class="dl-status"></div>
      <div class="dl-action"></div>`;
    downloadsEl.appendChild(el);
    entry = { el, source: entry?.source ?? null };
    rows.set(dl.id, entry);
  }
  const el = entry.el;
  el.querySelector(".dl-label").textContent =
    `${prettyDay(dl.date)} ${fmtClock(dl.start_time)}–${fmtClock(dl.end_time)}`;
  el.querySelector(".dl-bar > div").style.width = `${dl.progress_pct}%`;

  const statusNode = el.querySelector(".dl-status");
  statusNode.className = "dl-status" + (dl.status === "done" ? " done" : dl.status === "error" ? " error" : "");
  statusNode.textContent = labelFor(dl);
  statusNode.title = dl.error || "";

  const action = el.querySelector(".dl-action");
  if (dl.status === "done") {
    action.innerHTML = `<a class="btn small primary" href="/api/downloads/${dl.id}/file">Save file</a>`;
  } else if (dl.status === "running" || dl.status === "queued") {
    action.innerHTML = `<button class="small">Cancel</button>`;
    action.querySelector("button").onclick = async () => {
      try { await api(`/api/downloads/${dl.id}/cancel`, { method: "POST" }); } catch {}
    };
  } else {
    action.innerHTML = "";
  }
}

function labelFor(dl) {
  if (dl.status === "done") return "Done";
  if (dl.status === "error") return "Error";
  if (dl.status === "canceled") return "Canceled";
  if (dl.current_action) return `${dl.current_action} ${dl.progress_pct}%`;
  return `${dl.status} ${dl.progress_pct}%`;
}

// --------------------------------------------------------------------------- //
// helpers
// --------------------------------------------------------------------------- //
const isoDate = (d) => d.toISOString().slice(0, 10);
const prettyDay = (yyyymmdd) => `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
const fmtClock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return m ? `${m}m ${s}s` : `${s}s`;
}

document.getElementById("rec-load-days").addEventListener("click", loadDays);
dialog.addEventListener("close", () => {
  for (const { source } of rows.values()) source?.close();
});
