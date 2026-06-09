import "../vendor/video-stream.js"; // defines the <video-stream> custom element
import { api, snapshotUrl } from "./api.js";
import { openLive } from "./live.js";
import { openRecordings } from "./recordings.js";

const grid = document.getElementById("cameras");
const emptyEl = document.getElementById("empty");
const cameraDialog = document.getElementById("camera-dialog");
const form = document.getElementById("camera-form");
const testResult = document.getElementById("test-result");

let cameras = [];

// --------------------------------------------------------------------------- //
// Rendering
// --------------------------------------------------------------------------- //
const PLAY_SVG = `<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>`;

function cardEl(cam) {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="thumb" title="Watch live">
      <span class="placeholder">${cam.enabled ? "Loading preview…" : "Disabled"}</span>
      <div class="play-overlay">${PLAY_SVG}</div>
    </div>
    <div class="body">
      <div class="name"><span class="status checking" title="Checking…"></span>${escapeHtml(cam.name)}
        ${cam.enabled ? "" : '<span class="badge disabled">disabled</span>'}
      </div>
      <div class="meta">${escapeHtml(cam.host)}</div>
      <div class="meta model">—</div>
      <div class="actions">
        <button class="small primary act-live">▶ Live</button>
        <button class="small act-rec">Recordings</button>
        <button class="small act-edit">Edit</button>
        <button class="small danger act-del">Delete</button>
      </div>
    </div>`;

  // thumbnail preview (only for enabled cameras)
  const thumb = card.querySelector(".thumb");
  if (cam.enabled) {
    const img = new Image();
    img.onload = () => { thumb.querySelector(".placeholder")?.remove(); thumb.prepend(img); };
    img.onerror = () => { const p = thumb.querySelector(".placeholder"); if (p) p.textContent = "No preview"; };
    img.src = snapshotUrl(cam.slug);
  }
  thumb.addEventListener("click", () => openLive(cam));

  card.querySelector(".act-live").addEventListener("click", () => openLive(cam));
  card.querySelector(".act-rec").addEventListener("click", () => openRecordings(cam));
  card.querySelector(".act-edit").addEventListener("click", () => openEdit(cam));
  card.querySelector(".act-del").addEventListener("click", () => deleteCamera(cam));

  // async connection probe -> update status dot + model line
  probe(cam, card);
  return card;
}

async function probe(cam, card) {
  const dot = card.querySelector(".status");
  const model = card.querySelector(".model");
  try {
    const r = await api(`/api/cameras/${cam.id}/test-connection`, { method: "POST" });
    if (r.online) {
      dot.className = "status online"; dot.title = "Online";
      const bits = [r.model, r.firmware && `fw ${r.firmware}`].filter(Boolean);
      if (r.sd_present && r.sd_total_mb) bits.push(`SD ${(r.sd_free_mb / 1024).toFixed(1)}/${(r.sd_total_mb / 1024).toFixed(1)} GB free`);
      else bits.push("no SD card");
      model.textContent = bits.join(" · ") || "Online";
    } else {
      dot.className = "status offline"; dot.title = r.error || "Offline";
      model.textContent = r.error || "Offline";
    }
  } catch (err) {
    dot.className = "status offline"; dot.title = err.message;
    model.textContent = err.message;
  }
}

function render() {
  grid.innerHTML = "";
  emptyEl.hidden = cameras.length > 0;
  for (const cam of cameras) grid.appendChild(cardEl(cam));
}

async function loadCameras() {
  cameras = await api("/api/cameras");
  render();
}

// --------------------------------------------------------------------------- //
// Add / edit form
// --------------------------------------------------------------------------- //
function openAdd() {
  form.reset();
  form.id.value = "";
  form.enabled.checked = true;
  document.getElementById("camera-dlg-title").textContent = "Add camera";
  hideTest();
  cameraDialog.showModal();
}

function openEdit(cam) {
  form.reset();
  form.id.value = cam.id;
  form.name.value = cam.name;
  form.host.value = cam.host;
  form.control_port.value = cam.control_port ?? "";
  form.account_password.value = ""; // never round-tripped; blank = keep existing
  form.enabled.checked = !!cam.enabled;
  document.getElementById("camera-dlg-title").textContent = "Edit camera";
  hideTest();
  cameraDialog.showModal();
}

function readForm() {
  const isEdit = !!form.id.value;
  const payload = {
    name: form.name.value.trim(),
    host: form.host.value.trim(),
    control_port: form.control_port.value ? Number(form.control_port.value) : null,
    enabled: form.enabled.checked,
  };
  // Password: on edit, only send if the user typed something (blank = keep).
  if (form.account_password.value || !isEdit) payload.account_password = form.account_password.value;
  return { isEdit, payload };
}

async function saveCamera(e) {
  e.preventDefault();
  const { isEdit, payload } = readForm();
  if (!isEdit && !payload.account_password) {
    showTest(false, "The TP-Link account password is required.");
    return;
  }
  const saveBtn = document.getElementById("save-btn");
  saveBtn.disabled = true;
  try {
    if (isEdit) {
      await api(`/api/cameras/${form.id.value}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/cameras", { method: "POST", body: JSON.stringify(payload) });
    }
    cameraDialog.close();
    await loadCameras();
  } catch (err) {
    showTest(false, `Save failed: ${err.message}`);
  } finally {
    saveBtn.disabled = false;
  }
}

