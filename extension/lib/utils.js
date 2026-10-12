"use strict";

// ════════════════════════════════════════════
// AniFrame - Utility Functions
// ════════════════════════════════════════════

const DEFAULT_BACKEND = "http://localhost:8000";
const NON_VIDEO_EXTS = new Set(["svg", "jpg", "jpeg", "png", "gif", "webp", "bmp", "ico", "css", "js", "html", "htm", "php", "xml", "woff", "woff2", "ttf", "eot", "otf", "json", "pdf", "txt", "csv", "webmanifest", "vtt", "srt", "sub", "sbv"]);
const VIDEO_EXTS = new Set(["mp4", "mkv", "webm", "m3u8", "ts", "flv", "avi", "mov", "wmv", "m4v", "ogv", "3gp", "f4v", "divx", "xvid", "rm", "rmvb", "asf", "vob", "m2ts", "mts", "mpd", "m3u", "mpg", "mpeg"]);
const AUDIO_EXTS = new Set(["mp3", "aac", "ogg", "wma", "m4a", "weba", "opus", "flac", "wav", "pcm", "ac3", "dts", "aif", "aiff", "ape", "alac", "mid", "midi", "ra", "ram", "mp2"]);
const MANIFEST_EXTS = new Set(["m3u8", "mpd", "m3u", "ism", "isml", "ismc", "f4m"]);
const SUBTITLE_EXTS = new Set(["vtt", "webvtt", "srt"]);

function isAudioExtension(url) {
  const ext = getFileExtension(url).replace(".", "");
  return AUDIO_EXTS.has(ext);
}

function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function truncateUrl(url, maxLen) {
  if (!url) return "";
  try { const u = new URL(url); return u.hostname + u.pathname.substring(0, maxLen || 60); }
  catch { return url.length > (maxLen || 60) ? url.substring(0, maxLen || 60) + "..." : url; }
}

function cleanTitle(str) {
  if (!str) return "";
  return str.replace(/[-_|]?(hdfilmcehennemi|fullhdfilmizlesene|dizigom|dizibox|filmmodu|animeci|tranimeizle|blutv|netflix|amazon|hulu|disney|youtube)[-_|]?/gi, "")
    .replace(/\s*[|•·]\s*$/, "").replace(/\s+/g, " ").trim();
}

function formatFileSize(bytes) {
  if (!bytes || bytes <= 0) return "?";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + " " + units[i];
}

function formatDuration(sec) {
  if (!sec || sec <= 0) return "";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  return m + ":" + String(s).padStart(2, "0");
}

function getFileExtension(url) {
  if (!url) return "";
  const p = url.split("?")[0].split("#")[0];
  const last = p.split("/").pop();
  const dot = last.lastIndexOf(".");
  return dot >= 0 ? last.substring(dot).toLowerCase() : "";
}

function getVideoType(url) {
  if (!url) return "unknown";
  const lower = url.toLowerCase();
  if (lower.includes(".m3u8") || lower.includes("m3u8")) return "HLS";
  if (lower.includes(".mpd")) return "DASH";
  if (lower.includes(".mp4")) return "MP4";
  if (lower.includes(".webm")) return "WebM";
  if (lower.includes(".mkv")) return "MKV";
  if (lower.includes(".ts")) return "TS";
  if (lower.includes(".flv")) return "FLV";
  if (lower.includes(".avi")) return "AVI";
  if (lower.includes(".mov")) return "MOV";
  if (lower.includes(".wmv")) return "WMV";
  if (lower.includes(".m4v")) return "M4V";
  if (lower.includes(".3gp") || lower.includes(".3gpp")) return "3GP";
  if (lower.includes(".ogv") || lower.includes(".ogg")) return "OGG";
  if (lower.includes(".divx")) return "DivX";
  if (lower.includes(".mpeg") || lower.includes(".mpg")) return "MPEG";
  if (lower.includes(".mp3")) return "MP3";
  if (lower.includes(".aac")) return "AAC";
  if (lower.includes(".opus")) return "Opus";
  if (lower.includes(".m4a")) return "M4A";
  if (lower.includes(".flac")) return "FLAC";
  if (lower.includes(".wav") || lower.includes(".pcm")) return "Audio";
  if (lower.includes(".ism") || lower.includes(".isml")) return "SmoothStreaming";
  if (lower.includes("manifest")) return "Manifest";
  if (lower.includes("master.json")) return "MasterJSON";
  if (lower.includes("playlist")) return "Playlist";
  if (lower.includes("stream")) return "Stream";
  if (lower.includes("/video")) return "Video";
  if (lower.includes("/audio")) return "Audio";
  return "Video";
}

function isVideoExtension(url) {
  const ext = getFileExtension(url).replace(".", "");
  return VIDEO_EXTS.has(ext);
}

function isManifestUrl(url) {
  const lower = (url || "").toLowerCase();
  if (/\.webmanifest(\?|#|$)/i.test(lower)) return false;
  return lower.includes(".m3u8") || lower.includes(".mpd") || lower.includes("/manifest") ||
    lower.includes("master.json") || lower.includes("playlist.m3u8") ||
    lower.includes("/playlist?") || lower.includes("format=m3u8") ||
    lower.includes("format=mpd") || lower.includes(".ism") || lower.includes(".isml") ||
    lower.includes("/manifest(") || lower.includes("/qualitylevels(");
}

function isSegmentUrl(url) {
  const clean = (url || "").split("?")[0].toLowerCase();
  return clean.endsWith(".ts") || clean.endsWith(".m4s") || clean.endsWith(".cmfv") || clean.endsWith(".cmfa") ||
    clean.endsWith(".mp4.tmp") || clean.endsWith(".tmp") ||
    /\/seg(ment)?[_-]?\d+\.\w+$/i.test(clean) || /\/chunk[_-]?\d+\.\w+$/i.test(clean) ||
    /\/[a-f0-9]{32,}(\.\w+)?$/i.test(clean) || /\/init-.*\.(m4s|mp4)$/i.test(clean) ||
    /\/media[_-]\d+\.(ts|m4s|mp4)$/i.test(clean) ||
    /\.(ts|m4s|cmfv|cmfa)\?/i.test(url || "");
}

function guessCategory(hostname) {
  const h = (hostname || "").toLowerCase();
  if (/anime|animetv|gogoanime|tranimeizle|animeci/i.test(h)) return "anime";
  if (/dizi|dizigom|dizibox|dizilab|dizipal|episode|sezon|season|s\d.*e\d/i.test(h)) return "dizi";
  if (/film|movie|hdfilm|fullhdfilm|filmmakinesi|sinema|izle|netflix/i.test(h)) return "film";
  return "video";
}

function generateId() {
  return "af_" + Date.now().toString(36) + "_" + Math.random().toString(36).substring(2, 8);
}

function debounce(fn, ms) {
  let timer;
  return function(...args) { clearTimeout(timer); timer = setTimeout(() => fn.apply(this, args), ms); };
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function getDomain(url) {
  try { return new URL(url).hostname; }
  catch { return ""; }
}

function isSameOrigin(url1, url2) {
  return getDomain(url1) === getDomain(url2);
}
