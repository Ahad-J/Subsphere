// SubSphere v4.1 Chrome MV3 Privacy-Safe Activity Observer
// Captures ONLY allowlisted domain name + active focused seconds + visit count per day.
// NEVER captures full URLs, paths, query strings, prompts, DOM text, titles, or cookies.

const DEFAULT_STATE = {
  enabled: false,
  paused: false,
  paired: false,
  apiBaseUrl: "http://127.0.0.1:8080",
  dashboardUrl: "http://127.0.0.1:8080",
  accessToken: "",
  deviceName: "chrome-desktop",
  allowlist: ["chatgpt.com", "claude.ai", "huggingface.co"],
  activeDomain: null,
  activeStartedAt: null,
  isIdle: false,
  isWindowFocused: true,
  pendingBatches: [],
  buffered: {},
  lastSyncAt: null,
  lastSyncError: null,
  batchCounter: 1
};

async function loadState() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULT_STATE));
  return { ...DEFAULT_STATE, ...stored };
}

function extractAllowlistedDomain(rawUrl, allowlist) {
  if (!rawUrl || (!rawUrl.startsWith("http://") && !rawUrl.startsWith("https://"))) {
    return null;
  }
  try {
    const host = new URL(rawUrl).hostname.toLowerCase().replace(/^www\./, "");
    const matched = (allowlist || []).find(
      (allowed) => host === allowed.toLowerCase() || host.endsWith("." + allowed.toLowerCase())
    );
    return matched ? host : null;
  } catch {
    return null;
  }
}

function todayIsoDate(nowMs = Date.now()) {
  return new Date(nowMs).toISOString().slice(0, 10);
}

function closeActiveSegmentInState(state, nowMs = Date.now()) {
  const buffered = { ...(state.buffered || {}) };
  if (state.activeDomain && state.activeStartedAt && state.enabled && !state.paused && !state.isIdle && state.isWindowFocused) {
    const deltaSec = Math.max(1, Math.round((nowMs - state.activeStartedAt) / 1000));
    const day = todayIsoDate(nowMs);
    const key = `${day}|${state.activeDomain}`;
    const cur = buffered[key] || { day, domain: state.activeDomain, seconds: 0, visits: 0 };
    buffered[key] = {
      day,
      domain: state.activeDomain,
      seconds: cur.seconds + deltaSec,
      visits: cur.visits
    };
  }
  return {
    ...state,
    buffered,
    activeDomain: null,
    activeStartedAt: null
  };
}

async function recordTick(tabUrl, nowMs = Date.now()) {
  const state = await loadState();
  if (!state.enabled || state.paused || state.isIdle || !state.isWindowFocused) {
    const closed = closeActiveSegmentInState(state, nowMs);
    await chrome.storage.local.set({
      buffered: closed.buffered,
      activeDomain: null,
      activeStartedAt: null
    });
    return;
  }

  let updated = closeActiveSegmentInState(state, nowMs);
  const domain = extractAllowlistedDomain(tabUrl, state.allowlist);
  if (domain && domain !== state.activeDomain) {
    const day = todayIsoDate(nowMs);
    const key = `${day}|${domain}`;
    const cur = updated.buffered[key] || { day, domain, seconds: 0, visits: 0 };
    updated.buffered[key] = {
      day,
      domain,
      seconds: cur.seconds,
      visits: cur.visits + 1
    };
  }

  await chrome.storage.local.set({
    activeDomain: domain,
    activeStartedAt: domain ? nowMs : null,
    buffered: updated.buffered
  });
}

function buildPendingBatchFromBuffered(state, nowMs = Date.now()) {
  const closed = closeActiveSegmentInState(state, nowMs);
  const keys = Object.keys(closed.buffered || {});
  if (keys.length === 0) {
    return { records: [], remainingBuffered: {}, nextCounter: state.batchCounter || 1 };
  }
  let counter = state.batchCounter || 1;
  const records = [];
  for (const key of keys) {
    const entry = closed.buffered[key];
    if (!entry || (entry.seconds <= 0 && entry.visits <= 0)) continue;
    records.push({
      record_id: `${entry.day}:${entry.domain}:chunk-${counter}`,
      day: entry.day,
      domain: entry.domain,
      active_seconds: entry.seconds,
      visits: Math.max(1, entry.visits || 1),
      coverage: "live_focused"
    });
    counter += 1;
  }
  return { records, remainingBuffered: {}, nextCounter: counter };
}

