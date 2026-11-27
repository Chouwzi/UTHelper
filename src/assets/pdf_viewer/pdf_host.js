import * as pdfjsLib from "./pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "./pdf.worker.min.mjs";

const statusEl = document.getElementById("status");
const pagesEl = document.getElementById("pages");
const outlineBtn = document.getElementById("outline-btn");
const outlineDrawer = document.getElementById("outline-drawer");
const prevBtn = document.getElementById("prev-btn");
const nextBtn = document.getElementById("next-btn");
const pageInput = document.getElementById("page-input");
const pageTotalEl = document.getElementById("page-total");
const zoomOutBtn = document.getElementById("zoom-out-btn");
const zoomResetBtn = document.getElementById("zoom-reset-btn");
const zoomInBtn = document.getElementById("zoom-in-btn");
const searchToggleBtn = document.getElementById("search-toggle-btn");
const searchBar = document.getElementById("search-bar");
const searchInput = document.getElementById("search-input");
const searchCountEl = document.getElementById("search-count");
const searchPrevBtn = document.getElementById("search-prev-btn");
const searchNextBtn = document.getElementById("search-next-btn");
const searchCloseBtn = document.getElementById("search-close-btn");

const params = new URLSearchParams(location.search);

function emitHostMessage(payload) {
  const json = JSON.stringify(payload);
  try {
    console.info("__UTH_MSG__:" + json);
  } catch (_) {}
  try {
    if (window.chrome && window.chrome.webview && typeof window.chrome.webview.postMessage === "function") {
      window.chrome.webview.postMessage(json);
    }
  } catch (_) {}
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
}
setTheme(params.get("theme"));
window.setTheme = setTheme;

document.addEventListener("click", (event) => {
  if (event.target.closest("a")) {
    event.preventDefault();
  }
});

let readyEmitted = false;
function markReady() {
  statusEl.hidden = true;
  document.documentElement.dataset.ready = "true";
  if (!readyEmitted) {
    readyEmitted = true;
    emitHostMessage({ type: "ready" });
  }
}

function markError(error) {
  const message = error instanceof Error ? error.message : String(error || "Không thể hiển thị PDF.");
  statusEl.hidden = false;
  statusEl.textContent = message;
  statusEl.setAttribute("role", "alert");
  document.documentElement.dataset.ready = "error";
  emitHostMessage({ type: "error", message });
}

let pdfDoc = null;
let totalPages = 0;
let currentPage = 1;
let userZoom = 1.0;
const pageSlots = new Map();
const renderedPages = new Map();
const renderingPages = new Map();
const inflightRenders = new Map();
const visiblePages = new Set();
let renderGeneration = 0;
let windowScheduleTimer = null;
let baseViewportSize = { width: 595, height: 842 };
let intersectionObserver = null;
let searchHits = [];
let searchHitIndex = -1;
let searchSeq = 0;
let textCache = null;

function computeFitWidthScale(pageWidth) {
  const containerW = Math.max(240, (pagesEl.clientWidth || window.innerWidth || 360) - 16);
  return Math.max(0.25, Math.min(4.0, (containerW / Math.max(1, pageWidth)) * userZoom));
}

function computeTargetDpr() {
  const rawDpr = window.devicePixelRatio || 1;
  const maxDpr = window.innerWidth < 600 && userZoom <= 1.05 ? 1.5 : 2.0;
  return Math.min(rawDpr, maxDpr);
}

function updateNavControls() {
  prevBtn.disabled = currentPage <= 1;
  nextBtn.disabled = currentPage >= totalPages;
  pageInput.value = String(currentPage);
  pageTotalEl.textContent = `/ ${totalPages}`;
  zoomResetBtn.textContent = `${Math.round(userZoom * 100)}%`;
}

function updateSearchControls() {
  if (!searchHits.length) {
    searchCountEl.textContent = "0/0";
    searchPrevBtn.disabled = true;
    searchNextBtn.disabled = true;
  } else {
    searchCountEl.textContent = `${searchHitIndex + 1}/${searchHits.length}`;
    searchPrevBtn.disabled = false;
    searchNextBtn.disabled = false;
  }
  for (const [pNum, slot] of pageSlots.entries()) {
    const active = searchHits.length > 0 && searchHits[searchHitIndex] === pNum;
    slot.classList.toggle("search-hit", active);
  }
}

