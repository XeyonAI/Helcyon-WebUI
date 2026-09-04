// Font-only theme scaling. This deliberately does not use CSS zoom: zoom
// changes the geometry of the whole page, including padding, controls and
// fixed-position layout. Explicit font sizes keep their existing ratios;
// inherited text continues to follow its already-scaled parent.
(function () {
  'use strict';

  const root = document.documentElement;
  const BASE_SIZE_VAR = '--hwui-font-size-base';
  const MARKER = 'data-hwui-font-scale';
  const SKIP_TAGS = new Set(['BR', 'HR', 'IMG', 'LINK', 'META', 'SCRIPT', 'STYLE', 'SVG', 'VIDEO']);
  let scheduled = false;

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      applyToCurrentDom();
    });
  }

  function applyToCurrentDom() {
    const body = document.body;
    if (!body) return;

    // Capture each authored/computed font size once. The rule below then
    // multiplies only that value, so margins, padding, borders and dimensions
    // remain untouched by the setting.
    const elements = [body, ...body.querySelectorAll('*')];
    for (const element of elements) {
      if (!(element instanceof HTMLElement) || SKIP_TAGS.has(element.tagName)) continue;
      if (element.hasAttribute(MARKER)) continue;

      const computed = getComputedStyle(element);
      const size = parseFloat(computed.fontSize);
      if (!Number.isFinite(size)) continue;

      const parent = element.parentElement;
      const parentSize = parent ? parseFloat(getComputedStyle(parent).fontSize) : NaN;
      const hasOwnSize = element === body ||
        Boolean(element.style.fontSize) ||
        !Number.isFinite(parentSize) ||
        Math.abs(size - parentSize) > 0.01;

      // Elements that inherit their parent's font size need no override: the
      // parent's scaled size already flows into them naturally.
      if (!hasOwnSize) continue;
      element.style.setProperty(BASE_SIZE_VAR, `${size}px`);
      element.setAttribute(MARKER, '');
    }
  }

  function start() {
    const style = document.createElement('style');
    style.id = 'hwui-font-scale-style';
    style.textContent = `[${MARKER}] { font-size: calc(var(${BASE_SIZE_VAR}) * var(--app-text-scale, 1)) !important; }`;
    document.head.appendChild(style);
    applyToCurrentDom();

    // Chat messages, menus and modal contents are often created after load.
    // Mark only newly-added elements; the browser applies scale changes to all
    // existing marked elements automatically through the custom property.
    new MutationObserver((mutations) => {
      if (mutations.some(mutation => mutation.addedNodes.length > 0)) schedule();
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
