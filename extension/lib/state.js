"use strict";

// ════════════════════════════════════════════
// AniFrame - State Manager (chrome.storage wrapper)
// Manifest V3 uyumlu - service worker restart sonrası recovery
// ════════════════════════════════════════════

class StateManager {
  constructor() {
    this.cache = {};
    this.listeners = {};
  }

  async init() {
    try {
      const result = await chrome.storage.local.get(null);
      this.cache = result || {};
    } catch { this.cache = {}; }
  }

  async _save() {
    try { await chrome.storage.local.set(this.cache); } catch {}
  }

  // === TAB STATE ===
  async getTabState(tabId) {
    const key = "tab_" + tabId;
    return this.cache[key] || { videos: [], detectedUrls: {}, lastActive: 0 };
  }

  async setTabState(tabId, data) {
    const key = "tab_" + tabId;
    this.cache[key] = data;
    this.cache[key].lastActive = Date.now();
    await this._save();
  }

  async removeTabState(tabId) {
    const key = "tab_" + tabId;
    delete this.cache[key];
    await this._save();
  }

  // === VIDEOS PER TAB ===
  async addVideo(tabId, video) {
    const state = await this.getTabState(tabId);
    const exists = state.videos.some(v => v.videoUrl === video.videoUrl);
    if (!exists) {
      state.videos.unshift(video);
      if (state.videos.length > 50) state.videos.pop();
    }
    await this.setTabState(tabId, state);
    this._emit("video_added_" + tabId, video);
  }

  async getVideos(tabId) {
    const state = await this.getTabState(tabId);
    return state.videos || [];
  }

  async clearVideos(tabId) {
    const state = await this.getTabState(tabId);
    state.videos = [];
    state.detectedUrls = {};
    await this.setTabState(tabId, state);
  }

  // === DETECTED URLS (dedup - global) ===
  async isUrlSent(videoUrl) {
    if (!videoUrl) return false;
    const key = "sent_urls";
    const sent = this.cache[key] || [];
    const normalized = videoUrl.split("?")[0].split("#")[0];
    return sent.includes(normalized);
  }

  async markUrlSent(videoUrl) {
    if (!videoUrl) return;
    const key = "sent_urls";
    const sent = this.cache[key] || [];
    const normalized = videoUrl.split("?")[0].split("#")[0];
    if (!sent.includes(normalized)) {
      sent.unshift(normalized);
      if (sent.length > 500) sent.pop();
      this.cache[key] = sent;
      await this._save();
    }
  }

  // === SETTINGS ===
  async getSettings() {
    const key = "extension_settings";
    const defaults = {
      backendUrl: DEFAULT_BACKEND,
      autoDownload: false,
      showNotifications: true,
      category: "auto",
      quality: "best",
      maxConcurrent: 2,
      adFilterEnabled: true,
      wsReconnect: true,
      debugLogs: false,
    };
    return { ...defaults, ...(this.cache[key] || {}) };
  }

  async setSetting(key, value) {
    const settings = await this.getSettings();
    settings[key] = value;
    this.cache["extension_settings"] = settings;
    await this._save();
    this._emit("settings_changed", settings);
  }

  // === CONNECTION STATE ===
  async setConnectionState(state) {
    this.cache["connection_state"] = state;
    await this._save();
  }

  async getConnectionState() {
    return this.cache["connection_state"] || "disconnected";
  }

  // === DOWNLOAD QUEUE (local cache) ===
  async getLocalQueue() {
    return this.cache["local_queue"] || [];
  }

  async addToLocalQueue(task) {
    const queue = await this.getLocalQueue();
    queue.push(task);
    this.cache["local_queue"] = queue;
    await this._save();
  }

  // === CLEANUP ===
  async cleanup(olderThanMs) {
    const now = Date.now();
    const keys = Object.keys(this.cache);
    for (const k of keys) {
      if (k.startsWith("tab_") && this.cache[k].lastActive) {
        if (now - this.cache[k].lastActive > olderThanMs) {
          delete this.cache[k];
        }
      }
    }
    await this._save();
  }

  // === CHANGE LISTENERS ===
  onChange(event, callback) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(callback);
  }

  removeListener(event, callback) {
    if (this.listeners[event]) {
      this.listeners[event] = this.listeners[event].filter(c => c !== callback);
    }
  }

  _emit(event, data) {
    if (this.listeners[event]) {
      this.listeners[event].forEach(c => { try { c(data); } catch {} });
    }
  }
}
