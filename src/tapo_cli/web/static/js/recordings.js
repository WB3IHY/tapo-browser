// Recordings browser + downloads with live SSE progress.
import { api } from "./api.js";
import { playSegment, closePlayer, isPlayerOpen } from "./player.js";
import { createThumbnailLoader } from "./thumbnail_loader.js";
import { createTimeline } from "./timeline.js";

const dialog = document.getElementById("rec-dialog");
const daysEl = document.getElementById("rec-days");
const segsEl = document.getElementById("rec-segments");
const statusEl = document.getElementById("rec-status");
const downloadsEl = document.getElementById("downloads");
const startInput = document.getElementById("rec-start");
const endInput = document.getElementById("rec-end");
const timelineEl = document.getElementById("rec-timeline");

let cam = null;
let thumbLoader = null;
let timeline = null;
let daySegs = [];            // current day's segments, oldest first
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
  thumbLoader?.destroy();
  thumbLoader = null;
  hideTimeline();
  segsEl.innerHTML = `<div class="seg"><span class="spinner"></span> Loading…</div>`;
  try {
    const segs = await api(`/api/cameras/${cam.id}/recordings/segments?date=${date}`);
    // The bar is an extra: if the event lookup fails the list still works.
    const eventsReq = api(`/api/cameras/${cam.id}/recordings/events?date=${date}`).catch(() => null);
    if (!segs.length) {
      segsEl.innerHTML = `<div class="empty" style="padding:24px">No segments for this day.</div>`;
      return;
    }
    segsEl.innerHTML = "";
    for (const s of segs) {
      const el = document.createElement("div");
      el.className = "seg";
      el.dataset.start = s.start_time;
      el.innerHTML = `
        <img class="seg-thumb" alt=""
             data-src="/api/cameras/${cam.id}/recordings/segments/${s.start_time}/thumbnail" />
        <div class="seg-info">
          <div class="seg-time">${s.start_label} – ${s.end_label}</div>
          <div class="seg-dur">${fmtDuration(s.duration_sec)}</div>
        </div>
        <div class="seg-actions">
          <button class="small primary act-play">▶ Play</button>
          <button class="small act-dl">Download</button>
        </div>`;
      el.querySelector(".act-play").addEventListener("click", () => {
        // With the bar up, play via it so the bar follows and playback rolls on.
        if (timeline) playFrom(s.start_time);
        else playSegment(cam, s.start_time, s.end_time);
      });
      el.querySelector(".act-dl").addEventListener("click", (ev) => {
        ev.target.disabled = true;
        startDownload(date, s);
      });
      segsEl.appendChild(el);
    }

    thumbLoader = createThumbnailLoader(segsEl);
    thumbLoader.settle();

    const ev = await eventsReq;
    if (ev && segsEl.querySelector(".seg")) showTimeline(date, ev, segs);
  } catch (err) {
    segsEl.innerHTML = `<div class="empty" style="padding:24px;color:var(--danger)">${err.message}</div>`;
  }
}

function hideTimeline() {
  timeline?.destroy();
  timeline = null;
  timelineEl.hidden = true;
}

// Play from a raw timestamp: the camera streams one session chronologically
// through every later recording that day (skipping gaps), so the bar can keep
// following the video and playback rolls on into the next recording by itself.
// A moment inside a gap starts at the next recording.
function playFrom(raw) {
  raw = Math.floor(raw); // the bar's position is fractional; the camera wants whole seconds
  const i = daySegs.findIndex((s) => raw <= s.end_time);
  const idx = i === -1 ? daySegs.length - 1 : i;
  if (idx < 0) return;
  const startRaw = Math.max(raw, daySegs[idx].start_time);
  const chain = daySegs.slice(idx);
  // Footage lengths: the first clip from the drop point, the rest in full.
  const lengths = chain.map((s, n) => s.end_time - (n === 0 ? startRaw : s.start_time));
  const total = lengths.reduce((a, b) => a + b, 0);
  const toRaw = (elapsed) => {
    for (let n = 0; n < chain.length; n++) {
      if (elapsed < lengths[n]) return (n === 0 ? startRaw : chain[n].start_time) + elapsed;
      elapsed -= lengths[n];
    }
    return chain[chain.length - 1].end_time;
  };
  const tl = timeline;
  playSegment(cam, startRaw, startRaw + total, (t) => tl?.follow(toRaw(t)));
}

function showTimeline(date, ev, segs) {
  timelineEl.hidden = false;
  daySegs = [...segs].sort((a, b) => a.start_time - b.start_time);
  timeline = createTimeline(timelineEl, {
    dayStart: ev.day_start,
    timeCorrection: ev.time_correction,
    events: ev.events,
    segments: segs,
    onPlay: playFrom,
    onSettle: (s) => {
      segsEl.querySelectorAll(".seg.hl").forEach((x) => x.classList.remove("hl"));
      const row = s && segsEl.querySelector(`.seg[data-start="${s.start_time}"]`);
      if (!row) return;
      row.classList.add("hl");
      // Scroll only the list, never the page/dialog (scrollIntoView would yank those too).
      const top = row.offsetTop - segsEl.offsetTop;
      if (top < segsEl.scrollTop || top + row.offsetHeight > segsEl.scrollTop + segsEl.clientHeight) {
        segsEl.scrollTo({ top: top - 8, behavior: "smooth" });
      }
    },
  });
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
    `${prettyDay(dl.date)} ${dl.start_label}–${dl.end_label}`;
  el.querySelector(".dl-bar > div").style.width = `${dl.progress_pct}%`;

  const statusNode = el.querySelector(".dl-status");
  statusNode.className = "dl-status" + (dl.status === "done" ? " done" : dl.status === "error" ? " error" : "");
  statusNode.textContent = labelFor(dl);
  statusNode.title = dl.error || "";

  const action = el.querySelector(".dl-action");
  if (dl.status === "done") {
    action.innerHTML = `<a class="btn small primary" href="/api/downloads/${dl.id}/file">Save file</a> <button class="small">Clear</button>`;
    action.querySelector("button").onclick = () => clearRow(dl.id);
  } else if (dl.status === "running" || dl.status === "queued") {
    action.innerHTML = `<button class="small">Cancel</button>`;
    action.querySelector("button").onclick = async () => {
      try { await api(`/api/downloads/${dl.id}/cancel`, { method: "POST" }); } catch {}
    };
  } else {
    // error / canceled
    action.innerHTML = `<button class="small">Clear</button>`;
    action.querySelector("button").onclick = () => clearRow(dl.id);
  }
}

async function clearRow(id) {
  try { await api(`/api/downloads/${id}`, { method: "DELETE" }); } catch { return; }
  const entry = rows.get(id);
  entry?.source?.close();
  entry?.el?.remove();
  rows.delete(id);
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
function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return m ? `${m}m ${s}s` : `${s}s`;
}

document.getElementById("rec-load-days").addEventListener("click", loadDays);

// The header ✕ (and Escape) peel back one layer at a time: with a player
// open they close just the player and leave the camera's recordings list
// where it was; only with no player open do they close the dialog.
const recCloseBtn = document.getElementById("rec-close");
recCloseBtn.addEventListener("click", () => {
  if (isPlayerOpen()) closePlayer();
  else dialog.close();
});
dialog.addEventListener("cancel", (ev) => {
  if (isPlayerOpen()) {
    ev.preventDefault();
    closePlayer();
  }
});
dialog.addEventListener("close", () => {
  for (const { source } of rows.values()) source?.close();
  closePlayer();
  thumbLoader?.destroy();
  thumbLoader = null;
  hideTimeline();
});
