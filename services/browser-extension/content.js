// SubSphere 1-Click Extension Auto-Pairing Content Script
// Runs only on local SubSphere web app origins to relay explicit user consent pair requests.
(() => {
  try {
    window.postMessage(
      { source: "subsphere-extension", type: "SUBSPHERE_EXTENSION_READY" },
      window.location.origin
    );
  } catch {
    // Ignore non-origin pages
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const msg = event.data;
    if (!msg || msg.source !== "subsphere-web" || msg.type !== "SUBSPHERE_ONE_CLICK_PAIR") {
      return;
    }
    if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.sendMessage) {
      return;
    }
    chrome.runtime.sendMessage(
      {
        type: "PAIR",
        apiBaseUrl: msg.apiBaseUrl || window.location.origin,
        pairCode: msg.pairCode,
        allowlist: Array.isArray(msg.allowlist) ? msg.allowlist : [],
        deviceName: "chrome-one-click"
      },
      (res) => {
        window.postMessage(
          {
            source: "subsphere-extension",
            type: "SUBSPHERE_PAIR_RESULT",
            ok: Boolean(res && res.ok),
            error: res && res.error ? res.error : undefined
          },
          window.location.origin
        );
      }
    );
  });
})();
