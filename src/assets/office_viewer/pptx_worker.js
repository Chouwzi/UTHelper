"use strict";
importScripts("./pptx_engine.js");

let presentation = null;
let slides = [];

self.onmessage = async (event) => {
  const msg = event.data || {};
  try {
    if (msg.type === "load") {
      const src = new URL(String(msg.srcUrl || ""), self.location.href);
      if (src.origin !== self.location.origin || !/^\/[0-9a-f]{16}$/.test(src.pathname) || !src.searchParams.get("token")) {
        throw new Error("Địa chỉ tài liệu không hợp lệ.");
      }
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      const timer = controller ? setTimeout(() => controller.abort(), 12000) : null;
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
      const svg = UthPptx.renderSlideToSvg(presentation, slides[0]);
      const notes = UthPptx.getSlideNotes(slides[0]) || "";
      self.postMessage({ type: "loaded", count: slides.length, index: 0, svg, notes });
      return;
    }
    if (msg.type === "render") {
      if (!presentation || !slides.length) {
        throw new Error("Bài trình chiếu chưa sẵn sàng.");
      }
      const index = Math.max(0, Math.min(slides.length - 1, Number(msg.index) || 0));
      const svg = UthPptx.renderSlideToSvg(presentation, slides[index]);
      const notes = UthPptx.getSlideNotes(slides[index]) || "";
      self.postMessage({ type: "slide", index, svg, notes });
    }
  } catch (error) {
    const message = error && error.name === "AbortError"
      ? "Quá thời gian tải bài trình chiếu."
      : (error instanceof Error ? error.message : String(error || "Không thể hiển thị bài trình chiếu."));
    self.postMessage({ type: "error", message });
  }
};
