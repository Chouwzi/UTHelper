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
  const MAX_CACHE = 64;

  const SYMBOL_FONT_RE = /\b(wingdings(?:\s*[23])?|webdings|symbol|monotype\s+sorts|mt\s+extra|zapfdingbats|bookshelf\s+symbol(?:\s*7)?)\b/i;
  const WINGDINGS_MAP = {
    n: "\u25A0",
    l: "\u25CF",
    m: "\u274D",
    o: "\u25A1",
    p: "\u25A1",
    q: "\u2751",
    r: "\u2751",
    s: "\u25AA",
    t: "\u2756",
    u: "\u2756",
    v: "\u2756",
    w: "\u25AA",
    x: "\u2612",
    F: "\u261E",
    H: "\u261E",
    "\u00A7": "\u25AA",
    "\u00A8": "\u25CB",
    "\u00B7": "\u2022",
    "\u00D8": "\u27A2",
    "\u00DC": "\u27B2",
    "\u00E0": "\u2794",
    "\u00E8": "\u2794",
    "\u00F0": "\u2794",
    "\u00FB": "\u2717",
    "\u00FC": "\u2713",
    "\u00FE": "\u2611",
  };
  const SYMBOL_MAP = {
    "\u00B7": "\u2022",
    o: "\u25CB",
    "-": "\u2212",
    "\u00A7": "\u2663",
    "\u00A8": "\u2666",
    "\u00A9": "\u2665",
    "\u00AA": "\u2660",
    "\u00AE": "\u2192",
    "\u00DE": "\u21D2",
  };
  const WEBDINGS_MAP = {
    "=": "\u25A0",
    "<": "\u25A0",
    g: "\u25A0",
    n: "\u25CF",
    a: "\u2713",
    r: "\u2717",
    "4": "\u25B8",
    "8": "\u25B8",
  };

  function mapSymbolGlyph(ch, fontHint) {
    const code = ch.charCodeAt(0);
    const wasPua = code >= 0xF020 && code <= 0xF0FF;
    const low = wasPua ? String.fromCharCode(code - 0xF000) : ch;
    const fh = String(fontHint || "").toLowerCase();
    const isSym = SYMBOL_FONT_RE.test(fh);
    if (!wasPua && !isSym) return ch;
    if (/\bsymbol\b/.test(fh) && !/bookshelf/.test(fh)) {
      return SYMBOL_MAP[low] || WINGDINGS_MAP[low] || "\u2022";
    }
    if (/webdings/.test(fh)) {
      return WEBDINGS_MAP[low] || WINGDINGS_MAP[low] || "\u2022";
    }
    if (/wingdings\s*[23]|monotype\s+sorts|zapfdingbats/.test(fh)) {
      if (low === "P" || low === "Q") return "\u2713";
      if (low === "O" || low === "N") return "\u2717";
      return WINGDINGS_MAP[low] || "\u25B8";
    }
    return WINGDINGS_MAP[low] || SYMBOL_MAP[low] || (low === "n" ? "\u25A0" : "\u2022");
  }

  function convertSymbolString(text, fontHint) {
    if (!text) return text;
    let out = "";
    for (const ch of text) {
      if (/\s/.test(ch)) {
        out += ch;
      } else {
        out += mapSymbolGlyph(ch, fontHint);
      }
    }
    return out;
  }

  function normalizeSlideTypographyAndFallbacks(root) {
    for (const g of [...root.querySelectorAll('[data-pptx-fallback="graphicFrame"]')]) {
      const hasRichChild = Boolean(g.querySelector("foreignObject, table, image, path"));
      if (!hasRichChild) {
        g.remove();
        continue;
      }
      g.removeAttribute("data-pptx-fallback");
      for (const child of [...g.children]) {
        const tag = (child.localName || "").toLowerCase();
        if (tag === "title" || (tag === "text" && (child.textContent || "").trim() === "graphicFrame")) {
          child.remove();
        } else if (tag === "rect" && child.hasAttribute("stroke-dasharray")) {
          child.remove();
        }
      }
    }

    for (const el of root.querySelectorAll("span, text, tspan")) {
      const styleFont = (el.style && el.style.fontFamily) || el.getAttribute("font-family") || "";
      const txt = el.textContent || "";
      const hasPua = /[\uF020-\uF0FF]/.test(txt);
      const isSymFont = SYMBOL_FONT_RE.test(styleFont);
      if ((isSymFont || hasPua) && el.children.length === 0 && txt.trim()) {
        el.textContent = convertSymbolString(txt, styleFont);
        if (el.style && isSymFont) {
          el.style.fontFamily = '"Calibri", "Segoe UI", Arial, sans-serif';
        }
        if (isSymFont && el.hasAttribute("font-family")) {
          el.setAttribute("font-family", "Calibri, Arial, sans-serif");
        }
      }
    }

    for (const fo of root.querySelectorAll("foreignObject")) {
      fo.setAttribute("overflow", "visible");
      const wrap = fo.firstElementChild;
      if (wrap && wrap.style) {
        const origH = wrap.style.height;
        if (origH && origH !== "auto") {
          wrap.style.minHeight = origH;
          wrap.style.height = "auto";
        }
        wrap.style.overflow = "visible";
      }
    }

    for (const p of root.querySelectorAll("[data-pptx-paragraph]")) {
      if (!p.style) continue;
      const rawLh = (p.style.lineHeight || "").trim();
      if (!rawLh || rawLh === "normal") {
        p.style.lineHeight = "1.28";
      } else if (/^[\d.]+$/.test(rawLh)) {
        const v = parseFloat(rawLh);
        if (v > 0) {
          p.style.lineHeight = String(Math.max(1.26, Math.round(v * 1.24 * 100) / 100));
        }
      } else if (/^[\d.]+%$/.test(rawLh)) {
        const v = parseFloat(rawLh) / 100;
        if (v > 0) {
          p.style.lineHeight = String(Math.max(1.26, Math.round(v * 1.24 * 100) / 100));
        }
      } else if (/^[\d.]+px$/i.test(rawLh)) {
        const px = parseFloat(rawLh);
        const fs = parseFloat(p.style.fontSize || "0");
        if (px > 0 && fs > 0 && px < fs * 1.24) {
          p.style.lineHeight = `${Math.round(fs * 1.26 * 10) / 10}px`;
        }
      }
      for (const sp of p.querySelectorAll("span")) {
        if (!sp.style) continue;
        const slh = (sp.style.lineHeight || "").trim();
        if (!slh) continue;
        if (/^[\d.]+$/.test(slh)) {
          const sv = parseFloat(slh);
          if (sv > 0 && sv < 1.26) sp.style.lineHeight = "inherit";
        } else if (/^[\d.]+%$/.test(slh)) {
          const sv = parseFloat(slh);
          if (sv > 0 && sv < 126) sp.style.lineHeight = "inherit";
        } else if (/^[\d.]+px$/i.test(slh)) {
          const spx = parseFloat(slh);
          const sfs = parseFloat(sp.style.fontSize || "0");
          if (spx > 0 && sfs > 0 && spx < sfs * 1.24) {
            sp.style.lineHeight = `${Math.round(sfs * 1.26 * 10) / 10}px`;
          }
        }
      }
    }
  }

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
    normalizeSlideTypographyAndFallbacks(parsed.documentElement);
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

  function ensureParsedEntry(entry) {
    if (!entry.svgRoot && typeof entry.svg === "string") {
      entry.svgRoot = sanitizeSvgElement(entry.svg);
      entry.svg = null;
    }
    return entry;
  }

  function displayEntry(slideIndex, entry) {
    if (slideIndex !== index) return;
    ensureParsedEntry(entry);
    host.replaceChildren(document.importNode(entry.svgRoot, true));
    position.textContent = `${index + 1} / ${slideCount}`;
    prev.disabled = index === 0;
    next.disabled = index === slideCount - 1;
    notes.textContent = entry.notes || "";
    toggle.disabled = !notes.textContent;
  }

  function createCanvasMeasureText() {
    let ctx = null;
    try {
      const cvs = document.createElement("canvas");
      ctx = cvs.getContext("2d");
    } catch (_) {
      return undefined;
    }
    if (!ctx) return undefined;
    return (text, style) => {
      const family = (style && style.family) || "Calibri, Arial, sans-serif";
      const sizePx = (style && Number(style.sizePx)) || 16;
      ctx.font = `${style && style.italic ? "italic" : "normal"} ${style && style.bold ? "bold" : "normal"} ${sizePx}px ${family}`;
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
  const fallbackMeasureText = createCanvasMeasureText();

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
      const opts = fallbackMeasureText ? { measureText: fallbackMeasureText } : {};
      const svg = UthPptx.renderSlideToSvg(fallbackPres, fallbackSlides[targetIndex], opts);
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

  let touchStartX = null;
  let touchStartY = null;
  host.addEventListener("touchstart", (e) => {
    if (e.touches && e.touches.length === 1) {
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
    } else {
      touchStartX = null;
    }
  }, { passive: true });
  host.addEventListener("touchend", (e) => {
    if (touchStartX === null || !e.changedTouches || !e.changedTouches.length) return;
    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - (touchStartY || 0);
    touchStartX = null;
    touchStartY = null;
    if (Math.abs(dx) >= 48 && Math.abs(dx) > Math.abs(dy) * 1.4) {
      move(dx < 0 ? 1 : -1);
    }
  }, { passive: true });

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
      watchdog = setTimeout(() => failLoad(new Error("Quá thời gian dựng bài trình chiếu.")), 24000);
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
          } else if (msg.type === "prewarmed") {
            const slideIdx = Number(msg.index) || 0;
            if (!lruCache.has(slideIdx) && typeof msg.svg === "string") {
              const lazyEntry = { svg: msg.svg, svgRoot: null, notes: msg.notes || "" };
              cachePut(slideIdx, lazyEntry);
              if (slideIdx === index) {
                displayEntry(slideIdx, lazyEntry);
              }
            }
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
