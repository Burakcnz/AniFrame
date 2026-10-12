"use strict";

// ════════════════════════════════════════════
// AniFrame - Video Detection Engine v2
// 7 katmanli: DOM, Shadow DOM, Network, Player API, MutationObserver, Meta, Blob/MSE
// ════════════════════════════════════════════

class VideoDetector {
  constructor(tabId, stateManager) {
    this.tabId = tabId;
    this.state = stateManager;
    this.detectedUrls = new Set();
    this.observers = [];
    this.running = false;
    this._origFetch = null;
    this._origOpen = null;
    this._origPushState = null;
    this._origReplaceState = null;
    this._origCreateObjectURL = null;
    this._blobVideoMap = new Map();
  }

  // ═══════ MAIN ═══════
  async init(doc) {
    if (this.running) return;
    this.running = true;

    this.scanDOM(doc);
    this.scanShadowRoots(doc);
    this.interceptNetwork();
    this.scanPlayers(window);
    this.scanMediaSourceElements(document);
    this.scanMetaTags(doc);
    this.scanPerformanceAPI(doc);
    this._scanJWPlayerScripts(doc);
    this.observeMutations(doc);
    this.interceptBlobURLs();
    this.observeSPA();

    for (const delay of [1000, 2000, 4000, 8000]) {
      setTimeout(() => { if (this.running) { this.scanDOM(document); this.scanShadowRoots(document); this._scanJWPlayerScripts(document); } }, delay);
    }
    for (const delay of [1500, 3500, 6000]) {
      setTimeout(() => { if (this.running) this.scanPlayers(window); }, delay);
    }
    for (const delay of [2000, 5000, 10000]) {
      setTimeout(() => { if (this.running) this.scanMediaSourceElements(document); }, delay);
    }
    setTimeout(() => { if (this.running) this.scanIframes(doc, 0); }, 2000);
    setTimeout(() => { if (this.running) this.scanIframes(doc, 1); }, 5000);
    // Performance API'yi gecikmeli tekrar tara (gec yuklenen kaynaklar)
    setTimeout(() => { if (this.running) this.scanPerformanceAPI(doc); }, 3000);
    setTimeout(() => { if (this.running) this.scanPerformanceAPI(doc); }, 8000);
  }

  destroy() {
    this.running = false;
    this.observers.forEach(o => { try { o.disconnect(); } catch {} });
    this.observers = [];
    if (this._origFetch) { window.fetch = this._origFetch; this._origFetch = null; }
    if (this._origOpen) { XMLHttpRequest.prototype.open = this._origOpen; this._origOpen = null; }
    if (this._origPushState) { history.pushState = this._origPushState; }
    if (this._origReplaceState) { history.replaceState = this._origReplaceState; }
    if (this._origCreateObjectURL) { URL.createObjectURL = this._origCreateObjectURL; this._origCreateObjectURL = null; }
    this.detectedUrls.clear();
    this._blobVideoMap.clear();
  }

  // ═══════ KATMAN 1d: PERFORMANCE API ═══════
  scanPerformanceAPI(doc) {
    if (!doc || !performance || !performance.getEntriesByType) return;
    try {
      const resources = performance.getEntriesByType('resource');
      for (const entry of resources) {
        // contentType safari'de olmayabilir, initiatorType her zaman var
        const ct = entry.contentType || '';
        const initType = entry.initiatorType || '';

        // MIME tipi video/audio mu?
        const isVideoAudioMime = ct.startsWith('video/') || ct.startsWith('audio/');

        // initiator type media mı?
        const isMediaInit = initType === 'media' || initType === 'audio' || initType === 'video';

        // URL'de video uzantısı var mı?
        const hasVideoExt = isVideoExtension(entry.name);

        if (!isVideoAudioMime && !isMediaInit && !hasVideoExt) continue;

        const url = entry.name || '';
        if (!url || url.startsWith('data:') || url.startsWith('blob:')) continue;
        if (isSegmentUrl(url)) continue;
        if (NON_VIDEO_EXTS.has(getFileExtension(url).replace('.', ''))) continue;
        if (VideoFilter.analyze(url, null).isAd) continue;

        this.evaluate(url, 'performance_api', null, null);
      }
    } catch (e) {
      // performance API bazı context'lerde kullanılamayabilir
    }
  }
  scanDOM(doc) {
    if (!doc) return;
    doc.querySelectorAll("video:not([data-af-scanned])").forEach(v => {
      v.dataset.afScanned = "1";
      this._analyzeVideoElement(v, doc);
    });
    doc.querySelectorAll("source[src]:not([data-af-scanned])").forEach(s => {
      s.dataset.afScanned = "1";
      if (s.src) this.evaluate(s.src, "source_tag", s);
    });
    doc.querySelectorAll("[data-src]").forEach(el => {
      const src = el.getAttribute("data-src");
      if (src && (isVideoExtension(src) || isManifestUrl(src))) this.evaluate(src, "data_src", el);
    });
    doc.querySelectorAll("[data-video]").forEach(el => {
      const src = el.getAttribute("data-video");
      if (src) this.evaluate(src, "data_video", el);
    });
    doc.querySelectorAll("[data-video-src],[data-stream-url],[data-hls-url],[data-dash-url],[data-setup],[data-file]").forEach(el => {
      let src = el.getAttribute("data-video-src") || el.getAttribute("data-stream-url") || el.getAttribute("data-hls-url") || el.getAttribute("data-dash-url");
      if (src) {
        this.evaluate(src, "data_attr", el);
        return;
      }
      // JW Player data-setup="{"file":"https://..."}"
      const setup = el.getAttribute("data-setup");
      if (setup) {
        try { this._extractJWConfigJSON(setup); } catch {}
      }
      // JW Player data-file="https://..."
      const file = el.getAttribute("data-file");
      if (file && (isVideoExtension(file) || isManifestUrl(file))) {
        this.evaluate(file, "data_file", el);
      }
    });
  }

