"use strict";

// ════════════════════════════════════════════
// AniFrame - Backend API Client
// REST API + WebSocket client with auto-reconnect
// ════════════════════════════════════════════

class BackendClient {
  constructor(stateManager, options) {
    this.state = stateManager || { getSettings: async () => ({}), setConnectionState: async () => {}, getConnectionState: async () => "disconnected" };
    this.baseUrl = DEFAULT_BACKEND;
    this.ws = null;
    this.wsTimer = null;
    this.wsDelay = 1000;
    this.listeners = {};
    // WebSocket yalnizca background service worker'da acilmali. Icerik betikleri
    // her frame'de (reklam/sandbox iframe'ler dahil) calistigi icin oralarda WS
    // acmak CSP nedeniyle "connection failed" hatalari ve gereksiz baglantilar
    // uretir. enableWebSocket=false ile icerik betiklerinde WS devre disi birakilir.
    this.enableWebSocket = !(options && options.enableWebSocket === false);
  }

  async init() {
    try {
      const s = await this.state.getSettings();
      this.baseUrl = s.backendUrl || DEFAULT_BACKEND;
    } catch {}
    try {
      await this.pingRest();
    } catch {}
    if (this.enableWebSocket) this.connectWebSocket();
  }

  // === HEALTH CHECK ===
  async pingRest() {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const controller = new AbortController();
        setTimeout(() => controller.abort(), 4000);
        const r = await fetch(this.baseUrl + "/api/integration/ping", { signal: controller.signal });
        if (r.ok) {
          const data = await r.json();
          if (data.status === "ok") {
            this.state.setConnectionState("connected");
            this.emit("connection_change", "connected");
            return true;
          }
        }
      } catch {}
      if (attempt < 2) await new Promise(r => setTimeout(r, 800));
    }
    return false;
  }

  async healthCheck() {
    const wsAlive = this.ws && this.ws.readyState === WebSocket.OPEN;
    if (wsAlive) return true;
    return await this.pingRest();
  }

  // === REST API ===
  async detectVideo(data) {
    return this.post("/api/extension/detect", {
      page_url: data.pageUrl, video_url: data.videoUrl,
      title: data.title || "", video_type: data.videoType || "",
      hostname: data.hostname || "", resolution: data.quality || "",
    });
  }

  async download(url, options) {
    const pageUrl = options.pageUrl || "";
    const vurl = options.videoUrl || url || "";
    return this.post("/api/download", {
      url: pageUrl || options.videoUrl || url,
      category: options.category || "video",
      format_id: options.format || "best",
      is_browser_url: true,
      custom_video_url: vurl.startsWith("blob:") ? "" : vurl,
      custom_title: options.title || "",
      page_url: pageUrl,
      cookies: options.cookies || "",
      cookies_netscape: options.cookiesNetscape || "",
    });
  }

  async downloadNow(url, options) {
    const pageUrl = options.pageUrl || "";
    const vurl = options.videoUrl || url || "";
    return this.post("/api/download", {
      url: pageUrl || options.videoUrl || url,
      category: options.category || "video",
      format_id: options.format || "best",
      is_browser_url: true,
      custom_video_url: vurl.startsWith("blob:") ? "" : vurl,
      custom_title: options.title || "",
      page_url: pageUrl,
      cookies: options.cookies || "",
      cookies_netscape: options.cookiesNetscape || "",
      priority: "high",
    });
  }

  async getStatus() {
    return this.get("/api/extension/status");
  }

  async getQueue() {
    return this.get("/api/queue");
  }

  // === WebSocket ===
  connectWebSocket() {
    const wsUrl = this.baseUrl.replace(/^http/, "ws") + "/ws";
    if (this.ws) { try { this.ws.close(); } catch {} }
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.wsDelay = 1000;
      this.state.setConnectionState("connected");
      this.emit("connection_change", "connected");
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this._handleWS(msg);
      } catch {}
    };

    this.ws.onclose = async () => {
      const restOk = await this.pingRest();
      if (restOk) {
        this._scheduleReconnect();
        return;
      }
      this.state.setConnectionState("disconnected");
      this.emit("connection_change", "disconnected");
      this._scheduleReconnect();
    };

    this.ws.onerror = () => { try { this.ws.close(); } catch {} };
  }

  _handleWS(msg) {
    switch (msg.type) {
      case "analyze_request":
        this.emit("analyze_url", msg.url);
        break;
      case "download_started":
      case "download_progress":
      case "download_completed":
      case "download_error":
      case "pong":
        this.emit(msg.type, msg);
        break;
    }
  }

  _scheduleReconnect() {
    if (this.wsTimer) clearTimeout(this.wsTimer);
    this.wsTimer = setTimeout(() => {
      this.connectWebSocket();
      this.wsDelay = Math.min(this.wsDelay * 2, 30000);
    }, this.wsDelay);
  }

  sendWS(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  // === EVENT SYSTEM ===
  on(event, fn) { if (!this.listeners[event]) this.listeners[event] = []; this.listeners[event].push(fn); }
  off(event, fn) { if (this.listeners[event]) this.listeners[event] = this.listeners[event].filter(f => f !== fn); }
  emit(event, data) { if (this.listeners[event]) this.listeners[event].forEach(f => { try { f(data); } catch {} }); }

  // === HTTP HELPERS ===
  async get(path) {
    try {
      const r = await fetch(this.baseUrl + path);
      return await r.json();
    } catch { return { error: "Connection failed" }; }
  }

  async post(path, body) {
    try {
      const r = await fetch(this.baseUrl + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return await r.json();
    } catch { return { error: "Connection failed" }; }
  }

  // === INTEGRATION CONNECTOR (IDM modu - periyodik sync) ===
  async startConnector() {
    this._connectorRunning = true;
    this._syncLoop();
  }

  async _syncLoop() {
    if (!this._connectorRunning) return;
    try {
      const r = await fetch(this.baseUrl + "/api/integration/sync");
      if (r.ok) {
        const state = await r.json();
        this.emit("integration_sync", state);
      }
    } catch {}
    this._connectorTimer = setTimeout(() => this._syncLoop(), 3000);
  }

  stopConnector() {
    this._connectorRunning = false;
    if (this._connectorTimer) { clearTimeout(this._connectorTimer); }
  }

  async pingIntegration() {
    try {
      const r = await fetch(this.baseUrl + "/api/integration/ping");
      return await r.json();
    } catch { return { status: "error" }; }
  }

  async sendVideoToIntegration(data) {
    return this.post("/api/integration/video", data);
  }

  async downloadWithPriority(url, options) {
    const pageUrl = options.pageUrl || "";
    const vurl = options.videoUrl || url || "";
    return this.post("/api/download/priority", {
      url: pageUrl || options.videoUrl || url,
      category: options.category || "video",
      format_id: options.format || "best",
      priority: options.priority || "normal",
      is_browser_url: true,
      custom_video_url: vurl.startsWith("blob:") ? "" : vurl,
      custom_title: options.title || "",
      page_url: pageUrl,
      cookies: options.cookies || "",
      cookies_netscape: options.cookiesNetscape || "",
    });
  }
}
