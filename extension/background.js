"use strict";

// ════════════════════════════════════════════
// AniFrame Background Service Worker v2.0
// Service worker: state management, API comm, WebSocket, context menus
// ════════════════════════════════════════════

importScripts(
  "lib/utils.js",
  "lib/logger.js",
  "lib/state.js",
  "lib/filter.js",
  "lib/parser.js",
  "lib/api.js"
);

const state = new StateManager();
const backend = new BackendClient(state);

// ═══════ WS RELAY ═══════
// WebSocket yalnizca burada (service worker) acik. Icerik betikleri WS acmaz;
// sunucudan gelen push olaylarini iceren betiklere buradan iletiriz.
backend.on("analyze_url", (url) => {
  if (url) {
    AFLog.debug("BG", "WS analyze_url → sekme aciliyor", { url: truncateUrl(url) });
    chrome.tabs.create({ url, active: true });
  }
});

function broadcastToTabs(type, data) {
  chrome.tabs.query({}, (tabs) => {
    for (const t of tabs) {
      if (t.id) chrome.tabs.sendMessage(t.id, { type, data }).catch(() => {});
    }
  });
}

for (const ev of ["download_started", "download_progress", "download_completed", "download_error"]) {
  backend.on(ev, (data) => broadcastToTabs("WS_" + ev.toUpperCase(), data));
}

// ═══════ INSTALL ═══════
chrome.runtime.onInstalled.addListener(async () => {
  AFLog.info("BG", "Extension kuruluyor/v2.0");
  await state.init();
  await backend.init();

  chrome.contextMenus.create({
    id: "aniframe-download",
    title: "🎬 AniFrame ile İndir",
    contexts: ["link", "video", "audio", "image"],
  });
  chrome.contextMenus.create({
    id: "aniframe-download-page",
    title: "🎬 AniFrame ile Tüm Videoları İndir",
    contexts: ["page"],
  });
  chrome.contextMenus.create({
    id: "aniframe-separator",
    type: "separator",
    contexts: ["page", "link", "video"],
  });

  chrome.alarms.create("cleanup", { periodInMinutes: 10 });
  chrome.alarms.create("ws-check", { periodInMinutes: 1 });

  AFLog.info("BG", "Context menu ve alarm'lar olusturuldu");
  AFLog.info("BG", "Backend: " + backend.baseUrl);
});

// ═══════ STARTUP ═══════
chrome.runtime.onStartup.addListener(async () => {
  AFLog.info("BG", "Tarayici baslatildi, state yeniden yukleniyor");
  await state.init();
  await backend.init();
  updateBadge(null, 0);
});

// ═══════ MESSAGES ═══════
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender?.tab?.id || msg.tabId;
  switch (msg.type) {
    case "VIDEO_DETECTED":
      AFLog.debug("BG", "VIDEO_DETECTED mesaji alindi", { tabId, url: truncateUrl(msg.data?.videoUrl) });
      handleVideoDetected(msg.data, tabId);
      break;
    case "DOWNLOAD_REQUEST":
      AFLog.info("BG", "DOWNLOAD_REQUEST", { url: truncateUrl(msg.data?.videoUrl), priority: msg.priority });
      if (msg.data && !msg.data.pageUrl && sender?.tab?.url) msg.data.pageUrl = sender.tab.url;
      handleDownloadRequest(msg.data, msg.priority || "normal", sendResponse);
      return true;
    case "DRM_DETECTED":
      AFLog.warn("BG", "DRM korumali icerik tespit edildi", { hostname: msg.data?.hostname });
      if (tabId) updateBadge(tabId, -1);
      break;
    case "OPEN_TAB":
      if (msg.url) {
        AFLog.debug("BG", "Sekme aciliyor", { url: truncateUrl(msg.url) });
        chrome.tabs.create({ url: msg.url, active: true });
      }
      break;
    case "UPDATE_BADGE":
      updateBadge(tabId, msg.count);
      break;
    case "GET_QUEUE":
      backend.getQueue().then(sendResponse);
      return true;
    case "GET_STATUS":
      backend.getStatus().then(sendResponse);
      return true;
    case "GET_LOGS":
      sendResponse({ entries: AFLog.getEntries(msg.filter), stats: AFLog.getStats() });
      return true;
    case "CLEAR_LOGS":
      AFLog.clear();
      sendResponse({ success: true });
      return true;
    case "EXPORT_LOGS":
      sendResponse({ text: AFLog.exportText(msg.filter), json: AFLog.exportJSON(msg.filter) });
      return true;
  }
  return true;
});

