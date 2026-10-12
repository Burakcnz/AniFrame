"use strict";

// ════════════════════════════════════════════
// AniFrame - UI Factory (Shadow DOM isolation)
// Floating bar, download panel, quick dialog, overlay button
// ════════════════════════════════════════════

class UIFactory {
  static BASE_STYLES = `
    * { margin:0; padding:0; box-sizing:border-box; }
    :host { all:initial; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:13px; color:#e0e0e0; }
    .af-btn { padding:6px 14px; border:none; border-radius:6px; cursor:pointer; font-size:12px; font-weight:500; transition:all 0.2s; display:inline-flex; align-items:center; gap:4px; }
    .af-btn-primary { background:linear-gradient(135deg,#8b5cf6,#10b981); color:#fff; }
    .af-btn-primary:hover { filter:brightness(1.15); }
    .af-btn-secondary { background:rgba(255,255,255,0.08); color:#c0c0c0; }
    .af-btn-secondary:hover { background:rgba(255,255,255,0.14); }
    .af-btn-danger { background:rgba(239,68,68,0.2); color:#ef4444; }
    .af-btn-sm { padding:4px 10px; font-size:11px; }
    .af-btn-icon { padding:4px; min-width:28px; text-align:center; background:transparent; color:#888; }
    .af-btn-icon:hover { color:#fff; }
    .af-select { background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); color:#e0e0e0; padding:4px 8px; border-radius:4px; font-size:12px; }
    .af-select:focus { border-color:#8b5cf6; outline:none; }
  `;

  // ═══════ FLOATING BAR (UST) ═══════
  static createFloatingBar() {
    const host = document.createElement("div");
    host.id = "aniframe-bar";
    const shadow = host.attachShadow({ mode: "closed" });
    shadow.innerHTML = `<style>${this.BASE_STYLES}
      .af-float-bar { position:fixed; top:-60px; left:0; right:0; z-index:2147483647;
        background:rgba(15,15,26,0.95); backdrop-filter:blur(20px); border-bottom:1px solid rgba(139,92,246,0.3);
        padding:10px 20px; display:flex; align-items:center; gap:12px; transition:top 0.35s cubic-bezier(0.4,0,0.2,1); }
      .af-float-bar.visible { top:0; }
      .af-bar-logo { display:flex; align-items:center; gap:6px; font-weight:700; font-size:14px;
        background:linear-gradient(135deg,#8b5cf6,#10b981); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
      .af-bar-msg { flex:1; font-size:12px; color:#94a3b8; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .af-bar-msg.error { color:#ef4444; }
      .af-bar-actions { display:flex; gap:6px; flex-shrink:0; }
    </style>
    <div class="af-float-bar" id="bar">
      <div class="af-bar-logo"><span>📥</span> AniFrame</div>
      <div class="af-bar-msg" id="msg">Video aranıyor...</div>
      <div class="af-bar-actions" id="actions">
        <button class="af-btn af-btn-primary af-btn-sm" id="btnAll">Tümünü İndir</button>
        <button class="af-btn af-btn-secondary af-btn-sm" id="btnOpen">Paneli Aç</button>
      </div>
      <button class="af-btn af-btn-icon" id="btnDismiss">✕</button>
    </div>`;

    const bar = shadow.getElementById("bar");
    const msg = shadow.getElementById("msg");
    const actions = shadow.getElementById("actions");

    shadow.getElementById("btnDismiss").onclick = () => {
      bar.classList.remove("visible");
      bar._dismissed = true;
    };

    document.body.appendChild(host);
    return {
      show(text) {
        if (bar._dismissed) return;
        if (text) msg.textContent = text;
        bar.classList.add("visible");
        actions.style.display = text && text.includes("video") ? "" : "none";
      },
      hide() { bar.classList.remove("visible"); },
      showError(text) {
        if (bar._dismissed) return;
        msg.textContent = text || "Backend baglantisi yok";
        msg.classList.add("error");
        bar.classList.add("visible");
        actions.style.display = "none";
        bar._errorMode = true;
      },
      hideError() {
        if (!bar._errorMode) return;
        msg.classList.remove("error");
        msg.textContent = "";
        bar.classList.remove("visible");
        bar._errorMode = false;
      },
      onDownloadAll(fn) { shadow.getElementById("btnAll").onclick = fn; },
      onOpenPanel(fn) { shadow.getElementById("btnOpen").onclick = fn; },
    };
  }

