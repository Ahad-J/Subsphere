document.addEventListener("DOMContentLoaded", async () => {
  const s = await chrome.storage.local.get(["apiBaseUrl", "dashboardUrl", "deviceName", "allowlist"]);
  document.getElementById("apiUrl").value = s.apiBaseUrl || "http://localhost:8000";
  document.getElementById("dashUrl").value = s.dashboardUrl || "http://localhost:8080";
  document.getElementById("deviceName").value = s.deviceName || "chrome-desktop";
  document.getElementById("allowlist").value = (s.allowlist || ["chatgpt.com", "claude.ai"]).join(", ");

  document.getElementById("saveBtn").addEventListener("click", async () => {
    const apiBaseUrl = document.getElementById("apiUrl").value.trim().replace(/\/+$/, "") || "http://localhost:8000";
    const dashboardUrl = document.getElementById("dashUrl").value.trim().replace(/\/+$/, "") || "http://localhost:8080";
    const deviceName = document.getElementById("deviceName").value.trim() || "chrome-desktop";
    const allowlist = document
      .getElementById("allowlist")
      .value.split(",")
      .map((x) => x.trim().toLowerCase())
      .filter(Boolean);

    await chrome.storage.local.set({ apiBaseUrl, dashboardUrl, deviceName, allowlist });
    document.getElementById("msg").textContent = `Saved (${allowlist.length} allowlisted domains).`;
  });
});
