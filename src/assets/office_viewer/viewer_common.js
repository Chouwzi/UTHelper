"use strict";
window.uthViewer = (() => {
  const status = document.getElementById("status");
  const params = new URLSearchParams(location.search);
  function _emitHostMessage(payload) {
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
  function ready() {
    status.hidden = true;
    document.documentElement.dataset.ready = "true";
    _emitHostMessage({ type: "ready" });
  }
  function fail(error) {
    status.hidden = false;
    const message = error instanceof Error
      ? error.message
      : (typeof error === "string" && error ? error : "Không thể xem trước tệp. Bạn có thể lưu bản gốc.");
    status.textContent = message;
    status.setAttribute("role", "alert");
    document.documentElement.dataset.ready = "error";
    _emitHostMessage({ type: "error", message });
  }
  // Opening the original in another app remains an explicit header action.
  document.addEventListener("click", event => {
    if (event.target.closest("a")) event.preventDefault();
  });
  return { loadBytes, ready, fail, setTheme };
})();
