"use strict";

// ════════════════════════════════════════════
// AniFrame Content Script v2.0
// Her sayfada: video algila, UI goster, indir
// ════════════════════════════════════════════

(function() {
  if (window.__aniframe_content) return;
  window.__aniframe_content = true;

  const tabId = generateId();
  const state = new StateManager();
  // Icerik betikleri her frame'de calisir; WebSocket'i yalnizca background SW
  // acar. Burada WS kapali (sadece REST). Push olaylari background'dan
  // chrome.runtime mesajlariyla iletilir.
  const backend = new BackendClient(state, { enableWebSocket: false });
  let detector = null;
  let floatingBar = null;
  let downloadPanel = null;
  let _pageUrl = null;
  let _connectionErrors = 0;

  function getPageUrl() {
    if (_pageUrl) return _pageUrl;
    const href = location.href;
    if (href && href !== "about:blank" && !href.startsWith("data:")) return href;
    try { if (window.top && window.top !== window) { const t = window.top.location.href; if (t && t !== "about:blank") return t; } } catch {}
    try { if (document.referrer && document.referrer !== "about:blank") return document.referrer; } catch {}
    try { if (window.parent && window.parent !== window) { const t = window.parent.location.href; if (t && t !== "about:blank") return t; } } catch {}
    return href;
  }

  function getPageCookies() {
    try { return document.cookie || ""; } catch { return ""; }
  }

  async function init() {
    _pageUrl = getPageUrl();
    AFLog.info("CS", "Content script baslatiliyor", { url: truncateUrl(_pageUrl), tabId });

    try {
      await state.init();
      await backend.init();
      AFLog.info("CS", "State ve backend baslatildi");
    } catch (err) {
      AFLog.error("CS", "State/backend baslatma hatasi", { error: err.message });
    }

    const isTopFrame = (window.top === window.self);
    if (isTopFrame) injectUI();
    startDetector();

    // Not: analyze_url ve download_progress artik WS uzerinden gelmiyor.
    // WebSocket sadece background service worker'da acik; bu olaylar asagidaki
    // chrome.runtime.onMessage dinleyicisinde "WS_DOWNLOAD_PROGRESS" vb. olarak
    // background'dan iletiliyor.

    backend.on("connection_change", (status) => {
      AFLog.info("CS", "Backend baglanti durumu degisti", { status });
      if (status === "connected") {
        _connectionErrors = 0;
        if (floatingBar) floatingBar.hideError();
        return;
      }
      if (status === "disconnected") {
        _connectionErrors++;
        if (_connectionErrors < 3) return;
        // 2sn sonra REST ile tekrar dogrula
        setTimeout(async () => {
          try {
            const alive = await backend.healthCheck();
            if (alive) {
              _connectionErrors = 0;
              if (floatingBar) floatingBar.hideError();
            } else {
              _connectionErrors++;
              if (_connectionErrors >= 3 && floatingBar) {
                floatingBar.showError("Backend baglantisi yok - AniFrame'i baslatin");
              }
            }
          } catch {
            _connectionErrors++;
            if (_connectionErrors >= 3 && floatingBar) {
              floatingBar.showError("Backend baglantisi yok - AniFrame'i baslatin");
            }
          }
        }, 2000);
      }
    });

    chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
      switch (msg.type) {
        case "VIDEO_DETECTED":
          AFLog.debug("CS", "VIDEO_DETECTED mesaji alindi", { url: truncateUrl(msg.data?.videoUrl), source: msg.data?.source });
          onVideoDetected(msg.data);
          break;
        case "DOWNLOAD_ALL_REQUEST":
          AFLog.info("CS", "Tumunu indir istegi alindi");
          downloadPanel && downloadPanel.onDownloadAll && downloadPanel.onDownloadAll();
          break;
        case "GET_VIDEOS":
          sendResponse({ videos: downloadPanel ? downloadPanel.getVideos() : [] });
          break;
        case "RAW_VIDEO_DETECTED":
          AFLog.debug("CS", "RAW_VIDEO_DETECTED (webRequest)", { url: truncateUrl(msg.data?.videoUrl) });
          if (detector) detector.evaluate(msg.data.videoUrl, "webrequest");
          break;
        case "TOGGLE_PANEL":
          downloadPanel && downloadPanel.toggle();
          break;
        case "WS_DOWNLOAD_PROGRESS":
        case "WS_DOWNLOAD_STARTED":
        case "WS_DOWNLOAD_COMPLETED":
        case "WS_DOWNLOAD_ERROR":
          // Background SW'nin WebSocket'inden iletilen indirme olaylari
          downloadPanel && downloadPanel.updateProgress && downloadPanel.updateProgress(msg.data);
          break;
      }
      return true;
    });

    AFLog.info("CS", "Content script hazir", { hostname: location.hostname });
  }

  function injectUI() {
    floatingBar = UIFactory.createFloatingBar();
    downloadPanel = UIFactory.createDownloadPanel();

    floatingBar.onDownloadAll(() => {
      downloadAll();
    });

    floatingBar.onOpenPanel(() => {
      downloadPanel.show();
    });

    downloadPanel.onDownloadAll(() => {
      downloadAll();
    });

    downloadPanel.onOpenApp(() => {
      window.open(DEFAULT_BACKEND, "_blank");
    });

    setTimeout(() => {
      floatingBar.show("AniFrame hazır — video aranıyor...");
      AFLog.debug("CS", "Floating bar gosterildi");
    }, 1000);
  }

  function startDetector() {
    detector = new VideoDetector(tabId, state);
    detector.init(document);
    AFLog.info("CS", "Video detector baslatildi", { detector: "VideoDetector v2" });
  }

  function onVideoDetected(data) {
    if (!data || !data.videoUrl) {
      AFLog.warn("CS", "onVideoDetected: bos veri");
      return;
    }

    if (downloadPanel) {
      downloadPanel.addVideo(data);
      const count = downloadPanel.getCount();
      AFLog.info("CS", "Video algilandi", {
        url: truncateUrl(data.videoUrl),
        type: data.videoType,
        quality: data.quality,
        source: data.source,
        total: count,
      });

      if (count > 0 && floatingBar) {
        floatingBar.show(`Bu sayfada ${count} video bulundu`);
      }
      chrome.runtime.sendMessage({
        type: "UPDATE_BADGE",
        count: count,
      }).catch(() => {});
    } else {
      AFLog.debug("CS", "Video algilandi (UI yok - iframe)", {
        url: truncateUrl(data.videoUrl), type: data.videoType,
      });
    }
  }

  async function downloadAll() {
    if (!downloadPanel) return;
    const videos = downloadPanel.getVideos();
    if (videos.length === 0) {
      AFLog.warn("CS", "Indirilecek video yok");
      return;
    }

    AFLog.info("CS", "Tum videolar indiriliyor", { count: videos.length });
    let count = 0;
    for (const v of videos) {
      try {
        await backend.download(v.videoUrl, {
          title: v.title,
          videoUrl: v.videoUrl,
          pageUrl: v.pageUrl || getPageUrl(),
          cookies: getPageCookies(),
          category: v.videoType === "HLS" ? "video" : guessCategory(v.hostname),
        });
        count++;
        AFLog.debug("CS", "Video kuyruğa eklendi", { url: truncateUrl(v.videoUrl) });
      } catch (err) {
        AFLog.error("CS", "Video indirme hatasi", { url: truncateUrl(v.videoUrl), error: err.message });
      }
    }
    floatingBar.show(`${count} video kuyruğa eklendi!`);
    AFLog.info("CS", "Toplu indirme tamamlandi", { success: count, total: videos.length });
    setTimeout(() => floatingBar.hide(), 4000);
  }

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === "D") {
      e.preventDefault();
      AFLog.debug("CS", "Klavye kisayolu: Ctrl+Shift+D (toggle panel)");
      downloadPanel.toggle();
    }
    if (e.ctrlKey && e.shiftKey && e.key === "S") {
      e.preventDefault();
      AFLog.debug("CS", "Klavye kisayolu: Ctrl+Shift+S (download all)");
      downloadAll();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
