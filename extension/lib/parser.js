"use strict";

// ════════════════════════════════════════════
// AniFrame - M3U8 / MPD Manifest Parser
// HLS master playlist + DASH MPD quality detection
// ════════════════════════════════════════════

class VideoParser {
  static qualityMap = {
    "2160": "4K", "1440": "2K", "1080": "1080p",
    "720": "720p", "480": "480p", "360": "360p",
    "240": "240p", "144": "144p",
  };

  static bandwidthToQuality(bandwidth) {
    const bw = parseInt(bandwidth) || 0;
    if (bw >= 8000000) return "≥1080p";
    if (bw >= 5000000) return "1080p";
    if (bw >= 2500000) return "720p";
    if (bw >= 1000000) return "480p";
    if (bw >= 500000) return "360p";
    return "240p";
  }

  static formatBitrate(bps) {
    const bw = parseInt(bps) || 0;
    if (bw >= 1000000) return (bw / 1000000).toFixed(1) + " Mbps";
    if (bw >= 1000) return Math.round(bw / 1000) + " Kbps";
    return bw + " bps";
  }

  static parseM3U8(text) {
    if (!text) return [];
    if (text.includes("#EXT-X-STREAM-INF")) return this._parseMaster(text);
    if (text.includes("#EXTINF")) return []; // media playlist, not variants
    return [];
  }

  static _parseMaster(text) {
    const variants = [];
    const lines = text.split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (line.startsWith("#EXT-X-STREAM-INF:")) {
        const attrs = {};
        const raw = line.substring("#EXT-X-STREAM-INF:".length);
        raw.split(",").forEach(part => {
          const eq = part.indexOf("=");
          if (eq > 0) attrs[part.substring(0, eq).trim()] = part.substring(eq + 1).trim().replace(/"/g, "");
        });
        let url = "";
        for (let j = i + 1; j < lines.length; j++) {
          const nl = lines[j].trim();
          if (nl && !nl.startsWith("#")) { url = nl; i = j; break; }
        }
        if (url) {
          variants.push({
            url, type: "HLS",
            bandwidth: parseInt(attrs["BANDWIDTH"]) || 0,
            resolution: attrs["RESOLUTION"] || "",
            codecs: attrs["CODECS"] || "",
            frameRate: attrs["FRAME-RATE"] || "",
            quality: this.bandwidthToQuality(attrs["BANDWIDTH"]),
            bitrate: this.formatBitrate(attrs["BANDWIDTH"]),
          });
        }
      }
    }
    return variants;
  }

  static parseMPD(text) {
    const variants = [];
    if (!text) return variants;

    const sets = text.match(/<AdaptationSet[^>]*>[\s\S]*?<\/AdaptationSet>/g) || [];
    for (const set of sets) {
      const isVideo = /mimeType\s*=\s*[\"']video\//i.test(set);
      const isAudio = /mimeType\s*=\s*[\"']audio\//i.test(set);
      if (!isVideo && !isAudio) continue;

      const reps = set.match(/<Representation[^>]*>[\s\S]*?<\/Representation>/g) || [];
      for (const rep of reps) {
        const bw = parseInt((rep.match(/bandwidth\s*=\s*[\"'](\d+)[\"']/i) || [])[1]) || 0;
        const width = parseInt((rep.match(/width\s*=\s*[\"'](\d+)[\"']/i) || [])[1]) || 0;
        const height = parseInt((rep.match(/height\s*=\s*[\"'](\d+)[\"']/i) || [])[1]) || 0;
        const codecs = (rep.match(/codecs\s*=\s*[\"']([^\"']+)[\"']/i) || [])[1] || "";
        variants.push({
          type: isVideo ? "DASH-Video" : "DASH-Audio",
          bandwidth: bw, width, height,
          quality: height >= 2160 ? "4K" : height >= 1080 ? "1080p" : height >= 720 ? "720p" : height >= 480 ? "480p" : "SD",
          codecs,
        });
      }
    }
    return variants;
  }

  static detectQualityFromVideo(video) {
    if (!video) return "";
    const w = video.videoWidth || 0;
    const h = video.videoHeight || 0;
    const maxDim = Math.max(w, h);
    if (maxDim >= 3840) return "4K";
    if (maxDim >= 2560) return "2K";
    if (maxDim >= 1920) return "1080p";
    if (maxDim >= 1280) return "720p";
    if (maxDim >= 640) return "480p";
    if (maxDim > 0) return "SD";
    return "";
  }

  static detectQualityFromURL(url) {
    const match = (url || "").match(/(\d{3,4})p/i);
    return match ? match[1] + "p" : "";
  }

  static async fetchFileSize(url) {
    try {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 5000);
      const resp = await fetch(url, { method: "HEAD", signal: controller.signal });
      return parseInt(resp.headers.get("Content-Length")) || 0;
    } catch { return 0; }
  }
}