  // ═══════ DOWNLOAD PANEL (SAG ALT) ═══════
  static createDownloadPanel() {
    const host = document.createElement("div");
    host.id = "aniframe-panel";
    const shadow = host.attachShadow({ mode: "closed" });
    shadow.innerHTML = `<style>${this.BASE_STYLES}
      .af-panel-host { position:fixed; bottom:20px; right:20px; z-index:2147483646; display:none; }
      .af-panel { width:360px; max-height:500px; background:rgba(15,15,26,0.97); backdrop-filter:blur(30px); border:1px solid rgba(139,92,246,0.2); border-radius:12px; overflow:hidden; display:flex; flex-direction:column; box-shadow:0 20px 60px rgba(0,0,0,0.5); }
      .af-panel-header { padding:10px 14px; display:flex; align-items:center; justify-content:space-between; background:rgba(139,92,246,0.05); cursor:move; user-select:none; }
      .af-panel-title { font-weight:700; font-size:13px; background:linear-gradient(135deg,#8b5cf6,#10b981); -webkit-background-clip:text; -webkit-text-fill-color:transparent; display:flex; align-items:center; gap:6px; }
      .af-panel-count { font-size:11px; color:#8b5cf6; -webkit-text-fill-color:#8b5cf6; }
      .af-panel-body { flex:1; overflow-y:auto; padding:6px; max-height:380px; }
      .af-panel-body::-webkit-scrollbar { width:4px; }
      .af-panel-body::-webkit-scrollbar-thumb { background:rgba(255,255,255,0.1); border-radius:2px; }
      .af-panel-footer { padding:8px 14px; display:flex; gap:6px; border-top:1px solid rgba(255,255,255,0.05); }
      .af-video-item { display:flex; align-items:center; gap:8px; padding:8px 10px; border-radius:8px; margin-bottom:4px; background:rgba(255,255,255,0.02); transition:background 0.2s; }
      .af-video-item:hover { background:rgba(255,255,255,0.05); }
      .af-video-icon { font-size:18px; flex-shrink:0; }
      .af-video-info { flex:1; min-width:0; }
      .af-video-name { font-size:12px; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .af-video-meta { font-size:10px; color:#666; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .af-progress-bar { height:4px; background:rgba(255,255,255,0.06); border-radius:2px; margin-top:6px; overflow:hidden; }
      .af-progress-fill { height:100%; background:linear-gradient(90deg,#8b5cf6,#10b981); transition:width 0.3s; border-radius:2px; }
      .af-progress-text { font-size:10px; color:#888; margin-top:2px; }
      .af-empty { text-align:center; padding:30px 0; color:#4a4a60; font-size:12px; }
    </style>
    <div class="af-panel-host" id="host">
      <div class="af-panel" id="panel">
        <div class="af-panel-header" id="header">
          <div class="af-panel-title"><span>📥</span> AniFrame <span class="af-panel-count" id="count">0</span></div>
          <div style="display:flex;gap:4px">
            <button class="af-btn af-btn-sm af-btn-danger" id="btnClose">✕</button>
          </div>
        </div>
        <div class="af-panel-body" id="body"><div class="af-empty">Henüz video bulunamadı</div></div>
        <div class="af-panel-footer" id="footer">
          <button class="af-btn af-btn-primary af-btn-sm" id="btnDownloadAll">📥 Tümünü İndir</button>
          <button class="af-btn af-btn-secondary af-btn-sm" id="btnOpenApp">⚡ App</button>
        </div>
      </div>
    </div>`;

    const panelHost = shadow.getElementById("host");
    const body = shadow.getElementById("body");
    const count = shadow.getElementById("count");
    let videos = [];

    shadow.getElementById("btnClose").onclick = () => { panelHost.style.display = "none"; };

    this._makeDraggable(shadow.getElementById("header"), host, panelHost);

    document.body.appendChild(host);

    return {
      toggle() { panelHost.style.display = panelHost.style.display === "none" ? "block" : "none"; },
      show() { panelHost.style.display = "block"; },
      hide() { panelHost.style.display = "none"; },
      addVideo(v) {
        if (videos.some(x => x.videoUrl === v.videoUrl)) return;
        videos.unshift(v);
        this.render();
      },
      render() {
        count.textContent = String(videos.length);
        if (videos.length === 0) {
          body.innerHTML = '<div class="af-empty">Henüz video bulunamadı</div>';
          return;
        }
        body.innerHTML = videos.map(v => {
          const meta = [v.videoType, v.quality].filter(Boolean).join(" · ");
          return `<div class="af-video-item">
            <div class="af-video-icon">🎬</div>
            <div class="af-video-info">
              <div class="af-video-name">${escapeHtml(v.title || "Video")}</div>
              <div class="af-video-meta">${meta || v.hostname || ""}</div>
            </div>
            <button class="af-btn af-btn-primary af-btn-sm" data-url="${escapeHtml(v.videoUrl)}">İndir</button>
          </div>`;
        }).join("");
        body.querySelectorAll(".af-btn-primary[data-url]").forEach(btn => {
          btn.onclick = () => {
            chrome.runtime.sendMessage({
              type: "DOWNLOAD_REQUEST",
              data: videos.find(v => v.videoUrl === btn.dataset.url) || {},
              priority: "normal",
            }).catch(() => {});
          };
        });
      },
      getCount() { return videos.length; },
      getVideos() { return [...videos]; },
      clear() { videos = []; this.render(); },
      onDownloadAll(fn) { shadow.getElementById("btnDownloadAll").onclick = fn; },
      onOpenApp(fn) { shadow.getElementById("btnOpenApp").onclick = fn; },
    };
  }

  static _makeDraggable(headerEl, hostEl, panelEl) {
    let dragging = false, sx, sy, ox, oy;
    headerEl.onmousedown = (e) => {
      if (e.target.tagName === "BUTTON") return;
      dragging = true; sx = e.clientX; sy = e.clientY;
      ox = hostEl.offsetLeft; oy = hostEl.offsetTop;
    };
    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      hostEl.style.right = "auto";
      hostEl.style.bottom = "auto";
      hostEl.style.left = (ox + e.clientX - sx) + "px";
      hostEl.style.top = (oy + e.clientY - sy) + "px";
    });
    document.addEventListener("mouseup", () => { dragging = false; });
  }
}
