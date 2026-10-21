// Load recording-segment thumbnails only for rows actually visible on
// screen (plus a small look-ahead), and only once scrolling has settled.
//
// The server fills its thumbnail cache from the camera's own stored images
// in a few batched sessions when a day's segments are listed (see
// tapo/thumbnail_cache.py), so these requests are normally cheap disk reads
// and need no cap. Loading stays viewport-bound anyway: it keeps the number
// of requests held open at once small, and covers the rare segment the
// camera has no stored image for, which the server decodes on demand
// (slowly, and within its own per-camera budget).
const SETTLE_DELAY_MS = 150;
const LOAD_GAP_MS = 40;
// A ~6-7 row viewport plus this preload buffer covers what the user is
// about to scroll to next.
const PRELOAD_BELOW = 8;

export function createThumbnailLoader(scrollContainer) {
  const pending = [];
  const seen = new WeakSet();
  let settleTimer = null;
  let queueRunning = false;

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
    let preloadBudget = PRELOAD_BELOW;
    for (const img of scrollContainer.querySelectorAll(".seg-thumb[data-src]")) {
      if (seen.has(img)) continue;
      let shouldLoad = isVisible(img);
      if (!shouldLoad && preloadBudget > 0 && isBelowViewport(img)) {
        shouldLoad = true;
        preloadBudget--;
      }
      if (!shouldLoad) continue;
      seen.add(img);
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
    destroy() {
      clearTimeout(settleTimer);
      scrollContainer.removeEventListener("scroll", onScrollOrResize);
      window.removeEventListener("resize", onScrollOrResize);
      pending.length = 0;
    },
  };
}