function isSlotOnScreen(pNum) {
  const slot = pageSlots.get(pNum);
  if (!slot) return false;
  const rect = slot.getBoundingClientRect();
  const winH = window.innerHeight || document.documentElement.clientHeight || 720;
  return rect.bottom > 44 && rect.top < winH;
}

function pickBestVisiblePage() {
  const winH = window.innerHeight || document.documentElement.clientHeight || 720;
  const topEdge = 44;
  let bestPage = null;
  let bestPixels = 0;
  const sorted = [...visiblePages].sort((a, b) => a - b);
  for (const pNum of sorted) {
    const slot = pageSlots.get(pNum);
    if (!slot) continue;
    const rect = slot.getBoundingClientRect();
    const overlap = Math.min(rect.bottom, winH) - Math.max(rect.top, topEdge);
    if (overlap > bestPixels + 2) {
      bestPixels = overlap;
      bestPage = pNum;
    }
  }
  return bestPage;
}

function cancelDistantInflightRenders(anchorPage) {
  for (const [pNum, active] of inflightRenders.entries()) {
    if (Math.abs(pNum - anchorPage) > 2 && !isSlotOnScreen(pNum)) {
      if (active.renderTask) {
        try { active.renderTask.cancel(); } catch (_) {}
      }
      if (active.textLayer) {
        try { active.textLayer.cancel(); } catch (_) {}
      }
      inflightRenders.delete(pNum);
      renderingPages.delete(pNum);
    }
  }
}

function releaseFarPages(anchorPage) {
  for (const [pNum, state] of renderedPages.entries()) {
    if (Math.abs(pNum - anchorPage) > 8 && !visiblePages.has(pNum)) {
      if (state.renderTask) {
        try { state.renderTask.cancel(); } catch (_) {}
      }
      if (state.textLayer) {
        try { state.textLayer.cancel(); } catch (_) {}
      }
      const slot = pageSlots.get(pNum);
      if (slot) {
        for (const cvs of slot.querySelectorAll("canvas")) {
          cvs.width = 0;
          cvs.height = 0;
        }
        slot.replaceChildren();
      }
      renderedPages.delete(pNum);
    }
  }
}

async function renderPage(pNum) {
  if (!pdfDoc || pNum < 1 || pNum > totalPages) return;
  const slot = pageSlots.get(pNum);
  if (!slot) return;
  const existing = renderedPages.get(pNum);
  const scale = computeFitWidthScale(Number(slot.dataset.baseWidth || baseViewportSize.width));
  if (existing && Math.abs(existing.scale - scale) < 0.01) {
    return;
  }
  if (renderingPages.has(pNum)) return;
  const myGen = renderGeneration;
  renderingPages.set(pNum, myGen);
  let committed = false;

  try {
    const page = await pdfDoc.getPage(pNum);
    if (myGen !== renderGeneration || renderingPages.get(pNum) !== myGen) return;
    const baseVp = page.getViewport({ scale: 1.0 });
    slot.dataset.baseWidth = String(baseVp.width);
    slot.dataset.baseHeight = String(baseVp.height);

    const effScale = computeFitWidthScale(baseVp.width);
    const viewport = page.getViewport({ scale: effScale });
    const dpr = computeTargetDpr();

    slot.style.width = `${Math.round(viewport.width)}px`;
    slot.style.height = `${Math.round(viewport.height)}px`;
    slot.style.setProperty("--scale-factor", String(viewport.scale));

    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.floor(viewport.width * dpr));
    canvas.height = Math.max(1, Math.floor(viewport.height * dpr));
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }

    const textDiv = document.createElement("div");
    textDiv.className = "textLayer";

    if (existing) {
      if (existing.renderTask) {
        try { existing.renderTask.cancel(); } catch (_) {}
      }
      if (existing.textLayer) {
        try { existing.textLayer.cancel(); } catch (_) {}
      }
      renderedPages.delete(pNum);
    }

    slot.replaceChildren(canvas, textDiv);

    const renderContext = {
      canvasContext: ctx,
      viewport,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
    };
    const renderTask = page.render(renderContext);
    const activeState = { gen: myGen, scale: effScale, renderTask, textLayer: null };
    inflightRenders.set(pNum, activeState);

    await renderTask.promise;
    if (myGen !== renderGeneration || inflightRenders.get(pNum) !== activeState) return;
    committed = true;
    renderedPages.set(pNum, activeState);
    markReady();

    if (typeof pdfjsLib.TextLayer === "function") {
      try {
        const textLayer = new pdfjsLib.TextLayer({
          textContentSource: page.streamTextContent({ includeMarkedContent: true }),
          container: textDiv,
          viewport,
        });
        activeState.textLayer = textLayer;
        await textLayer.render();
        if (myGen !== renderGeneration) return;
        const endDiv = document.createElement("div");
        endDiv.className = "endOfContent";
        textDiv.append(endDiv);
      } catch (_) {}
    }
  } catch (err) {
    if (err && err.name === "RenderingCancelledException") {
      if (!committed) {
        for (const cvs of slot.querySelectorAll("canvas")) {
          cvs.width = 0;
          cvs.height = 0;
        }
        slot.replaceChildren();
      }
      return;
    }
    if (!readyEmitted) {
      markError(err);
    }
  } finally {
    const curActive = inflightRenders.get(pNum);
    if (curActive && curActive.gen === myGen) {
      inflightRenders.delete(pNum);
    }
    if (renderingPages.get(pNum) === myGen) {
      renderingPages.delete(pNum);
    }
  }
}

