// Event timeline bar: a zoomed strip of a day's detection events with a fixed
// centered playhead. Drag (or wheel) the strip to scrub; when it settles, the
// recording under the playhead is reported so the list can highlight it.
//
// All times are the camera's raw clock; `timeCorrection` is applied only to
// the labels shown to the user.

const WINDOW_SEC = 3 * 3600;
const DAY_SEC = 86400;
const SETTLE_MS = 250;
const AUTOPLAY_MS = 1000;    // pause after the last move before playback starts there
const TICK_SEC = 1800;
const CLICK_SLOP_PX = 4;

const pad = (n) => String(n).padStart(2, "0");

export function createTimeline(container, { dayStart, timeCorrection, events, segments, onSettle, onPlay }) {
  const ctl = new AbortController();
  const { signal } = ctl;

  container.innerHTML = `
    <div class="tl-bar">
      <div class="tl-strip"></div>
      <div class="tl-playhead"></div>
      <div class="tl-bubble"></div>
      <button type="button" class="small primary tl-play" hidden>▶ Play</button>
    </div>
    <div class="tl-legend"><i class="motion"></i>motion<i class="person"></i>person<i class="other"></i>other</div>`;
  const bar = container.querySelector(".tl-bar");
  const strip = container.querySelector(".tl-strip");
  const bubble = container.querySelector(".tl-bubble");
  const playBtn = container.querySelector(".tl-play");

  const dayEnd = dayStart + DAY_SEC;
  let pps = 1;                 // pixels per second
  let center = dayStart;       // raw ts under the playhead
  let selected = null;         // segment under the playhead after settling
  let settleTimer = null;
  let playTimer = null;

  // Static content: recordings as a thin underlay, events on top, hour ticks.
  const frag = document.createDocumentFragment();
  const add = (cls, start, end) => {
    const el = document.createElement("div");
    el.className = cls;
    el.dataset.start = start;
    el.dataset.len = Math.max(0, end - start);
    frag.appendChild(el);
    return el;
  };
  for (const s of segments) add("tl-rec", s.start_time, s.end_time);
  for (const e of events) add(`tl-ev ${e.kind}`, e.start_time, e.end_time);
  // Ticks sit at true-time half hours (raw position shifted back by the correction).
  for (let k = 0; k <= DAY_SEC / TICK_SEC; k++) {
    const t = dayStart - timeCorrection + k * TICK_SEC;
    const el = add("tl-tick", t, t);
    const span = document.createElement("span");
    span.textContent = clock(k * TICK_SEC, true);
    el.appendChild(span);
  }
  strip.appendChild(frag);

  function clock(secOfDay, short = false) {
    const s = ((Math.round(secOfDay) % DAY_SEC) + DAY_SEC) % DAY_SEC;
    const hms = `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}`;
    return short ? hms : `${hms}:${pad(s % 60)}`;
  }
  // Display time-of-day for a raw ts: shift by the correction, not by timezone.
  const label = (raw) => clock(raw - dayStart + timeCorrection);

  function layout() {
    pps = bar.clientWidth / WINDOW_SEC;
    strip.style.width = `${DAY_SEC * pps}px`;
    for (const el of strip.children) {
      const start = Number(el.dataset.start), len = Number(el.dataset.len);
      el.style.left = `${(start - dayStart) * pps}px`;
      if (!el.classList.contains("tl-tick")) el.style.width = `${Math.max(3, len * pps)}px`;
    }
    render();
  }

  function render() {
    strip.style.transform = `translateX(${bar.clientWidth / 2 - (center - dayStart) * pps}px)`;
    bubble.textContent = label(center);
  }

  // Move the playhead and cancel anything pending, without scheduling playback.
  function scrub(raw) {
    center = Math.min(dayEnd, Math.max(dayStart, raw));
    render();
    clearTimeout(settleTimer);
    clearTimeout(playTimer);
    settleTimer = playTimer = null;
    playBtn.hidden = true;
  }

  // Once movement stops: highlight the recording, then auto-play from here.
  function arm({ autoplay = true } = {}) {
    settleTimer = setTimeout(settle, SETTLE_MS);
    if (autoplay) playTimer = setTimeout(() => { playTimer = null; onPlay?.(center); }, AUTOPLAY_MS);
  }

  function moveTo(raw, opts) {
    scrub(raw);
    arm(opts);
  }

  function nearest(raw) {
    let best = null, bestDist = Infinity;
    for (const s of segments) {
      const dist = raw < s.start_time ? s.start_time - raw
                 : raw > s.end_time ? raw - s.end_time : 0;
      if (dist < bestDist) { best = s; bestDist = dist; }
    }
    return best;
  }

  function settle() {
    settleTimer = null;
    selected = nearest(center);
    playBtn.hidden = !selected;
    onSettle?.(selected);
  }

  // Called by the player as the video advances. Ignored while the user is
  // touching the bar or a move is still waiting to settle.
  function follow(raw) {
    if (drag || settleTimer || playTimer) return;
    center = Math.min(dayEnd, Math.max(dayStart, raw));
    render();
    const seg = nearest(center);
    if (seg !== selected) {
      selected = seg;
      onSettle?.(seg);
    }
  }

  // Drag / click-on-strip / wheel.
  let drag = null;
  bar.addEventListener("pointerdown", (ev) => {
    if (ev.target.closest(".tl-play")) return;
    drag = { x: ev.clientX, center, moved: false, target: ev.target };
    bar.setPointerCapture(ev.pointerId);
    bar.classList.add("dragging");
  }, { signal });
  bar.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const dx = ev.clientX - drag.x;
    if (Math.abs(dx) > CLICK_SLOP_PX) drag.moved = true;
    if (drag.moved) scrub(drag.center - dx / pps); // nothing plays until release
  }, { signal });
  const endDrag = () => {
    if (!drag) return;
    // A plain click on an event strip jumps the playhead to its start.
    if (!drag.moved && drag.target.classList?.contains("tl-ev")) moveTo(Number(drag.target.dataset.start));
    else if (drag.moved) arm();
    drag = null;
    bar.classList.remove("dragging");
  };
  bar.addEventListener("pointerup", endDrag, { signal });
  bar.addEventListener("pointercancel", endDrag, { signal });
  bar.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    moveTo(center + (ev.deltaX || ev.deltaY) / pps * 0.5);
  }, { signal, passive: false });

  playBtn.addEventListener("click", () => { clearTimeout(playTimer); playTimer = null; onPlay?.(center); }, { signal });

  const ro = new ResizeObserver(layout);
  ro.observe(bar);
  signal.addEventListener("abort", () => { ro.disconnect(); clearTimeout(settleTimer); clearTimeout(playTimer); });

  // Start on the most recent recording of the day.
  const latest = segments.reduce((a, s) => (s.start_time > a ? s.start_time : a), dayStart + DAY_SEC / 2);
  layout();
  moveTo(latest, { autoplay: false });

  return {
    follow,
    destroy() { ctl.abort(); container.innerHTML = ""; },
  };
}
