async function renderPopup() {
  const state = await chrome.storage.local.get([
    "enabled",
    "paused",
    "paired",
    "apiBaseUrl",
    "dashboardUrl",
    "allowlist",
    "lastSyncAt",
    "lastSyncError",
    "pendingBatches",
    "buffered"
  ]);

  document.getElementById("apiUrl").value = state.apiBaseUrl || "http://127.0.0.1:8080";
  document.getElementById("allowlist").value = (state.allowlist || ["chatgpt.com", "claude.ai", "huggingface.co"]).join(", ");

  const badge = document.getElementById("statusBadge");
  const toggleBtn = document.getElementById("togglePause");
  if (!state.paired) {
    badge.textContent = "Not paired";
    badge.className = "badge badge-off";
    toggleBtn.textContent = "Paused";
  } else if (state.paused || !state.enabled) {
    badge.textContent = "Paused";
    badge.className = "badge badge-warn";
    toggleBtn.textContent = "Resume Tracking";
  } else {
    badge.textContent = "Connected";
    badge.className = "badge badge-ok";
    toggleBtn.textContent = "Pause Tracking";
  }

  const bufferedCount = Object.keys(state.buffered || {}).length;
  const pendingCount = (state.pendingBatches || []).length;
  const syncText = state.lastSyncAt ? state.lastSyncAt.slice(0, 19).replace("T", " ") : "never";
  document.getElementById("syncMeta").textContent =
    `Last sync: ${syncText} | Buffered: ${bufferedCount + pendingCount}` +
    (state.lastSyncError ? ` | Err: ${state.lastSyncError}` : "");
}

function parseAllowlistInput() {
  return document
    .getElementById("allowlist")
    .value.split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

document.addEventListener("DOMContentLoaded", async () => {
  await renderPopup();
  const statusEl = document.getElementById("status");

  document.getElementById("pairBtn").addEventListener("click", async () => {
    const apiBaseUrl = document.getElementById("apiUrl").value.trim() || "http://127.0.0.1:8080";
    const pairCode = document.getElementById("pairCode").value.trim();
    if (!pairCode) {
      statusEl.textContent = "Enter the Pair Code generated in SubSphere.";
      return;
    }
    statusEl.textContent = "Pairing extension...";
    chrome.runtime.sendMessage(
      { type: "PAIR", apiBaseUrl, pairCode, allowlist: parseAllowlistInput(), deviceName: "chrome-desktop" },
      async (res) => {
        if (res && res.ok) {
          document.getElementById("pairCode").value = "";
          statusEl.textContent = "Paired and active!";
        } else {
          statusEl.textContent = (res && res.error) || "Pairing failed";
        }
        await renderPopup();
      }
    );
  });

  document.getElementById("saveAllow").addEventListener("click", async () => {
    const allowlist = parseAllowlistInput();
    const apiBaseUrl = document.getElementById("apiUrl").value.trim() || "http://127.0.0.1:8080";
    await chrome.storage.local.set({ allowlist, apiBaseUrl });
    statusEl.textContent = `Saved ${allowlist.length} allowlisted domain(s).`;
    await renderPopup();
  });

  document.getElementById("togglePause").addEventListener("click", async () => {
    const cur = await chrome.storage.local.get(["paused", "enabled", "paired"]);
    if (!cur.paired) {
      statusEl.textContent = "Pair extension first.";
      return;
    }
    const nextPaused = !cur.paused;
    await chrome.storage.local.set({ paused: nextPaused, enabled: !nextPaused });
    statusEl.textContent = nextPaused ? "Tracking paused." : "Tracking resumed.";
    await renderPopup();
  });

  document.getElementById("syncNow").addEventListener("click", async () => {
    statusEl.textContent = "Syncing aggregates...";
    chrome.runtime.sendMessage({ type: "SYNC_NOW" }, async (res) => {
      statusEl.textContent = res && res.ok
        ? `Synced ${res.accepted || 0} record(s).`
        : `Sync error: ${(res && res.error) || "unknown"}`;
      await renderPopup();
    });
  });

  document.getElementById("importHist").addEventListener("click", async () => {
    const granted = await chrome.permissions.request({ permissions: ["history"] });
    if (!granted) {
      statusEl.textContent = "History permission declined; continuing live tracking only.";
      return;
    }
    statusEl.textContent = "Scanning allowlisted domains from local history...";
    chrome.runtime.sendMessage({ type: "IMPORT_HISTORY", days: 30 }, async (res) => {
      if (res && res.ok) {
        statusEl.textContent = `Imported ${res.data.imported || 0} history day-aggregates.`;
      } else {
        statusEl.textContent = (res && res.error) || "History import failed";
      }
      await renderPopup();
    });
  });

  document.getElementById("openDash").addEventListener("click", async () => {
    const s = await chrome.storage.local.get(["dashboardUrl", "apiBaseUrl"]);
    const url = s.dashboardUrl || (s.apiBaseUrl || "http://localhost:8080").replace(":8000", ":8080");
    chrome.tabs.create({ url });
  });

  document.getElementById("openOpts").addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
  });
});