  _analyzeVideoElement(video, doc) {
    const src = video.src || video.currentSrc || "";

    // Blob URL bile olsa raporla — onemli degisiklik
    if (src) {
      if (src.startsWith("blob:")) {
        this._trackBlobVideo(src, video);
      } else {
        const quality = VideoParser.detectQualityFromVideo(video);
        this.evaluate(src, "video_src", video, quality);
      }
    }

    // Video elementinin srcObject'u var mi (MediaSource/RemotePlayback)
    if (video.srcObject && video.srcObject instanceof MediaSource) {
      this._trackMediaSource(video);
    }

    video.querySelectorAll("source[src]").forEach(s => {
      if (s.src) this.evaluate(s.src, "video_source", s);
    });

    if (video.querySelector("iframe")) {
      setTimeout(() => this.scanIframes(video, 0), 500);
    }
  }

  // ═══════ KATMAN 1b: SHADOW DOM TARAMASI ═══════
  scanShadowRoots(root) {
    if (!root) return;
    const elements = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (const el of elements) {
      if (el.shadowRoot) {
        this.scanDOM(el.shadowRoot);
        this._scanShadowVideos(el.shadowRoot);
        this.scanShadowRoots(el.shadowRoot);
      }
    }
  }

  _scanShadowVideos(shadowRoot) {
    if (!shadowRoot) return;
    shadowRoot.querySelectorAll("video:not([data-af-scanned])").forEach(v => {
      v.dataset.afScanned = "1";
      this._analyzeVideoElement(v, shadowRoot);
    });
    shadowRoot.querySelectorAll("[data-src],[data-video-src],[data-stream-url]").forEach(el => {
      const src = el.getAttribute("data-src") || el.getAttribute("data-video-src") || el.getAttribute("data-stream-url");
      if (src) this.evaluate(src, "shadow_dom", el);
    });
  }

  // ═══════ KATMAN 1c: IFRAME SCAN ═══════
  scanIframes(doc, depth) {
    if (depth >= 3) return;
    const iframes = doc ? (doc.tagName === "IFRAME" ? [doc] : doc.querySelectorAll("iframe")) : [];
    for (const iframe of iframes) {
      try {
        const idoc = iframe.contentDocument || iframe.contentWindow?.document;
        if (!idoc) continue;
        this.scanDOM(idoc);
        this.scanShadowRoots(idoc);
        this.scanPlayers(iframe.contentWindow);
        this._scanJWPlayerScripts(idoc);
        setTimeout(() => this.scanIframes(idoc, depth + 1), 500);
      } catch {}
    }
  }

