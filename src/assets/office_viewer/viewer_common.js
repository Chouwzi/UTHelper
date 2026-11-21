"use strict";
window.uthViewer = (() => {
  const status = document.getElementById("status");
  const params = new URLSearchParams(location.search);
  function setTheme(theme) {
    document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
  }
  setTheme(params.get("theme"));
  window.setTheme = setTheme;
  // Accept only this server's authenticated preview bytes, never a remote URL.
  async function loadBytes() {
    const src = new URL(params.get("src") || "", location.href);
    if (src.origin !== location.origin || !/^\/[0-9a-f]{16}$/.test(src.pathname) || !src.searchParams.get("token")) {
      throw new Error("Địa chỉ tài liệu không hợp lệ.");
    }
    const response = await fetch(src.href, { credentials: "omit", redirect: "error" });
    if (!response.ok) throw new Error("Không tải được tài liệu. Hãy mở lại tệp.");
    const data = await response.arrayBuffer();
    if (data.byteLength > 32 * 1024 * 1024) throw new Error("Tệp vượt quá giới hạn xem trước.");
    return data;
  }
  function ready() { status.hidden = true; document.documentElement.dataset.ready = "true"; }
  function fail(error) {
    status.hidden = false;
    status.textContent = error instanceof Error ? error.message : "Không thể xem trước tệp. Bạn có thể lưu bản gốc.";
    status.setAttribute("role", "alert");
    document.documentElement.dataset.ready = "error";
  }
  // Opening the original in another app remains an explicit header action.
  document.addEventListener("click", event => {
    if (event.target.closest("a")) event.preventDefault();
  });
  return { loadBytes, ready, fail, setTheme };
})();