async function renderWindowPrioritized(centerPage) {
  const myGen = renderGeneration;
  cancelDistantInflightRenders(centerPage);
  await renderPage(centerPage);
  if (myGen !== renderGeneration || currentPage !== centerPage) return;
  const neighbors = [centerPage + 1, centerPage - 1, centerPage + 2];
  for (const p of neighbors) {
    if (p >= 1 && p <= totalPages) {
      if (myGen !== renderGeneration || Math.abs(currentPage - centerPage) > 1) return;
      renderPage(p);
    }
  }
  releaseFarPages(centerPage);
}

function scheduleWindowRender(centerPage, immediate = false) {
  cancelDistantInflightRenders(centerPage);
  if (windowScheduleTimer !== null) {
    clearTimeout(windowScheduleTimer);
    windowScheduleTimer = null;
  }
  if (immediate) {
    renderWindowPrioritized(centerPage);
    return;
  }
  windowScheduleTimer = setTimeout(() => {
    windowScheduleTimer = null;
    renderWindowPrioritized(currentPage);
  }, 40);
}

function jumpToPage(targetPage) {
  const clamped = Math.max(1, Math.min(totalPages || 1, Number(targetPage) || 1));
  currentPage = clamped;
  updateNavControls();
  const slot = pageSlots.get(clamped);
  if (slot) {
    slot.scrollIntoView({ behavior: "auto", block: "start" });
  }
  scheduleWindowRender(clamped, true);
}

function applyZoom(newZoom) {
  userZoom = Math.max(0.5, Math.min(3.0, Math.round(newZoom * 100) / 100));
  updateNavControls();
  renderGeneration += 1;
  for (const state of inflightRenders.values()) {
    if (state.renderTask) {
      try { state.renderTask.cancel(); } catch (_) {}
    }
    if (state.textLayer) {
      try { state.textLayer.cancel(); } catch (_) {}
    }
  }
  for (const state of renderedPages.values()) {
    if (state.renderTask) {
      try { state.renderTask.cancel(); } catch (_) {}
    }
    if (state.textLayer) {
      try { state.textLayer.cancel(); } catch (_) {}
    }
  }
  for (const [pNum, slot] of pageSlots.entries()) {
    const bw = Number(slot.dataset.baseWidth || baseViewportSize.width);
    const bh = Number(slot.dataset.baseHeight || baseViewportSize.height);
    const scale = computeFitWidthScale(bw);
    slot.style.width = `${Math.round(bw * scale)}px`;
    slot.style.height = `${Math.round(bh * scale)}px`;
    slot.style.setProperty("--scale-factor", String(scale));
  }
  inflightRenders.clear();
  renderedPages.clear();
  renderingPages.clear();
  scheduleWindowRender(currentPage, true);
}

