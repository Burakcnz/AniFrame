"use strict";

const DEFAULTS = {
  backendUrl: "http://localhost:8000",
  category: "auto",
  quality: "best",
  autoDownload: false,
  notifications: true,
  adFilter: true,
  wsReconnect: true,
  debugLogs: false,
};

let settings = { ...DEFAULTS };

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  document.getElementById("btnSave").onclick = saveSettings;
  document.getElementById("btnReset").onclick = resetSettings;
  document.getElementById("btnClear").onclick = clearCache;
  checkConnection();
});

async function loadSettings() {
  try {
    const storage = await chrome.storage.local.get("extension_settings");
    settings = { ...DEFAULTS, ...(storage.extension_settings || {}) };
  } catch { settings = { ...DEFAULTS }; }

  document.getElementById("backendUrl").value = settings.backendUrl;
  document.getElementById("category").value = settings.category;
  document.getElementById("quality").value = settings.quality;
  document.getElementById("autoDownload").checked = settings.autoDownload;
  document.getElementById("notifications").checked = settings.notifications;
  document.getElementById("adFilter").checked = settings.adFilter;
  document.getElementById("wsReconnect").checked = settings.wsReconnect;
  document.getElementById("debugLogs").checked = settings.debugLogs;
}

async function saveSettings() {
  settings.backendUrl = document.getElementById("backendUrl").value.trim() || DEFAULTS.backendUrl;
  settings.category = document.getElementById("category").value;
  settings.quality = document.getElementById("quality").value;
  settings.autoDownload = document.getElementById("autoDownload").checked;
  settings.notifications = document.getElementById("notifications").checked;
  settings.adFilter = document.getElementById("adFilter").checked;
  settings.wsReconnect = document.getElementById("wsReconnect").checked;
  settings.debugLogs = document.getElementById("debugLogs").checked;

  try {
    await chrome.storage.local.set({ extension_settings: settings });
    const msg = document.getElementById("saveMsg");
    msg.style.display = "block";
    setTimeout(() => { msg.style.display = "none"; }, 3000);
  } catch {}
}

function resetSettings() {
  settings = { ...DEFAULTS };
  document.getElementById("backendUrl").value = settings.backendUrl;
  document.getElementById("category").value = settings.category;
  document.getElementById("quality").value = settings.quality;
  document.getElementById("autoDownload").checked = settings.autoDownload;
  document.getElementById("notifications").checked = settings.notifications;
  document.getElementById("adFilter").checked = settings.adFilter;
  document.getElementById("wsReconnect").checked = settings.wsReconnect;
  document.getElementById("debugLogs").checked = settings.debugLogs;
}

async function clearCache() {
  try {
    const keys = await chrome.storage.local.get(null);
    const toRemove = Object.keys(keys).filter(k => k.startsWith("tab_") || k === "sent_urls" || k === "local_queue");
    if (toRemove.length) await chrome.storage.local.remove(toRemove);
  } catch {}
  document.getElementById("saveMsg").textContent = "✅ Önbellek temizlendi";
  document.getElementById("saveMsg").style.display = "block";
  setTimeout(() => { document.getElementById("saveMsg").style.display = "none"; }, 3000);
}

async function checkConnection() {
  const status = document.getElementById("connStatus");
  try {
    const res = await fetch(settings.backendUrl + "/api/extension/status");
    const data = await res.json();
    status.className = "status-msg ok";
    status.textContent = "🟢 Backend bağlı — " + settings.backendUrl;
  } catch {
    status.className = "status-msg warn";
    status.textContent = "🟡 Backend bağlantı hatası — AniFrame'i başlatın";
  }
}