// ═══════ VIDEO DETECTED ═══════
async function handleVideoDetected(data, tabId) {
  const url = data?.videoUrl;
  if (!url) {
    AFLog.warn("BG", "VIDEO_DETECTED: videoUrl bos");
    return;
  }

  if (await state.isUrlSent(url)) {
    AFLog.debug("BG", "URL zaten gonderilmis, atlanıyor", { url: truncateUrl(url) });
    return;
  }
  await state.markUrlSent(url);
  await state.addVideo(tabId, data);

  AFLog.info("BG", "Video algilandi → backend'e gonderiliyor", {
    url: truncateUrl(url),
    type: data.videoType,
    quality: data.quality,
    source: data.source,
    hostname: data.hostname,
  });

  try {
    const result = await backend.detectVideo(data);
    if (result.recorded) {
      data.category = result.category;
      AFLog.info("BG", "Video algilandi (indirme icin tiklama bekleniyor)", { category: result.category });
    } else if (result.error) {
      AFLog.warn("BG", "Backend hata dondurdu", { error: result.error });
    }
  } catch (err) {
    AFLog.error("BG", "Backend baglanti hatasi", { error: err.message });
  }

  if (tabId) {
    chrome.tabs.sendMessage(tabId, { type: "VIDEO_DETECTED", data }).catch(() => {});
  }

  const videos = await state.getVideos(tabId);
  updateBadge(tabId, videos.length);
  AFLog.debug("BG", "Badge guncellendi", { tabId, count: videos.length });
}

// ═══════ COOKIES (YouTube bot-check icin oturum cerezi) ═══════
const YT_COOKIE_DOMAINS = new Set(["youtube.com", ".youtube.com", "www.youtube.com", "google.com", ".google.com"]);

async function getNetscapeCookies(hostname) {
  if (!hostname || !/(^|\.)youtube\.com$|(^|\.)google\.com$/.test(hostname)) return "";
  try {
    const all = [];
    for (const domain of ["youtube.com", "google.com"]) {
      const cookies = await chrome.cookies.getAll({ domain });
      all.push(...cookies);
    }
    const relevant = all.filter(c => YT_COOKIE_DOMAINS.has(c.domain));
    if (!relevant.length) return "";
    const clean = (s) => String(s ?? "").replace(/[\t\r\n]/g, "");
    const lines = ["# Netscape HTTP Cookie File"];
    for (const c of relevant) {
      const includeSub = c.domain.startsWith(".") ? "TRUE" : "FALSE";
      const expiry = c.expirationDate ? Math.floor(c.expirationDate) : 0;
      lines.push([clean(c.domain), includeSub, clean(c.path) || "/", c.secure ? "TRUE" : "FALSE", expiry, clean(c.name), clean(c.value)].join("\t"));
    }
    return lines.join("\n");
  } catch (err) {
    AFLog.warn("BG", "Cerez okuma hatasi", { error: err.message });
    return "";
  }
}

// ═══════ DOWNLOAD REQUEST ═══════
async function handleDownloadRequest(data, priority, sendResponse) {
  const url = data?.videoUrl;
  AFLog.info("BG", "Indirme istegi", { url: truncateUrl(url), priority });

  try {
    const hostname = data?.hostname || (data?.pageUrl ? getDomain(data.pageUrl) : "");
    data.cookiesNetscape = await getNetscapeCookies(hostname);
    if (!data.format) {
      // Kullanicinin ayarlardan sectigi varsayilan kalite (best/1080/720/480/360)
      try {
        const s = await state.getSettings();
        data.format = s.quality || "best";
      } catch { data.format = "best"; }
    }
    const result = await (priority === "high" ? backend.downloadNow(url, data) : backend.download(url, data));
    if (result.id) {
      AFLog.info("BG", "Indirme baslatildi", { id: result.id });
      if (sendResponse) sendResponse({ success: true, id: result.id, status: "queued" });
      chrome.notifications.create("dl_" + result.id, {
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: "AniFrame",
        message: "Video kuyruğa eklendi: " + (data.title || "Video"),
        priority: 1,
      });
    } else {
      AFLog.error("BG", "Indirme basarisiz", { error: result.error });
      if (sendResponse) sendResponse({ success: false, error: result.error || "Unknown error" });
    }
  } catch (err) {
    AFLog.error("BG", "Indirme istisnasi", { error: err.message });
    if (sendResponse) sendResponse({ success: false, error: "Connection failed" });
  }
}