async function runSearch(query) {
  const clean = String(query || "").trim().toLowerCase();
  const mySeq = ++searchSeq;
  searchHits = [];
  searchHitIndex = -1;
  if (!clean || !pdfDoc) {
    updateSearchControls();
    return;
  }
  if (!textCache) {
    textCache = new Array(totalPages).fill(null);
  }
  for (let p = 1; p <= totalPages; p++) {
    if (mySeq !== searchSeq) return;
    if (textCache[p - 1] === null) {
      try {
        const page = await pdfDoc.getPage(p);
        const content = await page.getTextContent();
        textCache[p - 1] = content.items.map((it) => it.str || "").join(" ").toLowerCase();
      } catch (_) {
        textCache[p - 1] = "";
      }
    }
    if (textCache[p - 1].includes(clean)) {
      searchHits.push(p);
    }
  }
  if (mySeq !== searchSeq) return;
  if (searchHits.length > 0) {
    searchHitIndex = 0;
    jumpToPage(searchHits[0]);
  }
  updateSearchControls();
}

async function buildOutline(outline) {
  if (!Array.isArray(outline) || !outline.length) return;
  outlineBtn.hidden = false;
  const fragment = document.createDocumentFragment();

  async function resolvePageNumber(dest) {
    try {
      let resolved = dest;
      if (typeof dest === "string") {
        resolved = await pdfDoc.getDestination(dest);
      }
      if (Array.isArray(resolved) && resolved[0]) {
        const idx = await pdfDoc.getPageIndex(resolved[0]);
        return idx + 1;
      }
    } catch (_) {}
    return null;
  }

  function appendItems(items, depth) {
    for (const item of items) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "outline-item";
      btn.style.paddingLeft = `${10 + Math.min(depth, 4) * 14}px`;
      btn.textContent = String(item.title || "Mục");
      btn.addEventListener("click", async () => {
        const pageNum = await resolvePageNumber(item.dest);
        if (pageNum) {
          jumpToPage(pageNum);
        }
        outlineDrawer.hidden = true;
        outlineBtn.setAttribute("aria-expanded", "false");
      });
      fragment.appendChild(btn);
      if (Array.isArray(item.items) && item.items.length) {
        appendItems(item.items, depth + 1);
      }
    }
  }

  appendItems(outline, 0);
  outlineDrawer.replaceChildren(fragment);
}

outlineBtn.addEventListener("click", () => {
  const open = outlineDrawer.hidden;
  outlineDrawer.hidden = !open;
  outlineBtn.setAttribute("aria-expanded", String(open));
});

prevBtn.addEventListener("click", () => jumpToPage(currentPage - 1));
nextBtn.addEventListener("click", () => jumpToPage(currentPage + 1));
pageInput.addEventListener("change", () => {
  const val = parseInt(pageInput.value, 10);
  if (Number.isFinite(val)) {
    jumpToPage(val);
  } else {
    pageInput.value = String(currentPage);
  }
});

zoomOutBtn.addEventListener("click", () => applyZoom(userZoom - 0.25));
zoomInBtn.addEventListener("click", () => applyZoom(userZoom + 0.25));
zoomResetBtn.addEventListener("click", () => applyZoom(1.0));

searchToggleBtn.addEventListener("click", () => {
  const open = searchBar.hidden;
  searchBar.hidden = !open;
  searchToggleBtn.setAttribute("aria-expanded", String(open));
  if (open) searchInput.focus();
});

searchCloseBtn.addEventListener("click", () => {
  searchBar.hidden = true;
  searchToggleBtn.setAttribute("aria-expanded", "false");
});

searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    runSearch(searchInput.value);
  }
});

searchPrevBtn.addEventListener("click", () => {
  if (!searchHits.length) return;
  searchHitIndex = (searchHitIndex - 1 + searchHits.length) % searchHits.length;
  updateSearchControls();
  jumpToPage(searchHits[searchHitIndex]);
});

