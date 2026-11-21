"use strict";
(async () => {
  const container = document.getElementById("document");
  function fitPages() {
    const wrapper = container.querySelector(".docx-wrapper");
    if (!wrapper) return;
    wrapper.style.zoom = "1";
    const pages = [...wrapper.querySelectorAll("section.docx")];
    const width = Math.max(1, ...pages.map(page => page.offsetWidth));
    wrapper.style.zoom = String(Math.min(1, Math.max(0.1, (container.clientWidth - 24) / width)));
  }
  try {
    const data = await uthViewer.loadBytes();
    await docx.renderAsync(data, container, null, {
      ignoreWidth: false, ignoreHeight: false, ignoreFonts: false,
      breakPages: true, ignoreLastRenderedPageBreak: false,
      renderHeaders: true, renderFooters: true, renderFootnotes: true,
      renderEndnotes: true, renderAltChunks: false, useBase64URL: true,
      experimental: false,
    });
    // docx-preview preserves OOXML equations as MathML where supported by the WebView.
    fitPages();
    new ResizeObserver(fitPages).observe(container);
    await document.fonts.ready;
    fitPages();
    uthViewer.ready();
  } catch (error) { uthViewer.fail(error); }
})();