// ═══════ CONTEXT MENU ═══════
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab) return;
  if (info.menuItemId === "aniframe-download") {
    const url = info.srcUrl || info.linkUrl;
    if (url && isVideoExtension(url)) {
      AFLog.info("BG", "Context menu: video indir", { url: truncateUrl(url) });
      handleVideoDetected({
        videoUrl: url,
        pageUrl: info.pageUrl || tab.url,
        title: tab.title || "",
        hostname: getDomain(url),
        videoType: getVideoType(url),
        quality: VideoParser.detectQualityFromURL(url),
      }, tab.id);
      chrome.tabs.sendMessage(tab.id, { type: "DOWNLOAD_REQUEST", data: { videoUrl: url, title: tab.title }, priority: "normal" });
    }
  }
  if (info.menuItemId === "aniframe-download-page") {
    AFLog.info("BG", "Context menu: sayfadaki tum videolari indir");
    chrome.tabs.sendMessage(tab.id, { type: "DOWNLOAD_ALL_REQUEST" });
  }
});

// ═══════ TABS ═══════
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "complete") {
    AFLog.debug("BG", "Sayfa yuklendi, video listesi temizlendi", { tabId });
    state.clearVideos(tabId);
    updateBadge(tabId, 0);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  AFLog.debug("BG", "Sekme kapatildi", { tabId });
  state.removeTabState(tabId);
});

// ═══════ WEB REQUEST - ON BEFORE REQUEST (URL tabanli) ═══════
const MEDIA_HEADERS = ['content-length', 'content-range', 'accept-ranges', 'content-type', 'content-disposition'];

const requestCache = new Map();
const CACHE_MAX = 200;

function _cacheRequest(tabId, info) {
  let arr = requestCache.get(tabId);
  if (!arr) { arr = []; requestCache.set(tabId, arr); }
  arr.push(info);
  while (arr.length > CACHE_MAX) arr.shift();
}

function _getCachedRequests(tabId) {
  return requestCache.get(tabId) || [];
}

function _isMediaMime(ct) {
  if (!ct) return false;
  const lower = ct.toLowerCase();
  if (lower.startsWith('video/') || lower.startsWith('audio/')) return true;
  if (lower.includes('application/x-mpegurl') || lower.includes('application/vnd.apple.mpegurl')) return true;
  if (lower.includes('application/dash+xml') || lower.includes('video/mp2t')) return true;
  if (lower.includes('application/octet-stream') || lower.includes('binary/octet-stream')) return true;
  return false;
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (!details.tabId || details.tabId < 0) return { cancel: false };
    const url = details.url;
    if (!url || url.startsWith("data:") || url.startsWith("blob:")) return { cancel: false };
    if (!isVideoExtension(url) && !isManifestUrl(url)) return { cancel: false };
    if (isSegmentUrl(url)) return { cancel: false };
    if (NON_VIDEO_EXTS.has(getFileExtension(url).replace(".", ""))) return { cancel: false };

    AFLog.debug("BG", "WebRequest: video kaynagi algilandi", { url: truncateUrl(url), type: details.type });

    chrome.tabs.sendMessage(details.tabId, {
      type: "RAW_VIDEO_DETECTED",
      data: { videoUrl: url, source: "webrequest", tabId: details.tabId },
    }).catch(() => {});
    return { cancel: false };
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] },
  []
);

// ═══════ WEB REQUEST - ON HEADERS RECEIVED (MIME/Content-Type tabanli) ═══════
function _onHeadersReceived(details) {
  if (!details.tabId || details.tabId < 0) return;
  const url = details.url || "";
  if (!url || url.startsWith("data:") || url.startsWith("blob:") || url.startsWith("chrome-extension:")) return;

  let contentLength = 0;
  let contentType = "";
  let contentDisposition = "";
  let isMedia = false;

  for (const h of details.responseHeaders || []) {
    const name = (h.name || "").toLowerCase();
    const value = (h.value || "").trim();
    if (!name || !value) continue;

    if (name === "content-type") {
      contentType = value;
      if (_isMediaMime(value)) isMedia = true;
    } else if (name === "content-length") {
      contentLength = parseInt(value) || 0;
    } else if (name === "content-disposition") {
      contentDisposition = value;
    } else if (name === "content-range") {
      isMedia = true;
    }
  }

  const urlLower = url.toLowerCase();
  const hasMediaExt = isVideoExtension(url) || isManifestUrl(url);

  if (!isMedia && !hasMediaExt) return;

  if (isSegmentUrl(url)) return;
  if (NON_VIDEO_EXTS.has(getFileExtension(url).replace(".", ""))) return;

  const info = {
    url: url,
    contentType: contentType,
    contentLength: contentLength,
    timeStamp: details.timeStamp,
    initiator: details.initiator || "",
    tabId: details.tabId,
    source: isMedia ? "mime_header" : "url_pattern",
  };
  _cacheRequest(details.tabId, info);

  if (contentLength > 0 || (hasMediaExt && !details.url.includes("google"))) {
    chrome.tabs.sendMessage(details.tabId, {
      type: "RAW_VIDEO_DETECTED",
      data: {
        videoUrl: url,
        source: info.source,
        tabId: details.tabId,
        contentLength: contentLength,
        contentType: contentType,
      },
    }).catch(() => {});
  }
}

