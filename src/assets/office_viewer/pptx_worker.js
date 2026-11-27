"use strict";
importScripts("./pptx_engine.js");

let presentation = null;
let slides = [];
const renderedSlides = new Map();
const MAX_WORKER_CACHE = 64;
let prewarmTimer = null;
let prewarmCursor = 1;

function createOffscreenMeasureText() {
  if (typeof OffscreenCanvas !== "function") return undefined;
  let ctx = null;
  try {
    ctx = new OffscreenCanvas(1, 1).getContext("2d");
  } catch (_) {
    return undefined;
  }
  if (!ctx) return undefined;
  return (text, style) => {
    const family = (style && style.family) || "Calibri, Arial, sans-serif";
    const sizePx = (style && Number(style.sizePx)) || 16;
    ctx.font = `${style && style.italic ? "italic" : "normal"} ${style && style.bold ? "bold" : "normal"} ${sizePx}px ${family}`;
    if ("fontKerning" in ctx) {
      ctx.fontKerning = style && style.kerning === false ? "none" : "normal";
    }
    if ("letterSpacing" in ctx) {
      ctx.letterSpacing = `${(style && Number(style.letterSpacingPx)) || 0}px`;
    }
    const m = ctx.measureText(String(text || ""));
    return {
      widthPx: m.width,
      ascentPx: m.fontBoundingBoxAscent ?? sizePx * 0.8,
      descentPx: m.fontBoundingBoxDescent ?? sizePx * 0.22,
      lineGapPx: 0,
      inkAscentPx: m.actualBoundingBoxAscent ?? sizePx * 0.76,
      inkDescentPx: m.actualBoundingBoxDescent ?? sizePx * 0.22,
    };
  };
}

const workerMeasureText = createOffscreenMeasureText();
const renderOptions = workerMeasureText ? { measureText: workerMeasureText } : {};

function renderSlideCached(index) {
  if (renderedSlides.has(index)) {
    const hit = renderedSlides.get(index);
    renderedSlides.delete(index);
    renderedSlides.set(index, hit);
    return hit;
  }
  const svg = UthPptx.renderSlideToSvg(presentation, slides[index], renderOptions);
  const notes = UthPptx.getSlideNotes(slides[index]) || "";
  const entry = { svg, notes };
  renderedSlides.set(index, entry);
  while (renderedSlides.size > MAX_WORKER_CACHE) {
    const oldest = renderedSlides.keys().next().value;
    renderedSlides.delete(oldest);
  }
  return entry;
}

function schedulePrewarm() {
  if (prewarmTimer !== null || !presentation || slides.length <= 1) return;
  const limit = Math.min(slides.length, MAX_WORKER_CACHE);
  if (prewarmCursor >= limit) return;
  prewarmTimer = setTimeout(() => {
    prewarmTimer = null;
    if (!presentation || prewarmCursor >= limit) return;
    while (prewarmCursor < limit && renderedSlides.has(prewarmCursor)) {
      prewarmCursor += 1;
    }
    if (prewarmCursor >= limit) return;
    const idx = prewarmCursor++;
    try {
      const { svg, notes } = renderSlideCached(idx);
      self.postMessage({ type: "prewarmed", index: idx, svg, notes });
    } catch (_) {}
    if (prewarmCursor < limit) {
      schedulePrewarm();
    }
  }, 8);
}

self.onmessage = async (event) => {
  const msg = event.data || {};
  try {
    if (msg.type === "load") {
      if (prewarmTimer !== null) {
        clearTimeout(prewarmTimer);
        prewarmTimer = null;
      }
      renderedSlides.clear();
      prewarmCursor = 1;
      const src = new URL(String(msg.srcUrl || ""), self.location.href);
      if (src.origin !== self.location.origin || !/^\/[0-9a-f]{16}$/.test(src.pathname) || !src.searchParams.get("token")) {
        throw new Error("Địa chỉ tài liệu không hợp lệ.");
      }
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      const timer = controller ? setTimeout(() => controller.abort(), 24000) : null;
      let buf;
      try {
        const res = await fetch(src.href, {
          credentials: "omit",
          redirect: "error",
          signal: controller ? controller.signal : undefined,
        });
        if (!res.ok) throw new Error("Không tải được tài liệu. Hãy mở lại tệp.");
        buf = await res.arrayBuffer();
      } finally {
        if (timer !== null) clearTimeout(timer);
      }
      if (buf.byteLength > 32 * 1024 * 1024) {
        throw new Error("Tệp vượt quá giới hạn xem trước.");
      }
      presentation = await UthPptx.loadPresentation(new Uint8Array(buf), {
        limits: {
          maxEntries: 5000,
          maxTotalUncompressedBytes: 96 * 1024 * 1024,
          maxXmlPartBytes: 16 * 1024 * 1024,
          maxTotalXmlBytes: 48 * 1024 * 1024,
        },
      });
      slides = UthPptx.getSlides(presentation);
      if (!slides.length || slides.length > 200) {
        throw new Error("Không có trang chiếu hoặc tệp vượt quá giới hạn 200 trang.");
      }
      const { svg, notes } = renderSlideCached(0);
      self.postMessage({ type: "loaded", count: slides.length, index: 0, svg, notes });
      schedulePrewarm();
      return;
    }
    if (msg.type === "render") {
      if (!presentation || !slides.length) {
        throw new Error("Bài trình chiếu chưa sẵn sàng.");
      }
      const index = Math.max(0, Math.min(slides.length - 1, Number(msg.index) || 0));
      const { svg, notes } = renderSlideCached(index);
      self.postMessage({ type: "slide", index, svg, notes });
      if (index + 1 < slides.length && !renderedSlides.has(index + 1)) {
        prewarmCursor = Math.min(prewarmCursor, index + 1);
        schedulePrewarm();
      }
    }
  } catch (error) {
    const message = error && error.name === "AbortError"
      ? "Quá thời gian tải bài trình chiếu."
      : (error instanceof Error ? error.message : String(error || "Không thể hiển thị bài trình chiếu."));
    self.postMessage({ type: "error", message });
  }
};