  // ═══════ KATMAN 2: NETWORK INTERCEPT ═══════
  interceptNetwork() {
    const self = this;
    this._origFetch = window.fetch;
    window.fetch = function(...args) {
      let url = "";
      if (typeof args[0] === "string") url = args[0];
      else if (args[0]?.url) url = args[0].url;
      else if (args[0] instanceof Request) url = args[0].url;
      if (url && self._isVideoRequest(url)) {
        self.evaluate(url, "fetch_intercept");
        if (url.includes(".m3u8")) {
          self._origFetch.apply(this, args).then(r => {
            if (r.ok) r.clone().text().then(t => self._onM3U8Fetched(t, url)).catch(() => {});
          }).catch(() => {});
        }
      }
      return self._origFetch.apply(this, args);
    };

    this._origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
      if (typeof url === "string" && self._isVideoRequest(url)) {
        self.evaluate(url, "xhr_intercept");
      }
      return self._origOpen.apply(this, arguments);
    };
  }

  _onM3U8Fetched(text, url) {
    if (!text || !text.includes("#EXT-X-STREAM-INF")) return;
    const baseUrl = url.substring(0, url.lastIndexOf("/") + 1);
    const variants = VideoParser.parseM3U8(text);
    variants.forEach(v => {
      let fullUrl = v.url;
      if (!fullUrl.startsWith("http")) fullUrl = baseUrl + fullUrl;
      this.evaluate(fullUrl, "m3u8_variant", null, v.quality);
    });
  }

  _isVideoRequest(url) {
    if (!url || url.startsWith("data:")) return false;
    if (/\.webmanifest(\?|#|$)/i.test(url)) return false;
    if (VideoFilter.analyze(url, null).isAd) return false;
    return isVideoExtension(url) || isManifestUrl(url) || /\/video\//i.test(url) || /\/manifest/i.test(url);
  }

  // ═══════ KATMAN 3: PLAYER API ═══════
  scanPlayers(win) {
    if (!win) return;
    this._checkJWPlayer(win);
    this._checkVideoJS(win);
    this._checkHLSjs(win);
    this._checkDashJS(win);
    this._checkPlyr(win);
    this._checkArtPlayer(win);
    this._checkFlowPlayer(win);
    this._checkClappr(win);
    this._checkMediaElement(win);
    this._checkShaka(win);
    this._checkBitmovin(win);
    this._checkCustomReactPlayers(win);
  }

  _checkJWPlayer(win) {
    try {
      if (!win.jwplayer) return;

      const self = this;

      // --- Monkey-patch setup() to intercept file/sources in real-time ---
      if (!this._jwSetupPatched) {
        this._jwSetupPatched = true;
        const origJwplayer = win.jwplayer.bind(win);
        win.jwplayer = function(id) {
          const inst = origJwplayer(id);
          if (inst && inst._af_patched) return inst;
          if (!inst || !inst.setup) return inst;

          const origSetup = inst.setup.bind(inst);
          inst.setup = function(config) {
            setTimeout(() => {
              try {
                if (config.file) self.evaluate(config.file, "jwplayer_setup_file");
                if (config.sources) {
                  (Array.isArray(config.sources) ? config.sources : [config.sources]).forEach(s => {
                    if (s.file) self.evaluate(s.file, "jwplayer_setup_source", null, s.label || "");
                  });
                }
                if (config.playlist) {
                  (Array.isArray(config.playlist) ? config.playlist : [config.playlist]).forEach(item => {
                    if (item.file) self.evaluate(item.file, "jwplayer_setup_pl_file");
                    (item.sources || []).forEach(s => {
                      if (s.file) self.evaluate(s.file, "jwplayer_setup_pl_source", null, s.label || "");
                    });
                  });
                }
              } catch {}
            }, 100);
            const result = origSetup(config);
            return result;
          };
          inst._af_patched = true;

          // --- Event listener: yakalanan playlist item ---
          try {
            inst.on("playlistItem", function(e) {
              try {
                if (e.item) {
                  if (e.item.file) self.evaluate(e.item.file, "jwplayer_event_file");
                  (e.item.sources || []).forEach(s => {
                    if (s.file) self.evaluate(s.file, "jwplayer_event_source", null, s.label || "");
                  });
                }
              } catch {}
            });
            inst.on("ready", function() {
              try {
                const cfg = inst.getConfig ? inst.getConfig() : null;
                if (cfg) {
                  if (cfg.file) self.evaluate(cfg.file, "jwplayer_cfg_file");
                  (cfg.sources || []).forEach(s => {
                    if (s.file) self.evaluate(s.file, "jwplayer_cfg_source", null, s.label || "");
                  });
                }
                const pl = inst.getPlaylist ? inst.getPlaylist() : [];
                pl.forEach(item => {
                  if (item.file) self.evaluate(item.file, "jwplayer_ready_file");
                  (item.sources || []).forEach(s => {
                    if (s.file) self.evaluate(s.file, "jwplayer_ready_source", null, s.label || "");
                  });
                });
              } catch {}
            });
          } catch {}

          return inst;
        };
      }

      // --- Mevcut: jwplayer() ile playlist okuma ---
      try {
        const p = win.jwplayer();
        if (p && p.getPlaylist) {
          const pl = p.getPlaylist();
          (pl || []).forEach(item => {
            if (item.file) this.evaluate(item.file, "jwplayer", null, item.label || "");
            (item.sources || []).forEach(s => {
              if (s.file) this.evaluate(s.file, "jwplayer_source", null, s.label || "");
            });
          });
        }
        if (p && p.getConfig) {
          try {
            const cfg = p.getConfig();
            if (cfg) {
              if (cfg.file) this.evaluate(cfg.file, "jwplayer_cfg");
              (cfg.sources || []).forEach(s => {
                if (s.file) this.evaluate(s.file, "jwplayer_cfg_source", null, s.label || "");
              });
            }
          } catch {}
        }
      } catch {}

      // --- DOM element üzerinden ---
      (win.document || document).querySelectorAll('[id^="player"], [id$="player"], .jwplayer, [class*="jwplayer"]').forEach(el => {
        if (!el.id) return;
        try {
          const jw = win.jwplayer(el.id);
          if (jw && jw.getPlaylist) {
            const pl = jw.getPlaylist();
            (pl || []).forEach(item => {
              if (item.file) this.evaluate(item.file, "jwplayer_el");
              (item.sources || []).forEach(s => {
                if (s.file) this.evaluate(s.file, "jwplayer_el_source", null, s.label || "");
              });
            });
          }
        } catch {}
      });
    } catch {}
  }

  _scanJWPlayerScripts(doc) {
    doc.querySelectorAll("script:not([data-af-jw-scanned])").forEach(s => {
      s.dataset.afJwScanned = "1";
      const text = s.textContent || "";
      if (!text.includes("jwplayer")) return;

      // Pattern 1: jwplayer("id").setup({ file: "...", sources: [...] })
      const setupRe = /jwplayer\s*\(\s*["'][^"']*["']\s*\)\s*\.\s*setup\s*\(\s*(\{[^}]*(?:\{[^}]*\}[^}]*)*\})\s*\)/g;
      let m;
      while ((m = setupRe.exec(text)) !== null) {
        try {
          this._extractJWConfigJSON(m[1]);
        } catch {}
      }

      // Pattern 2: var cfg = { file: "..." }; jwplayer("x").setup(cfg)
      const cfgRe = /(\{["']file["']\s*:\s*["'][^"']+["'][^}]*\})/g;
      while ((m = cfgRe.exec(text)) !== null) {
        try {
          this._extractJWConfigJSON(m[1]);
        } catch {}
      }

      // Pattern 3: sources array directly
      const srcRe = /\bsources?\s*:\s*(\[[^\]]*\])/g;
      while ((m = srcRe.exec(text)) !== null) {
        try {
          const arr = JSON.parse(m[1]);
          arr.forEach(s => { if (s.file) this.evaluate(s.file, "jwplayer_script_source", null, s.label || ""); });
        } catch {}
      }

      // Pattern 4: file: "url" inside JW config
      const fileRe = /["']file["']\s*:\s*["']([^"']*\.(?:m3u8|mp4|mpd|webm|mkv|flv|ts)[^"']*)["']/gi;
      while ((m = fileRe.exec(text)) !== null) {
        if (m[1]) this.evaluate(m[1], "jwplayer_script_file");
      }
    });
  }

  _extractJWConfigJSON(jsonStr) {
    let cfg;
    try {
      cfg = JSON.parse(jsonStr);
    } catch {
      // Try fixing common JS object notation issues
      const fixed = jsonStr
        .replace(/([{,]\s*)(\w+)(\s*:)/g, '$1"$2"$3')  // quote unquoted keys
        .replace(/'/g, '"');  // single to double quotes
      try { cfg = JSON.parse(fixed); } catch { return; }
    }
    if (!cfg) return;
    if (cfg.file) this.evaluate(cfg.file, "jwplayer_script_file");
    if (cfg.sources) {
      (Array.isArray(cfg.sources) ? cfg.sources : [cfg.sources]).forEach(s => {
        if (s.file) this.evaluate(s.file, "jwplayer_script_source", null, s.label || "");
      });
    }
    if (cfg.playlist) {
      (Array.isArray(cfg.playlist) ? cfg.playlist : [cfg.playlist]).forEach(item => {
        if (item.file) this.evaluate(item.file, "jwplayer_script_pl_file");
        (item.sources || []).forEach(s => {
          if (s.file) this.evaluate(s.file, "jwplayer_script_pl_source", null, s.label || "");
        });
      });
    }
  }

  _checkVideoJS(win) {
    try {
      if (!win.videojs) return;
      (win.document || document).querySelectorAll(".video-js").forEach(p => {
        if (!p.id) return;
        try {
          const vjs = win.videojs(p.id);
          if (vjs && vjs.src()) {
            const src = vjs.src();
            if (typeof src === "string") this.evaluate(src, "videojs");
            else if (Array.isArray(src) && src.length) this.evaluate(src[0].src, "videojs");
          }
        } catch {}
      });
    } catch {}
  }

  _checkHLSjs(win) {
    try {
      if (!win.Hls) return;
      // Hls nesnesinden kaynagi almaya calis
      const hlsInstances = win.Hls?.instances || [];
      for (const hls of hlsInstances) {
        if (hls.url) this.evaluate(hls.url, "hlsjs_instance");
      }
      (win.document || document).querySelectorAll("video").forEach(v => {
        if (v.src) this.evaluate(v.src, "hlsjs");
        if (v.currentSrc) this.evaluate(v.currentSrc, "hlsjs_curr");
      });
    } catch {}
  }

  _checkDashJS(win) {
    try {
      if (!win.dashjs) return;
      const mp = win.dashjs.MediaPlayer();
      if (!mp) return;
      const source = mp.getSource();
      if (source) {
        if (typeof source === "string") this.evaluate(source, "dashjs");
        else if (source.url) this.evaluate(source.url, "dashjs_url");
      }
    } catch {}
  }

  _checkPlyr(win) {
    try {
      if (!win.Plyr) return;
      (win.document || document).querySelectorAll(".plyr,.plyr__video-wrapper video").forEach(el => {
        try {
          const player = el.plyr || el.__plyr;
          if (player && player.source) {
            if (typeof player.source === "string") this.evaluate(player.source, "plyr");
            else if (player.source.sources) {
              player.source.sources.forEach(s => { if (s.src) this.evaluate(s.src, "plyr_source", null, s.label); });
            }
          }
        } catch {}
      });
    } catch {}
  }

  _checkArtPlayer(win) {
    try {
      if (!win.art) return;
      const a = win.art;
      if (a.video && a.video.src) this.evaluate(a.video.src, "artplayer");
      if (a.url) this.evaluate(a.url, "artplayer_url");
    } catch {}
  }

  _checkFlowPlayer(win) {
    try {
      if (!win.flowplayer) return;
      (win.document || document).querySelectorAll(".flowplayer,.fp-player").forEach(el => {
        try {
          const fp = el.flowplayer || win.flowplayer(el);
          if (fp && fp.video && fp.video.src) this.evaluate(fp.video.src, "flowplayer");
        } catch {}
      });
    } catch {}
  }

  _checkClappr(win) {
    try {
      if (!win.Clappr) return;
      (win.document || document).querySelectorAll("[data-player]").forEach(el => {
        try {
          if (el.player && el.player.options && el.player.options.source) {
            this.evaluate(el.player.options.source, "clappr");
          }
        } catch {}
      });
    } catch {}
  }

  _checkMediaElement(win) {
    try {
      (win.document || document).querySelectorAll("video").forEach(v => {
        try {
          if (v.mediaElementPlayer) {
            const src = v.mediaElementPlayer.media?.src;
            if (src) this.evaluate(src, "mediaelement");
          }
        } catch {}
      });
    } catch {}
  }

  _checkShaka(win) {
    try {
      if (!win.shaka) return;
      (win.document || document).querySelectorAll("video").forEach(v => {
        if (v.src) this.evaluate(v.src, "shaka");
        if (v.currentSrc) this.evaluate(v.currentSrc, "shaka_curr");
      });
    } catch {}
  }

  _checkBitmovin(win) {
    try {
      // Bitmovin Player
      if (win.bitmovin && win.bitmovin.player) {
        const players = Object.values(win.bitmovin.player.PlayerInstances || {});
        for (const p of players) {
          try {
            const src = p.getSource?.() || p.config?.source;
            if (src) {
              if (typeof src === "string") this.evaluate(src, "bitmovin");
              else if (src.hls) this.evaluate(src.hls, "bitmovin_hls");
              else if (src.dash) this.evaluate(src.dash, "bitmovin_dash");
              else if (src.url) this.evaluate(src.url, "bitmovin_url");
            }
          } catch {}
        }
      }
      // Bitmovin DOM
      (win.document || document).querySelectorAll("[class*='bitmovin'],[id*='bitmovin']").forEach(el => {
        try {
          const player = el.__bitmovinPlayer || el.player;
          if (player?.getSource) {
            const src = player.getSource();
            if (src?.hls) this.evaluate(src.hls, "bitmovin_dom_hls");
            if (src?.dash) this.evaluate(src.dash, "bitmovin_dom_dash");
          }
        } catch {}
      });
    } catch {}
  }

  _checkCustomReactPlayers(win) {
    try {
      // React/Vue player'lari icin video element'leri tekrar tara
      // Bazi player'lar global yerine __reactFiber$ veya __vue__ kullanir
      (win.document || document).querySelectorAll("video").forEach(v => {
        // React fiber'da player bilgisi olabilir
        const fiberKey = Object.keys(v).find(k => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$"));
        if (fiberKey) {
          let fiber = v[fiberKey];
          for (let i = 0; i < 20 && fiber; i++) {
            const props = fiber.memoizedProps || fiber.pendingProps || {};
            if (props.src) this.evaluate(props.src, "react_video_props");
            if (props.source) {
              if (typeof props.source === "string") this.evaluate(props.source, "react_video_source");
              else if (props.source.src) this.evaluate(props.source.src, "react_video_source_src");
            }
            fiber = fiber.return;
          }
        }
      });
    } catch {}
  }

  // ═══════ KATMAN 4: MUTATION OBSERVER ═══════
  observeMutations(doc) {
    const observer = new MutationObserver(mutations => {
      let shouldScan = false;
      let hasShadow = false;
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node.nodeType !== 1) continue;
          if (/^(VIDEO|SOURCE|IFRAME|EMBED|OBJECT)$/i.test(node.tagName)) { shouldScan = true; break; }
          if (node.querySelector && node.querySelector("video,source,iframe")) { shouldScan = true; break; }
          if (node.shadowRoot) { hasShadow = true; shouldScan = true; break; }
        }
        if (shouldScan) break;
      }
      if (shouldScan) {
        setTimeout(() => { this.scanDOM(doc); if (hasShadow) this.scanShadowRoots(doc); }, 800);
        setTimeout(() => this.scanPlayers(window), 2000);
        setTimeout(() => this.scanMediaSourceElements(document), 3000);
      }
    });
    observer.observe(doc.documentElement, { childList: true, subtree: true });
    this.observers.push(observer);
  }

  // ═══════ KATMAN 5: META TAGS ═══════
  scanMetaTags(doc) {
    const metaProps = ["og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"];
    metaProps.forEach(prop => {
      doc.querySelectorAll(`meta[property="${prop}"],meta[name="${prop}"]`).forEach(m => {
        if (m.content) this.evaluate(m.content, "meta_" + prop.split(":")[0]);
      });
    });

    doc.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
      try {
        const data = JSON.parse(s.textContent);
        const videos = [];
        const find = (obj) => {
          if (!obj || typeof obj !== "object") return;
          if (obj["@type"] === "VideoObject" && obj.contentUrl) videos.push(obj.contentUrl);
          if (Array.isArray(obj)) obj.forEach(find);
          else Object.values(obj).forEach(find);
        };
        find(data);
        videos.forEach(v => this.evaluate(v, "jsonld"));
      } catch {}
    });
  }

  // ═══════ KATMAN 6: BLOB URL INTERCEPT ═══════
  interceptBlobURLs() {
    const self = this;
    const origCreate = URL.createObjectURL;
    this._origCreateObjectURL = origCreate;

    URL.createObjectURL = function(blob) {
      const url = origCreate.call(this, blob);

      if (blob && blob.type) {
        // Video blob'u mu?
        if (blob.type.startsWith("video/") || blob.type === "audio/mp4" || blob.type.includes("mp4") || blob.type.includes("webm")) {
          self._blobVideoMap.set(url, { type: blob.type, createdAt: Date.now() });
          // Bagli video elementlerini bul
          setTimeout(() => self._findVideosForBlob(url), 500);
          setTimeout(() => self._findVideosForBlob(url), 2000);
          setTimeout(() => self._findVideosForBlob(url), 5000);
        }

        // MediaSource blob'u mu?
        if (blob.type === "video/mp4" || blob.type === "audio/mp4") {
          // MediaSource icin de kontrol et
          setTimeout(() => self._findVideosForBlob(url), 1000);
        }
      }

      return url;
    };
  }

  _trackBlobVideo(blobUrl, videoEl) {
    // Blob URL'li video elementini izle
    if (!this._blobVideoMap.has(blobUrl)) {
      this._blobVideoMap.set(blobUrl, { type: "unknown", createdAt: Date.now() });
    }

    const info = this._blobVideoMap.get(blobUrl);
    info.videoElement = videoEl;

    // Video oynuyor mu kontrol et
    if (videoEl.readyState > 0 || !videoEl.paused) {
      // Kalite bilgisi al
      const quality = VideoParser.detectQualityFromVideo(videoEl);
      // Blob URL'yi raporla ama segment olmadigindan emin ol
      if (!isSegmentUrl(blobUrl)) {
        this.evaluate(blobUrl, "blob_video_element", videoEl, quality);
      }
    }

    // readyState degisikliklerini izle
    const onReady = () => {
      if (videoEl.readyState >= 2 && !isSegmentUrl(blobUrl)) {
        const quality = VideoParser.detectQualityFromVideo(videoEl);
        this.evaluate(blobUrl, "blob_video_ready", videoEl, quality);
      }
    };
    videoEl.addEventListener("loadeddata", onReady, { once: false });
    videoEl.addEventListener("canplay", onReady, { once: false });
  }

  _findVideosForBlob(blobUrl) {
    if (!this.running) return;
    // Sayfadaki tum video elementlerini tara
    document.querySelectorAll("video").forEach(v => {
      if (v.src === blobUrl || v.currentSrc === blobUrl) {
        this._trackBlobVideo(blobUrl, v);
      }
    });
    // Shadow DOM'dakileri de tara
    this._scanShadowForBlob(document, blobUrl);
  }

  _scanShadowForBlob(root, blobUrl) {
    if (!root) return;
    const els = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (const el of els) {
      if (el.shadowRoot) {
        el.shadowRoot.querySelectorAll("video").forEach(v => {
          if (v.src === blobUrl || v.currentSrc === blobUrl) {
            this._trackBlobVideo(blobUrl, v);
          }
        });
        this._scanShadowForBlob(el.shadowRoot, blobUrl);
      }
    }
  }

  // ═══════ KATMAN 7: MEDIA SOURCE ELEMENTS ═══════
  scanMediaSourceElements(doc) {
    if (!doc) return;
    doc.querySelectorAll("video").forEach(v => {
      if (!v.srcObject) return;

      // MediaSource
      if (typeof MediaSource !== "undefined" && v.srcObject instanceof MediaSource) {
        this._trackMediaSource(v);
      }

      // Remote Playback API
      if (v.remote) {
        try {
          const remoteSrc = v.remote?.current?.url;
          if (remoteSrc) this.evaluate(remoteSrc, "remote_playback");
        } catch {}
      }

      // video.getVideoPlaybackQuality - aktif video mu?
      try {
        const quality = v.getVideoPlaybackQuality?.();
        if (quality && (quality.totalVideoFrames > 0 || quality.corruptedVideoFrames >= 0)) {
          // Aktif video element - readyState kontrolu
          if (v.readyState >= 2) {
            const src = v.src || v.currentSrc || "";
            if (src && !src.startsWith("blob:")) {
              this.evaluate(src, "active_video_check", v);
            }
          }
        }
      } catch {}
    });
  }

  _trackMediaSource(videoEl) {
    // MediaSource baglantisi varsa, ilgili segment URL'lerini bulmaya calis
    try {
      const ms = videoEl.srcObject;
      if (!ms || !ms.sourceBuffers) return;

      // Source buffer'lardaki URL'leri kontrol et
      for (let i = 0; i < ms.sourceBuffers.length; i++) {
        const sb = ms.sourceBuffers[i];
        if (sb.segmentLoader_?.stats_?.url) {
          this.evaluate(sb.segmentLoader_.stats_.url, "mediasource_segment");
        }
      }
    } catch {}

    // Video elementinin URL'sini raporla (blob olsa bile)
    const src = videoEl.src || videoEl.currentSrc || "";
    if (src) {
      if (!isSegmentUrl(src)) {
        const quality = VideoParser.detectQualityFromVideo(videoEl);
        this.evaluate(src, "mediasource_video", videoEl, quality);
      }
    }
  }

  // ═══════ ÖZEL: SPA ═══════
  observeSPA() {
    this._origPushState = history.pushState;
    const self = this;
    history.pushState = function(...args) {
      self._origPushState.apply(this, args);
      self._onURLChange();
    };
    this._origReplaceState = history.replaceState;
    history.replaceState = function(...args) {
      self._origReplaceState.apply(this, args);
      self._onURLChange();
    };
    window.addEventListener("popstate", () => this._onURLChange());
    window.addEventListener("hashchange", () => this._onURLChange());
  }

  _onURLChange() {
    if (!this.running) return;
    this.detectedUrls.clear();
    setTimeout(() => {
      this.scanDOM(document);
      this.scanShadowRoots(document);
      this.scanPlayers(window);
      this.scanMediaSourceElements(document);
      this._scanJWPlayerScripts(document);
      setTimeout(() => this.scanIframes(document, 0), 2000);
    }, 1000);
  }

  // ═══════ DEĞERLENDİRME ═══════
  evaluate(url, source, element, qualityHint) {
    if (!url) return;
    if (url.startsWith("//")) url = location.protocol + url;

    const normalized = url.split("?")[0].split("#")[0];
    if (this.detectedUrls.has(normalized)) return;

    // DRM domain kontrolu
    if (VideoDetector.isDRMDomain(location.hostname)) {
      chrome.runtime.sendMessage({
        type: "DRM_DETECTED",
        data: { pageUrl: location.href, hostname: location.hostname, url: url },
      }).catch(() => {});
    }

    // Blob URL'ler icin ozel filtre
    if (url.startsWith("blob:")) {
      // Blob URL segment mi kontrol et
      if (isSegmentUrl(url)) return;
      // Blob URL video kaynagi mi?
      if (!this._blobVideoMap.has(url) && !element) return;
    }

    if (VideoFilter.analyze(url, element).isAd) return;
    if (isSegmentUrl(url)) return;

    // Non-blob URL'ler icin video kontrolu
    if (!url.startsWith("blob:")) {
      if (!this._isVideoRequest(url) && !isManifestUrl(url)) return;
    }

    const ext = getFileExtension(url).replace(".", "");
    if (NON_VIDEO_EXTS.has(ext)) return;

    this.detectedUrls.add(normalized);

    let quality = qualityHint || VideoParser.detectQualityFromURL(url) || "";
    if (!quality && element) quality = VideoParser.detectQualityFromVideo(element);

    const videoData = {
      id: generateId(),
      pageUrl: location.href,
      videoUrl: url,
      title: cleanTitle(document.title || ""),
      source: source || "unknown",
      videoType: getVideoType(url),
      quality: quality,
      fileSize: 0,
      hostname: location.hostname,
      detectedAt: Date.now(),
      tabId: this.tabId,
    };

    this._report(videoData);

    if (element && element.tagName === "VIDEO") {
      this._addOverlayIfNeeded(element);
    }
  }

  _report(data) {
    try {
      chrome.runtime.sendMessage({
        type: "VIDEO_DETECTED",
        data: data,
        tabId: this.tabId,
      }).catch(() => {});
    } catch {}
  }

  _addOverlayIfNeeded(videoEl) {
    if (videoEl.dataset.afOverlay) return;
    videoEl.dataset.afOverlay = "1";

    try {
      const parent = videoEl.parentElement;
      if (!parent) return;
      if (getComputedStyle(parent).position === "static") parent.style.position = "relative";

      const btn = document.createElement("div");
      btn.innerHTML = `<div style="position:absolute;top:8px;left:8px;z-index:2147483646;
        background:linear-gradient(135deg,#8b5cf6,#10b981);color:#fff;padding:4px 8px;
        border-radius:6px;cursor:pointer;font-size:11px;font-family:Arial,sans-serif;
        opacity:0.7;transition:opacity 0.2s;white-space:nowrap;user-select:none;"
        onmouseenter="this.style.opacity='1'" onmouseleave="this.style.opacity='0.7'"
        title="AniFrame ile indir">📥 AniFrame</div>`;
      btn.querySelector("div").onclick = function(e) {
        e.stopPropagation();
        e.preventDefault();
        const src = videoEl.src || videoEl.currentSrc || "";
        chrome.runtime.sendMessage({
          type: "DOWNLOAD_REQUEST",
          data: {
            videoUrl: src,
            pageUrl: location.href,
            title: document.title,
            hostname: location.hostname,
            quality: VideoParser.detectQualityFromVideo(videoEl),
          },
          priority: "normal",
        }).catch(() => {});
      };
      parent.appendChild(btn.firstElementChild);
    } catch {}
  }

  // ═══════ DRM DETECTION ═══════
  isDRMProtected() {
    // EME API check
    if (typeof navigator !== "undefined" && navigator.requestMediaKeySystemAccess) {
      return true;
    }
    // MediaKeys on video
    const videos = document.querySelectorAll("video");
    for (const v of videos) {
      if (v.mediaKeys) return true;
      // Check for encrypted events
      if (v.getAttribute("media-encrypted") === "") return true;
      // Check src contains widevine/playready
      const src = (v.src || v.currentSrc || "").toLowerCase();
      if (src.includes("widevine") || src.includes("playready") || src.includes("fairplay") || src.includes("drm")) return true;
    }
    // Check page content for DRM keywords
    try {
      const html = (document.documentElement.outerHTML || "").toLowerCase();
      if (html.includes("requestmediakeysystemaccess") || html.includes("widevine") || html.includes("playready")) return true;
      if (html.includes("drm") && (html.includes("ndev") || html.includes("evaluator"))) return true;
    } catch {}
    return false;
  }

  static DRM_DOMAINS = new Set([
    "netflix.com", "disneyplus.com", "hulu.com", "amazon.com", "primevideo.com",
    "hbomax.com", "max.com", "peacocktv.com", "paramountplus.com",
    "tv.apple.com", "hotstar.com", "crunchyroll.com", "spotify.com",
    "nowtv.com", "stan.com.au", "binge.com.au", "apple.com",
  ]);

  static isDRMDomain(hostname) {
    const h = (hostname || "").toLowerCase();
    return Array.from(this.DRM_DOMAINS).some(d => h.includes(d));
  }
}
