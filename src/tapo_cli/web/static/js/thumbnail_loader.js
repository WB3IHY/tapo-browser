// Load recording-segment thumbnails only for rows actually visible on
// screen, and only once scrolling has settled — never the whole list, and
// never everything scrolled past during a fast flick.
//
// This camera's media-session mechanism (thumbnails and direct playback
// both open one) appears to have a low tolerance for many camera
// connections opened in a short window — confirmed by two real incidents
// where eagerly loading a whole day's thumbnails (200-300+ segments) took
// the camera offline for the official Android app too, surviving a reboot
// each time. See CLAUDE.md. Server-side pacing (a shared limiter with a
// cooldown between sessions) already exists as one layer of defense; this
// is the other: bound total client-side demand to what a human actually
// scrolled to and paused on, with a hard cap requiring explicit action to
// exceed, and a forced minimum gap between requests.
const SETTLE_DELAY_MS = 400;
const LOAD_GAP_MS = 1000;
const MAX_AUTO_THUMBNAILS = 25;
// A ~6-7 row viewport plus this preload buffer covers what the user is
// about to scroll to next, so a small scroll doesn't retrigger a fresh
// settle -> queue cycle for a couple of newly-revealed rows.
const PRELOAD_BELOW = 4;

export function createThumbnailLoader(scrollContainer) {
  const pending = [];
  const seen = new WeakSet();
  let settleTimer = null;
  let queueRunning = false;
  let loadedCount = 0;
  let capped = false;
  let cappedCallback = null;

  function isVisible(el) {
    const c = scrollContainer.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return r.bottom > c.top && r.top < c.bottom;
  }

  function isBelowViewport(el) {
    const c = scrollContainer.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return r.top >= c.bottom;
  }

  function evaluate() {
    if (capped) return;
    let preloadBudget = PRELOAD_BELOW;
    for (const img of scrollContainer.querySelectorAll(".seg-thumb[data-src]")) {
      if (seen.has(img)) continue;
      let shouldLoad = isVisible(img);
      if (!shouldLoad && preloadBudget > 0 && isBelowViewport(img)) {
        shouldLoad = true;
        preloadBudget--;
      }
      if (!shouldLoad) continue;
      // Only mark as seen once actually queued — otherwise the image that
      // trips the cap (and any others reached in the same pass) would be
      // permanently skipped, even after resumeAfterCap() gives it another try.
      if (loadedCount >= MAX_AUTO_THUMBNAILS) {
        capped = true;
        cappedCallback?.();
        return;
      }
      seen.add(img);
      loadedCount++;
      pending.push(img);
    }
    if (!queueRunning) runQueue();
  }

  async function runQueue() {
    queueRunning = true;
    while (pending.length) {
      const img = pending.shift();
      img.src = img.dataset.src;
      await new Promise((resolve) => setTimeout(resolve, LOAD_GAP_MS));
    }
    queueRunning = false;
  }

  function onScrollOrResize() {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(evaluate, SETTLE_DELAY_MS);
  }

  scrollContainer.addEventListener("scroll", onScrollOrResize);
  window.addEventListener("resize", onScrollOrResize);

  return {
    // Call once right after new segment rows are added to the DOM, and any
    // time the visible set might have changed for a reason other than a
    // scroll/resize event this loader already listens for.
    settle: onScrollOrResize,
    onCapped(cb) {
      cappedCallback = cb;
    },
    // Give the next batch (up to MAX_AUTO_THUMBNAILS) an explicit go-ahead —
    // called from a user action (e.g. a "Load more previews" click), never
    // automatically.
    resumeAfterCap() {
      capped = false;
      loadedCount = 0;
      evaluate();
    },
    destroy() {
      clearTimeout(settleTimer);
      scrollContainer.removeEventListener("scroll", onScrollOrResize);
      window.removeEventListener("resize", onScrollOrResize);
      pending.length = 0;
    },
  };
}