async function syncPendingBatches() {
  const state = await loadState();
  if (!state.accessToken) {
    return { ok: false, error: "Not paired with SubSphere account" };
  }
  const built = buildPendingBatchFromBuffered(state);
  const pendingBatches = [...(state.pendingBatches || [])];
  if (built.records.length > 0) {
    pendingBatches.push({
      id: `batch-${Date.now()}`,
      records: built.records
    });
    await chrome.storage.local.set({
      buffered: {},
      activeStartedAt: state.activeDomain ? Date.now() : null,
      batchCounter: built.nextCounter,
      pendingBatches
    });
  }

  if (pendingBatches.length === 0) {
    return { ok: true, accepted: 0, pending: 0 };
  }

  const remaining = [];
  let totalAccepted = 0;
  let lastErr = null;
  for (const batch of pendingBatches) {
    try {
      const resp = await fetch(`${state.apiBaseUrl}/api/v1/personal/activity/sync`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${state.accessToken}`
        },
        body: JSON.stringify({
          source_device: state.deviceName || "chrome-desktop",
          records: batch.records
        })
      });
      if (resp.ok) {
        const data = await resp.json().catch(() => ({}));
        totalAccepted += Number(data.accepted || batch.records.length);
      } else {
        const errBody = await resp.json().catch(() => ({}));
        lastErr = errBody.message || `HTTP ${resp.status}`;
        remaining.push(batch);
      }
    } catch (err) {
      lastErr = String(err && err.message ? err.message : err);
      remaining.push(batch);
    }
  }

  const syncTs = new Date().toISOString();
  await chrome.storage.local.set({
    pendingBatches: remaining,
    lastSyncAt: totalAccepted > 0 || remaining.length === 0 ? syncTs : state.lastSyncAt,
    lastSyncError: lastErr
  });
  return { ok: remaining.length === 0, accepted: totalAccepted, pending: remaining.length, error: lastErr };
}

async function pairWithCode(apiBaseUrl, pairCode, allowlist, deviceName) {
  const cleanUrl = (apiBaseUrl || "http://127.0.0.1:8080").replace(/\/+$/, "");
  const cleanAllow = (allowlist && allowlist.length ? allowlist : ["chatgpt.com", "claude.ai", "huggingface.co"])
    .map((d) => String(d).trim().toLowerCase())
    .filter(Boolean);
  const resp = await fetch(`${cleanUrl}/api/v1/personal/extension/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pair_code: String(pairCode || "").trim(),
      device_name: deviceName || "chrome-desktop",
      allowlist: cleanAllow,
      permissions: ["tabs", "storage", "alarms", "idle"]
    })
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.message || `Pairing failed (${resp.status})`);
  }
  await chrome.storage.local.set({
    apiBaseUrl: cleanUrl,
    accessToken: data.extension_token,
    allowlist: data.allowlist || cleanAllow,
    paired: true,
    enabled: true,
    paused: false,
    lastSyncError: null
  });
  return data;
}

function summarizeHistoryItems(items, allowlist) {
  const grouped = {};
  for (const item of items || []) {
    const domain = extractAllowlistedDomain(item.url, allowlist);
    if (!domain) continue;
    const day = item.lastVisitTime
      ? new Date(item.lastVisitTime).toISOString().slice(0, 10)
      : todayIsoDate();
    const key = `hist:${day}:${domain}`;
    if (!grouped[key]) {
      grouped[key] = {
        record_id: key,
        day,
        domain,
        visits: 0,
        active_seconds: null,
        coverage: "history_import_may_include_synced"
      };
    }
    grouped[key].visits += Math.max(1, Number(item.visitCount || 1));
  }
  return Object.values(grouped);
}

