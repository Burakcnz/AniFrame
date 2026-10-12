"use strict";

const API = "http://localhost:8000";
let videos = [];
let logRefreshTimer = null;

document.addEventListener("DOMContentLoaded", async () => {
  checkStatus();
  await loadVideos();
  initTabs();
  initLogViewer();

  document.getElementById("btnAll").onclick = downloadAll;
  document.getElementById("btnOpen").onclick = () => window.open(API, "_blank");
  document.getElementById("btnSettings").onclick = () => chrome.runtime.openOptionsPage();
});

// ═══════ TABS ═══════
function initTabs() {
  document.querySelectorAll(".tab").forEach(tab => {
    tab.onclick = () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      const target = tab.dataset.tab;
      document.getElementById("tab-videos").style.display = target === "videos" ? "" : "none";
      document.getElementById("tab-logs").style.display = target === "logs" ? "" : "none";
      if (target === "logs") refreshLogs();
    };
  });
}

// ═══════ STATUS ═══════
async function checkStatus() {
  const dot = document.getElementById("statusDot");
  const text = document.getElementById("statusText");
  try {
    const res = await fetch(API + "/api/extension/status");
    const data = await res.json();
    if (data.enabled || data.running) {
      dot.className = "status-dot on";
      text.textContent = "Backend aktif — " + API;
    } else {
      dot.className = "status-dot off";
      text.textContent = "Backend kapalı — lütfen AniFrame'i başlatın";
    }
  } catch {
    dot.className = "status-dot err";
    text.textContent = "Bağlantı hatası — AniFrame çalışıyor mu?";
  }
}

// ═══════ VIDEOS ═══════
async function loadVideos() {
  const list = document.getElementById("tab-videos");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      list.innerHTML = '<div class="empty">Sekme bilgisi alınamadı</div>';
      return;
    }
    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: "GET_VIDEOS" });
      videos = response?.videos || [];
    } catch {
      videos = [];
    }

    document.getElementById("videoCount").textContent = videos.length;

    if (videos.length === 0) {
      list.innerHTML = '<div class="empty">Bu sayfada henüz video algılanmadı</div>';
      return;
    }
    renderVideos();
  } catch {
    list.innerHTML = '<div class="empty">Video listesi yüklenemedi</div>';
  }
}

function renderVideos() {
  const list = document.getElementById("tab-videos");
  list.innerHTML = videos.map(v => {
    const meta = [v.videoType, v.quality].filter(Boolean).join(" · ");
    return `<div class="video-item">
      <div class="video-icon">🎬</div>
      <div class="video-info">
        <div class="video-name">${escapeHtml(v.title || "Video")}</div>
        <div class="video-meta">${meta || v.hostname || ""}</div>
      </div>
      <button class="btn btn-primary" data-url="${escapeHtml(v.videoUrl)}">İndir</button>
    </div>`;
  }).join("");

  list.querySelectorAll(".btn-primary[data-url]").forEach(btn => {
    btn.onclick = async () => {
      const url = btn.dataset.url;
      const v = videos.find(x => x.videoUrl === url);
      try {
        const pageUrl = v.pageUrl || "";
        const res = await fetch(API + "/api/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: pageUrl || v.videoUrl,
            category: "video",
            format_id: "best",
            is_browser_url: true,
            custom_video_url: v.videoUrl,
            custom_title: v.title || "",
            page_url: pageUrl,
          }),
        });
        const data = await res.json();
        if (data.id) {
          btn.textContent = "Eklendi ✓";
          btn.disabled = true;
        }
      } catch {
        btn.textContent = "Hata!";
      }
    };
  });
}

async function downloadAll() {
  const btns = document.querySelectorAll("#tab-videos .btn-primary[data-url]");
  for (const btn of btns) {
    btn.click();
    await new Promise(r => setTimeout(r, 300));
  }
  document.getElementById("tab-videos").innerHTML = '<div class="empty">✅ Tüm videolar kuyruğa eklendi</div>';
}

