"use strict";
(async () => {
  try {
    const presentation = await UthPptx.loadPresentation(new Uint8Array(await uthViewer.loadBytes()));
    const slides = UthPptx.getSlides(presentation);
    if (!slides.length || slides.length > 200) throw new Error("Không có trang chiếu hoặc tệp vượt quá giới hạn 200 trang.");
    let index = 0;
    const host = document.getElementById("slide");
    const prev = document.getElementById("prev"), next = document.getElementById("next");
    const notes = document.getElementById("notes"), toggle = document.getElementById("notes-toggle");
    function showSlide() {
      // SVG retains slide aspect ratio, authored shape positions and master/layout geometry.
      const svg = UthPptx.renderSlideToSvg(presentation, slides[index]);
      const parsed = new DOMParser().parseFromString(svg, "image/svg+xml");
      if (parsed.querySelector("parsererror")) throw new Error("Không hiển thị được trang chiếu.");
      for (const node of parsed.querySelectorAll("script, iframe, object, embed, animate, set")) node.remove();
      for (const node of parsed.querySelectorAll("*")) {
        for (const attr of [...node.attributes]) {
          const val = String(attr.value || "");
          if (/^on/i.test(attr.name) || (/href$/i.test(attr.name) && (!/^(#|data:image\/|blob:)/i.test(val) || /^data:image\/svg\+xml/i.test(val)))) node.removeAttribute(attr.name);
        }
      }
      host.replaceChildren(document.importNode(parsed.documentElement, true));
      document.getElementById("position").textContent = `${index + 1} / ${slides.length}`;
      prev.disabled = index === 0; next.disabled = index === slides.length - 1;
      notes.textContent = UthPptx.getSlideNotes(slides[index]) || "";
      toggle.disabled = !notes.textContent;
    }
    function move(delta) {
      index = Math.max(0, Math.min(slides.length - 1, index + delta));
      try { showSlide(); } catch (error) { uthViewer.fail(error); }
    }
    prev.onclick = () => move(-1); next.onclick = () => move(1);
    toggle.onclick = () => { notes.hidden = !notes.hidden; toggle.setAttribute("aria-expanded", String(!notes.hidden)); };
    document.addEventListener("keydown", event => {
      if (event.key === "ArrowRight" || event.key === "PageDown") { event.preventDefault(); move(1); }
      if (event.key === "ArrowLeft" || event.key === "PageUp") { event.preventDefault(); move(-1); }
    });
    showSlide(); uthViewer.ready();
  } catch (error) { uthViewer.fail(error); }
})();