async function importAllowlistedHistory(days = 30) {
  const state = await loadState();
  if (!state.accessToken) {
    throw new Error("Pair the extension before importing visit history.");
  }
  if (!chrome.history) {
    throw new Error("History permission not granted.");
  }
  const startTime = Date.now() - Math.max(1, Number(days)) * 86400 * 1000;
  const items = await chrome.history.search({ text: "", startTime, maxResults: 5000 });
  const summarized = summarizeHistoryItems(items, state.allowlist);
  if (summarized.length === 0) {
    return { imported: 0, message: "No allowlisted domains found in browser history." };
  }
  const resp = await fetch(`${state.apiBaseUrl}/api/v1/personal/activity/sync`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${state.accessToken}`
    },
    body: JSON.stringify({
      source_device: `${state.deviceName || "chrome-desktop"}-history`,
      records: summarized
    })
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.message || `History sync failed (${resp.status})`);
  }
  await chrome.storage.local.set({ lastSyncAt: new Date().toISOString(), lastSyncError: null });
  return { imported: data.accepted || 0, duplicates_skipped: data.duplicates_skipped || 0 };
}

async function refreshActiveTab() {
  if (!chrome.tabs) return;
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true }).catch(() => []);
  await recordTick(tabs && tabs[0] ? tabs[0].url : null);
}

if (typeof chrome !== "undefined" && chrome.runtime) {
  chrome.runtime.onInstalled.addListener(async () => {
    const current = await chrome.storage.local.get(null);
    if (typeof current.enabled === "undefined") {
      await chrome.storage.local.set(DEFAULT_STATE);
    }
    if (chrome.idle) chrome.idle.setDetectionInterval(60);
    if (chrome.alarms) chrome.alarms.create("subsphere-flush", { periodInMinutes: 1 });
  });

  chrome.runtime.onStartup.addListener(async () => {
    if (chrome.idle) chrome.idle.setDetectionInterval(60);
    if (chrome.alarms) chrome.alarms.create("subsphere-flush", { periodInMinutes: 1 });
    await refreshActiveTab();
  });

  chrome.tabs.onActivated.addListener(async ({ tabId }) => {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    await recordTick(tab ? tab.url : null);
  });

  chrome.tabs.onUpdated.addListener(async (_tabId, changeInfo, tab) => {
    if (tab && tab.active && changeInfo.url) {
      await recordTick(changeInfo.url);
    }
  });

  if (chrome.windows && chrome.windows.onFocusChanged) {
    chrome.windows.onFocusChanged.addListener(async (windowId) => {
      const focused = windowId !== chrome.windows.WINDOW_ID_NONE;
      const state = await loadState();
      if (!focused) {
        const closed = closeActiveSegmentInState(state);
        await chrome.storage.local.set({
          isWindowFocused: false,
          activeDomain: null,
          activeStartedAt: null,
          buffered: closed.buffered
        });
      } else {
        await chrome.storage.local.set({ isWindowFocused: true });
        await refreshActiveTab();
      }
    });
  }

  if (chrome.idle && chrome.idle.onStateChanged) {
    chrome.idle.onStateChanged.addListener(async (newState) => {
      const isIdle = newState !== "active";
      const state = await loadState();
      if (isIdle) {
        const closed = closeActiveSegmentInState(state);
        await chrome.storage.local.set({
          isIdle: true,
          activeDomain: null,
          activeStartedAt: null,
          buffered: closed.buffered
        });
      } else {
        await chrome.storage.local.set({ isIdle: false });
        await refreshActiveTab();
      }
    });
  }

  if (chrome.alarms) {
    chrome.alarms.onAlarm.addListener(async (alarm) => {
      if (alarm.name === "subsphere-flush") {
        await syncPendingBatches();
      }
    });
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "PAIR") {
      pairWithCode(msg.apiBaseUrl, msg.pairCode, msg.allowlist, msg.deviceName)
        .then((res) => sendResponse({ ok: true, data: res }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;
    }
    if (msg && msg.type === "SYNC_NOW") {
      syncPendingBatches()
        .then((res) => sendResponse(res))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;
    }
    if (msg && msg.type === "IMPORT_HISTORY") {
      importAllowlistedHistory(msg.days || 30)
        .then((res) => sendResponse({ ok: true, data: res }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;
    }
    return false;
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    extractAllowlistedDomain,
    closeActiveSegmentInState,
    buildPendingBatchFromBuffered,
    summarizeHistoryItems
  };
}
