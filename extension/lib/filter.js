"use strict";

// ════════════════════════════════════════════
// AniFrame - Smart Ad/Tracker Filter v2
// Yumusatilmis: streaming CDN'leri haksiz yere filtrelenmesin
// ════════════════════════════════════════════

class VideoFilter {
  // Sadece gercek reklam domainleri — genel analytics/tracking/telemetry kaldirildi
  static AD_DOMAINS = [
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "googleads.g.doubleclick.net", "pagead2.googlesyndication.com",
    "adservice.google.com", "imasdk.googleapis.com", "pubads.g.doubleclick.net",
    "securepubads.g.doubleclick.net", "tpc.googlesyndication.com",
    "adserver.com", "adnxs.com", "rubiconproject.com", "openx.net",
    "criteo.com", "criteo.net", "taboola.com", "outbrain.com",
    "adsrvr.org", "adsymptotic.com", "adzerk.net", "moatads.com",
    "scorecardresearch.com",
    "adsafeprotected.com", "advertising.com", "serving-sys.com",
    "casalemedia.com", "turn.com", "mathtag.com", "bluekai.com",
    "exelator.com", "demdex.net", "krxd.net", "1rx.io",
    "addthis.com", "sharethis.com",
    "amazon-adsystem.com", "aax-us-east.amazon-adsystem.com",
    "fls-na.amazon.com", "device-metrics-us.amazon.com",
    "adroll.com", "adtrue.com",
    "tiqcdn.com", "ensighten.com", "everesttech.net",
    "media.net", "revcontent.com", "mgid.com",
    "propellerads.com", "popads.net", "adcash.com",
    "bidswitch.net", "adform.net", "smartadserver.com",
    "smaato.net", "inmobi.com", "applovin.com",
  ];

  static AD_URL_PATTERNS = [
    /\/ad[s]?(\d+)?\//i, /\/advert/i, /\/adv\//i,
    /\/adservice\//i, /\/prebid\//i, /\/amazon-adsystem/i,
    /\/video[_-]?ad[s]?\//i, /\/preroll/i, /\/midroll/i, /\/postroll/i,
    /\.ad\./i, /[_-]ad[_-]/i, /advertisement/i,
    /\/banner\/pop/i, /\/popunder\//i,
    /\/interstitial\//i, /\/outbrain/i, /\/taboola/i,
  ];

  static AD_CONTAINER_SELECTORS = [
    "[id*='google_ads']", "[class*='google_ads']",
    ".adsbygoogle", ".ad-slot", ".ad-unit",
    "[class*='banner-ad']",
    ".advert", ".ad-wrapper", ".ad-placement",
    "[data-ad]", "[data-ads]",
  ];

  static isAdDomain(url) {
    if (!url) return false;
    const lower = url.toLowerCase();
    return this.AD_DOMAINS.some(d => lower.includes(d.toLowerCase()));
  }

  static matchesAdPattern(url) {
    if (!url) return false;
    return this.AD_URL_PATTERNS.some(p => p.test(url));
  }

  static isInAdContainer(element) {
    if (!element) return false;
    let el = element;
    let depth = 0;
    while (el && depth < 6) {
      if (el.matches) {
        for (const sel of this.AD_CONTAINER_SELECTORS) {
          try { if (el.matches(sel)) return true; } catch {}
        }
      }
      if (el instanceof ShadowRoot) el = el.host;
      else el = el.parentElement;
      depth++;
    }
    return false;
  }

  // Boyut filtresi: blob URL'li videolar icin devre disi birak
  static isAdBySize(element) {
    if (!element) return false;
    // Blob URL'li video buyuk olcucle oynuyor olabilir — boyut kontrolunu atla
    const src = element.src || element.currentSrc || "";
    if (src.startsWith("blob:")) return false;
    // MediaSource kullanan video icin de atla
    if (element.srcObject && typeof MediaSource !== "undefined" && element.srcObject instanceof MediaSource) return false;

    const w = element.videoWidth || element.offsetWidth || 0;
    const h = element.videoHeight || element.offsetHeight || 0;
    return (w > 0 && w < 350) || (h > 0 && h < 250);
  }

  static analyze(url, element) {
    const result = { isAd: false, confidence: 0, reasons: [] };

    if (this.isAdDomain(url)) {
      result.confidence += 0.6;
      result.reasons.push("ad_domain");
    }
    if (this.matchesAdPattern(url)) {
      result.confidence += 0.4;
      result.reasons.push("ad_pattern");
    }
    if (element && this.isInAdContainer(element)) {
      result.confidence += 0.5;
      result.reasons.push("ad_container");
    }
    if (element && this.isAdBySize(element)) {
      result.confidence += 0.3;
      result.reasons.push("ad_size");
    }
    if (url && /\.(svg|jpg|jpeg|png|gif|webp|bmp|ico)(\?|$)/i.test(url)) {
      result.confidence += 1.0;
      result.reasons.push("image_url");
    }

    // Blob URL'ler icin esik degerini yukselt — cogu zaman video kaynagidir
    if (url && url.startsWith("blob:")) {
      result.isAd = result.confidence > 1.5;
    } else {
      result.isAd = result.confidence > 0.5;
    }
    return result;
  }
}