searchNextBtn.addEventListener("click", () => {
  if (!searchHits.length) return;
  searchHitIndex = (searchHitIndex + 1) % searchHits.length;
  updateSearchControls();
  jumpToPage(searchHits[searchHitIndex]);
});

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => applyZoom(userZoom), 120);
});

// Touch pinch-to-zoom support
let pinchStartDist = 0;
let pinchStartZoom = 1.0;
pagesEl.addEventListener("touchstart", (e) => {
  if (e.touches && e.touches.length === 2) {
    const dx = e.touches[0].clientX - e.touches[1].clientX;
    const dy = e.touches[0].clientY - e.touches[1].clientY;
    pinchStartDist = Math.hypot(dx, dy);
    pinchStartZoom = userZoom;
  }
}, { passive: true });
pagesEl.addEventListener("touchend", (e) => {
  if (pinchStartDist > 0 && e.changedTouches && e.touches.length < 2) {
    pinchStartDist = 0;
  }
}, { passive: true });
pagesEl.addEventListener("touchmove", (e) => {
  if (pinchStartDist > 0 && e.touches && e.touches.length === 2) {
    const dx = e.touches[0].clientX - e.touches[1].clientX;
    const dy = e.touches[0].clientY - e.touches[1].clientY;
    const dist = Math.hypot(dx, dy);
    if (dist > 0) {
      const target = Math.max(0.5, Math.min(3.0, pinchStartZoom * (dist / pinchStartDist)));
      if (Math.abs(target - userZoom) >= 0.12) {
        applyZoom(target);
      }
    }
  }
}, { passive: true });

(async () => {
  try {
    const src = new URL(params.get("src") || "", location.href);
    if (src.origin !== location.origin || !/^\/[0-9a-f]{16}$/.test(src.pathname) || !src.searchParams.get("token")) {
      throw new Error("Địa chỉ tài liệu không hợp lệ.");
    }

    const loadingTask = pdfjsLib.getDocument({
      url: src.href,
      isEvalSupported: false,
      useSystemFonts: true,
      disableAutoFetch: false,
      disableStream: false,
      rangeChunkSize: 524288,
    });
    pdfDoc = await loadingTask.promise;
    totalPages = pdfDoc.numPages || 0;
    if (!totalPages) {
      throw new Error("Tài liệu PDF không có trang nào.");
    }

    const firstPage = await pdfDoc.getPage(1);
    const vp1 = firstPage.getViewport({ scale: 1.0 });
    baseViewportSize = { width: vp1.width, height: vp1.height };
    const initScale = computeFitWidthScale(vp1.width);
    const initW = Math.round(vp1.width * initScale);
    const initH = Math.round(vp1.height * initScale);

    const frag = document.createDocumentFragment();
    for (let p = 1; p <= totalPages; p++) {
      const slot = document.createElement("section");
      slot.className = "pdf-page";
      slot.dataset.pageNumber = String(p);
      slot.dataset.baseWidth = String(vp1.width);
      slot.dataset.baseHeight = String(vp1.height);
      slot.style.width = `${initW}px`;
      slot.style.height = `${initH}px`;
      slot.setAttribute("aria-label", `Trang ${p}`);
      pageSlots.set(p, slot);
      frag.appendChild(slot);
    }
    pagesEl.replaceChildren(frag);
    updateNavControls();

    if (typeof IntersectionObserver === "function") {
      intersectionObserver = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            const pNum = Number(entry.target.dataset.pageNumber);
            if (entry.isIntersecting) {
              visiblePages.add(pNum);
            } else {
              visiblePages.delete(pNum);
            }
          }
          const bestPage = pickBestVisiblePage();
          if (bestPage !== null && bestPage !== currentPage) {
            currentPage = bestPage;
            updateNavControls();
            scheduleWindowRender(bestPage, false);
          }
        },
        { rootMargin: "350px 0px", threshold: [0, 0.25, 0.6] }
      );
      for (const slot of pageSlots.values()) {
        intersectionObserver.observe(slot);
      }
    }

    await renderPage(1);
    scheduleWindowRender(1, true);
    try {
      const outline = await pdfDoc.getOutline();
      await buildOutline(outline);
    } catch (_) {}
  } catch (err) {
    markError(err);
  }
})();