// ═══════ LOG VIEWER ═══════
function initLogViewer() {
  document.getElementById("logLevelFilter").onchange = () => refreshLogs();
  document.getElementById("logSearchFilter").oninput = debounce(() => refreshLogs(), 300);
  document.getElementById("btnCopyLogs").onclick = copyLogs;
  document.getElementById("btnExportLogs").onclick = exportLogs;
  document.getElementById("btnClearLogs").onclick = clearLogs;

  // Otomatik yenileme
  logRefreshTimer = setInterval(() => {
    if (document.getElementById("tab-logs").style.display !== "none") {
      refreshLogs();
    }
  }, 2000);
}

async function refreshLogs() {
  const container = document.getElementById("logContainer");
  const level = document.getElementById("logLevelFilter").value;
  const search = document.getElementById("logSearchFilter").value;

  try {
    const response = await chrome.runtime.sendMessage({
      type: "GET_LOGS",
      filter: { level, text: search },
    });

    const entries = response?.entries || [];
    const stats = response?.stats || {};

    // Stats goster
    const statsEl = document.getElementById("logStats");
    statsEl.textContent = `${stats.total || 0} log | ❌${stats.ERROR || 0} ⚠️${stats.WARN || 0} ℹ️${stats.INFO || 0} 🔍${stats.DEBUG || 0}`;

    // Badge guncelle
    document.getElementById("logCount").textContent = stats.total || 0;

    if (entries.length === 0) {
      container.innerHTML = '<div class="empty">Log bulunamadı</div>';
      return;
    }

    // Son 200 logu goster (performans icin)
    const show = entries.slice(-200).reverse();
    container.innerHTML = show.map(e => {
      const time = e.time ? e.time.substring(11, 23) : "";
      const dataStr = e.data ? (typeof e.data === "string" ? e.data : JSON.stringify(e.data)) : "";
      return `<div class="log-entry">
        <span class="log-time">${time}</span>
        <span class="log-level ${e.level}">${e.level}</span>
        <span class="log-cat">[${e.cat}]</span>
        <span class="log-msg">${escapeHtml(e.msg)}${dataStr ? " " + escapeHtml(dataStr) : ""}</span>
      </div>`;
    }).join("");

    // Auto-scroll
    container.scrollTop = 0;
  } catch (err) {
    container.innerHTML = '<div class="empty">Log yüklenemedi</div>';
  }
}

async function copyLogs() {
  try {
    const level = document.getElementById("logLevelFilter").value;
    const search = document.getElementById("logSearchFilter").value;
    const response = await chrome.runtime.sendMessage({
      type: "EXPORT_LOGS",
      filter: { level, text: search },
    });
    if (response?.text) {
      await navigator.clipboard.writeText(response.text);
      const btn = document.getElementById("btnCopyLogs");
      btn.textContent = "✓";
      setTimeout(() => { btn.textContent = "📋"; }, 1500);
    }
  } catch (err) {
    alert("Kopyalama hatasi: " + err.message);
  }
}

async function exportLogs() {
  try {
    const level = document.getElementById("logLevelFilter").value;
    const search = document.getElementById("logSearchFilter").value;
    const response = await chrome.runtime.sendMessage({
      type: "EXPORT_LOGS",
      filter: { level, text: search },
    });
    if (response?.text) {
      const blob = new Blob([response.text], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `aniframe-logs-${new Date().toISOString().replace(/[:.]/g, "-")}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    }
  } catch (err) {
    alert("Export hatasi: " + err.message);
  }
}

async function clearLogs() {
  try {
    await chrome.runtime.sendMessage({ type: "CLEAR_LOGS" });
    refreshLogs();
  } catch {}
}

function debounce(fn, ms) {
  let timer;
  return function(...args) { clearTimeout(timer); timer = setTimeout(() => fn.apply(this, args), ms); };
}

function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