chrome.webRequest.onHeadersReceived.addListener(
  _onHeadersReceived,
  { urls: ["*://*/*"], types: ["media"] },
  ["responseHeaders"]
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (!details.tabId || details.tabId < 0) return;
    const url = (details.url || "").toLowerCase();
    if (url.includes("google") && !url.includes("googlevideo")) return;
    _onHeadersReceived(details);
  },
  { urls: ["*://*/*"], types: ["xmlhttprequest"] },
  ["responseHeaders"]
);

// Mime-watch: video/audio iceren xmlhttprequest'leri yakala
chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (!details.tabId || details.tabId < 0) return;
    const url = details.url || "";
    let hasVideoMime = false;
    for (const h of details.responseHeaders || []) {
      const name = (h.name || "").toLowerCase();
      const value = (h.value || "").trim();
      if (name === "content-type" && (value.startsWith("video/") || value.startsWith("audio/"))) {
        hasVideoMime = true;
        break;
      }
    }
    if (!hasVideoMime || isSegmentUrl(url)) return;
    if (NON_VIDEO_EXTS.has(getFileExtension(url).replace(".", ""))) return;

    _cacheRequest(details.tabId, {
      url: url,
      contentType: details.responseHeaders.find(h => (h.name || "").toLowerCase() === "content-type")?.value || "",
      timeStamp: details.timeStamp,
      initiator: details.initiator || "",
      tabId: details.tabId,
      source: "mime_video_audio",
    });

    chrome.tabs.sendMessage(details.tabId, {
      type: "RAW_VIDEO_DETECTED",
      data: {
        videoUrl: url,
        source: "mime_video_audio",
        tabId: details.tabId,
        contentType: details.responseHeaders.find(h => (h.name || "").toLowerCase() === "content-type")?.value || "",
      },
    }).catch(() => {});
  },
  { urls: ["*://*/*"], types: ["xmlhttprequest"] },
  ["responseHeaders"]
);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GET_CACHED_REQUESTS" && sender?.tab?.id) {
    sendResponse(_getCachedRequests(sender.tab.id));
    return true;
  }
  return false;
});

// Sekme kapandiginda cache temizle
chrome.tabs.onRemoved.addListener((tabId) => {
  requestCache.delete(tabId);
});

// ═══════ BADGE ═══════
function updateBadge(tabId, count) {
  let text, color;
  if (count < 0) {
    text = "DRM";
    color = "#ef4444";
  } else {
    text = count > 0 ? String(count) : "";
    color = count > 0 ? "#10b981" : "#666";
  }
  const opts = { text, color };
  if (tabId) opts.tabId = tabId;
  try {
    chrome.action.setBadgeText({ text: opts.text, tabId: opts.tabId });
    chrome.action.setBadgeBackgroundColor({ color: opts.color, tabId: opts.tabId });
  } catch {}
}

// ═══════ ALARMS ═══════
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "cleanup") {
    AFLog.debug("BG", "Temizlik alarmi calisti");
    state.cleanup(60 * 60 * 1000);
  }
  if (alarm.name === "ws-check") {
    const ws = backend.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      AFLog.debug("BG", "WebSocket baglantisi yok, yeniden baglaniliyor");
      backend.connectWebSocket();
    }
  }
});

// ═══════ COMMANDS ═══════
chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "toggle-panel" && tab?.id) {
    AFLog.debug("BG", "Klavye kisayolu: toggle-panel");
    chrome.tabs.sendMessage(tab.id, { type: "TOGGLE_PANEL" }).catch(() => {});
  }
  if (command === "download-all" && tab?.id) {
    AFLog.debug("BG", "Klavye kisayolu: download-all");
    chrome.tabs.sendMessage(tab.id, { type: "DOWNLOAD_ALL_REQUEST" }).catch(() => {});
  }
});

AFLog.info("BG", "Background service worker baslatildi");
