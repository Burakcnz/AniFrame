"use strict";

// ════════════════════════════════════════════
// AniFrame - Centralized Logger
// Seviyeli, zaman damgalı, export edilebilir log sistemi
// ════════════════════════════════════════════

const AFLog = (() => {
  const MAX_ENTRIES = 500;
  const STORAGE_KEY = "aniframe_logs";
  const entries = [];
  let enabled = true;

  const LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 };
  const LEVEL_COLORS = {
    DEBUG: "#6b7280",
    INFO: "#3b82f6",
    WARN: "#f59e0b",
    ERROR: "#ef4444",
  };
  const LEVEL_ICONS = {
    DEBUG: "🔍",
    INFO: "ℹ️",
    WARN: "⚠️",
    ERROR: "❌",
  };

  function timestamp() {
    return new Date().toISOString().replace("T", " ").substring(0, 23);
  }

  function formatEntry(entry) {
    return `[${entry.time}] ${entry.level} [${entry.cat}] ${entry.msg}${entry.data ? " " + entry.data : ""}`;
  }

  function add(level, cat, msg, data) {
    if (!enabled) return;
    const entry = {
      id: entries.length,
      time: timestamp(),
      level,
      cat,
      msg,
      data: data || null,
      ts: Date.now(),
    };
    entries.push(entry);
    if (entries.length > MAX_ENTRIES) entries.shift();

    // Console'a yaz
    const consoleMsg = `[AF:${cat}] ${msg}${data ? " " + (typeof data === "string" ? data : JSON.stringify(data)) : ""}`;
    if (level === "ERROR") console.error(consoleMsg);
    else if (level === "WARN") console.warn(consoleMsg);
    else console.log(consoleMsg);

    return entry;
  }

  return {
    setEnabled(v) { enabled = v; },
    isEnabled() { return enabled; },

    debug(cat, msg, data) { return add("DEBUG", cat, msg, data); },
    info(cat, msg, data) { return add("INFO", cat, msg, data); },
    warn(cat, msg, data) { return add("WARN", cat, msg, data); },
    error(cat, msg, data) { return add("ERROR", cat, msg, data); },

    getEntries(filter) {
      if (!filter) return [...entries];
      const lower = (filter.level || "").toUpperCase();
      const catFilter = (filter.cat || "").toLowerCase();
      const textFilter = (filter.text || "").toLowerCase();
      return entries.filter(e => {
        if (lower && e.level !== lower) return false;
        if (catFilter && !e.cat.toLowerCase().includes(catFilter)) return false;
        if (textFilter && !e.msg.toLowerCase().includes(textFilter) && !(e.data && JSON.stringify(e.data).toLowerCase().includes(textFilter))) return false;
        return true;
      });
    },

    getAllFormatted(filter) {
      return this.getEntries(filter).map(formatEntry).join("\n");
    },

    getStats() {
      const stats = { total: entries.length, DEBUG: 0, INFO: 0, WARN: 0, ERROR: 0 };
      entries.forEach(e => { if (stats[e.level] !== undefined) stats[e.level]++; });
      return stats;
    },

    clear() { entries.length = 0; },

    exportText(filter) {
      const header = `=== AniFrame Logs ===\nTarayici: ${navigator.userAgent}\nTarih: ${new Date().toISOString()}\nToplam: ${entries.length} log\n\n`;
      return header + this.getAllFormatted(filter);
    },

    exportJSON(filter) {
      return JSON.stringify({
        exported: new Date().toISOString(),
        userAgent: navigator.userAgent,
        entries: this.getEntries(filter),
      }, null, 2);
    },

    async copyToClipboard(filter) {
      const text = this.exportText(filter);
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        // Fallback
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        return true;
      }
    },

    getConstants() { return { LEVELS, LEVEL_COLORS, LEVEL_ICONS, MAX_ENTRIES }; },
  };
})();
