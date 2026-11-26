"use strict";
(() => {
  const host = document.getElementById("slide");
  const prev = document.getElementById("prev");
  const next = document.getElementById("next");
  const notes = document.getElementById("notes");
  const toggle = document.getElementById("notes-toggle");
  const position = document.getElementById("position");
  const params = new URLSearchParams(location.search);

  let slideCount = 0;
  let index = 0;
  let worker = null;
  let fallbackPres = null;
  let fallbackSlides = [];
  const lruCache = new Map();
  const MAX_CACHE = 5;

  function sanitizeSvgElement(svg) {
    const parsed = new DOMParser().parseFromString(svg, "image/svg+xml");
    if (parsed.querySelector("parsererror")) throw new Error("Không hiển thị được trang chiếu.");
    for (const node of parsed.querySelectorAll("script, iframe, object, embed, animate, set")) node.remove();
    for (const node of parsed.querySelectorAll("*")) {
      for (const attr of [...node.attributes]) {
        const val = String(attr.value || "");
        if (/^on/i.test(attr.name) || (/href$/i.test(attr.name) && (!/^(#|data:image\/|blob:)/i.test(val) || /^data:image\/svg\+xml/i.test(val)))) {
          node.removeAttribute(attr.name);
        }
      }
    }
    return parsed.documentElement;
  }

  function cachePut(slideIndex, entry) {
    if (lruCache.has(slideIndex)) lruCache.delete(slideIndex);
    lruCache.set(slideIndex, entry);
    while (lruCache.size > MAX_CACHE) {
      const oldestKey = lruCache.keys().next().value;
      lruCache.delete(oldestKey);
    }
  }

  function displayEntry(slideIndex, entry) {
    if (slideIndex !== index) return;
    host.replaceChildren(document.importNode(entry.svgRoot, true));
    position.textContent = `${index + 1} / ${slideCount}`;
    prev.disabled = index === 0;
    next.disabled = index === slideCount - 1;
    notes.textContent = entry.notes || "";
    toggle.disabled = !notes.textContent;
  }

  function requestSlide(targetIndex) {
    const cached = lruCache.get(targetIndex);
    if (cached) {
      lruCache.delete(targetIndex);
      lruCache.set(targetIndex, cached);
      displayEntry(targetIndex, cached);
      return;
    }
    if (worker) {
      worker.postMessage({ type: "render", index: targetIndex });
    } else if (fallbackPres && fallbackSlides.length) {
      const svg = UthPptx.renderSlideToSvg(fallbackPres, fallbackSlides[targetIndex]);
      const entry = {
        svgRoot: sanitizeSvgElement(svg),
        notes: UthPptx.getSlideNotes(fallbackSlides[targetIndex]) || "",
      };
      cachePut(targetIndex, entry);
      displayEntry(targetIndex, entry);
    }
  }

  function move(delta) {
    if (!slideCount) return;
    index = Math.max(0, Math.min(slideCount - 1, index + delta));
    try { requestSlide(index); } catch (error) { uthViewer.fail(error); }
  }

  prev.onclick = () => move(-1);
  next.onclick = () => move(1);
  toggle.onclick = () => {
    notes.hidden = !notes.hidden;
    toggle.setAttribute("aria-expanded", String(!notes.hidden));
  };
  document.addEventListener("keydown", event => {
    if (event.key === "ArrowRight" || event.key === "PageDown") { event.preventDefault(); move(1); }
    if (event.key === "ArrowLeft" || event.key === "PageUp") { event.preventDefault(); move(-1); }
  });

  if (typeof Worker === "function") {
    let watchdog = null;
    let settled = false;
    function failLoad(err) {
      if (settled) return;
      settled = true;
      if (watchdog !== null) clearTimeout(watchdog);
      if (worker) {
        try { worker.terminate(); } catch (_) {}
        worker = null;
      }
      uthViewer.fail(err);
    }
    try {
      const src = new URL(params.get("src") || "", location.href);
      if (src.origin !== location.origin || !/^\/[0-9a-f]{16}$/.test(src.pathname) || !src.searchParams.get("token")) {
        throw new Error("Địa chỉ tài liệu không hợp lệ.");
      }
      worker = new Worker("./pptx_worker.js");
      watchdog = setTimeout(() => failLoad(new Error("Quá thời gian dựng bài trình chiếu.")), 12000);
      worker.onmessage = (event) => {
        const msg = event.data || {};
        try {
          if (msg.type === "loaded") {
            if (watchdog !== null) clearTimeout(watchdog);
            settled = true;
            slideCount = Number(msg.count) || 0;
            const entry = { svgRoot: sanitizeSvgElement(msg.svg), notes: msg.notes || "" };
            cachePut(0, entry);
            displayEntry(0, entry);
            uthViewer.ready();
          } else if (msg.type === "slide") {
            const slideIdx = Number(msg.index) || 0;
            const entry = { svgRoot: sanitizeSvgElement(msg.svg), notes: msg.notes || "" };
            cachePut(slideIdx, entry);
            displayEntry(slideIdx, entry);
          } else if (msg.type === "error") {
            failLoad(new Error(msg.message || "Không thể hiển thị bài trình chiếu."));
          }
        } catch (err) {
          failLoad(err);
        }
      };
      worker.onerror = (event) => {
        failLoad(new Error((event && event.message) || "Lỗi Web Worker trình chiếu."));
      };
      worker.postMessage({ type: "load", srcUrl: src.href });
    } catch (err) {
      failLoad(err);
    }
    return;
  }

  (async () => {
    try {
      fallbackPres = await UthPptx.loadPresentation(new Uint8Array(await uthViewer.loadBytes()));
      fallbackSlides = UthPptx.getSlides(fallbackPres);
      if (!fallbackSlides.length || fallbackSlides.length > 200) {
        throw new Error("Không có trang chiếu hoặc tệp vượt quá giới hạn 200 trang.");
      }
      slideCount = fallbackSlides.length;
      requestSlide(0);
      uthViewer.ready();
    } catch (error) {
      uthViewer.fail(error);
    }
  })();
})();