async function testConnection() {
  const btn = document.getElementById("test-btn");
  const body = {
    host: form.host.value.trim(),
    account_password: form.account_password.value,
    control_port: form.control_port.value ? Number(form.control_port.value) : null,
  };
  if (!body.host) { showTest(false, "Enter the host / IP first."); return; }

  // When editing with blank password, test the saved camera instead.
  const editId = form.id.value;
  const useSaved = editId && !body.account_password;

  btn.disabled = true;
  showTest(null, `<span class="spinner"></span> Testing…`);
  try {
    const r = useSaved
      ? await api(`/api/cameras/${editId}/test-connection`, { method: "POST" })
      : await api("/api/cameras/test-connection", { method: "POST", body: JSON.stringify(body) });
    if (r.online) {
      const bits = [r.alias, r.model, r.firmware && `firmware ${r.firmware}`].filter(Boolean);
      if (r.sd_present && r.sd_total_mb) bits.push(`SD card: ${(r.sd_free_mb / 1024).toFixed(1)} GB free of ${(r.sd_total_mb / 1024).toFixed(1)} GB`);
      else bits.push("no SD card detected");
      showTest(true, `Connected ✓ — ${bits.join(" · ")}`);
    } else {
      showTest(false, r.error || "Could not connect.");
    }
  } catch (err) {
    showTest(false, err.message);
  } finally {
    btn.disabled = false;
  }
}

async function deleteCamera(cam) {
  if (!confirm(`Delete camera “${cam.name}”? This does not delete anything on the camera itself.`)) return;
  try {
    await api(`/api/cameras/${cam.id}`, { method: "DELETE" });
    await loadCameras();
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

// --------------------------------------------------------------------------- //
// helpers
// --------------------------------------------------------------------------- //
function showTest(ok, html) {
  testResult.className = "test-result show " + (ok === true ? "ok" : ok === false ? "err" : "");
  testResult.innerHTML = html;
}
function hideTest() { testResult.className = "test-result"; testResult.innerHTML = ""; }
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// --------------------------------------------------------------------------- //
// wiring
// --------------------------------------------------------------------------- //
document.getElementById("add-btn").addEventListener("click", openAdd);
form.addEventListener("submit", saveCamera);
document.getElementById("test-btn").addEventListener("click", testConnection);
document.querySelectorAll("[data-close]").forEach((b) =>
  b.addEventListener("click", () => b.closest("dialog").close())
);

loadCameras().catch((err) => {
  grid.innerHTML = `<div class="empty">Failed to load: ${err.message}</div>`;
});
