import time
import re
import random
import os
import json
import hashlib
from urllib.parse import urlsplit

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".m3u8", ".ts", ".flv", ".avi", ".mov", ".wmv", ".m4v", ".mpd", ".ogv", ".3gp"}
NON_VIDEO_EXTS = {".svg", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico", ".css", ".js", ".html", ".htm", ".php", ".xml"}
VIDEO_MIMES_LOWER = {
    "video/mp4", "video/webm", "video/x-flv", "video/ogg", "video/x-msvideo",
    "application/x-mpegurl", "application/vnd.apple.mpegurl", "application/dash+xml",
    "video/mp2t", "video/quicktime", "video/x-matroska",
}
EMBED_KEYWORDS = [
    "player", "video", "embed", "stream", "watch", "play", "iframe",
    "mp4upload", "vidmoly", "voe", "dood", "streamtape", "mixdrop",
    "vudeo", "fembed", "sbembed", "dailymotion", "cdn", "hls",
    "cloudflarestream", "gounlimited", "streamsb", "supervideo",
]
PLAY_SELECTORS = [
    ".vjs-big-play-button",
    ".plyr__control--large",
    ".jw-icon-display",
    ".jw-icon.jw-icon-play",
    ".play-button",
    ".big-play-button",
    ".video-play-btn",
    '[aria-label="Play"]',
    '[aria-label="Oynat"]',
    ".jwplayer",
    "video[controls]",
    ".player-overlay",
    ".video-poster",
    ".video-overlay",
    ".play-overlay",
    ".overlay-play",
    ".clappr-icon--play",
    ".fp-icon.fp-playbtn",
    ".mejs__play-button",
    ".mejs-overlay-play",
    ".ytp-large-play-button",
    ".bmpui-playback-toggle-play",
    ".shaka-play-button",
    'button[title="Play"]',
    'button[title="Oynat"]',
]
POPUP_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "[data-testid='cookie-consent-accept']",
    ".cc-btn.cc-dismiss",
    ".cc-accept",
    ".cookie-consent-accept",
    "[data-consent='accept']",
    ".age-gate-accept",
    ".age-gate-yes",
    "[class*='cookie'] button[class*='accept']",
    "[class*='cookie'] button:first-child",
    ".modal .btn-primary",
    ".popup .close",
    ".overlay .close",
    ".ad-overlay",
    ".video-ad-overlay",
]
TITLE_SELECTORS = [
    'meta[property="og:title"]',
    'meta[name="og:title"]',
    'meta[name="title"]',
    'meta[name="twitter:title"]',
    'meta[itemprop="name"]',
    "h1",
    "h2.title",
    ".video-title",
    ".entry-title",
]
DRM_DOMAINS = {
    "netflix.com", "disneyplus.com", "disney-plus", "hulu.com",
    "amazon.com", "primevideo.com", "hbomax.com", "max.com",
    "peacocktv.com", "paramountplus.com", "appletv", "hotstar.com",
}

STEALTH_SCRIPT = """
// Override webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Chrome runtime
window.chrome = { runtime: {} };

// Plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' }))
});

// Languages
Object.defineProperty(navigator, 'languages', { get: () => ['tr-TR', 'tr', 'en-US', 'en'] });

// Platform
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// Hardware concurrency
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// Device memory
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

// WebGL vendor
try {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return getParam.call(this, p);
    };
} catch(e) {}

// Permissions override
try {
    const origQuery = navigator.permissions.query;
    navigator.permissions.query = (params) => {
        if (params.name === 'notifications') return Promise.resolve({ state: 'denied' });
        return origQuery(params);
    };
} catch(e) {}
"""

AD_PATTERNS = [
    r"doubleclick\.net",
    r"googleadservices\.com",
    r"googlesyndication\.com",
    r"google-analytics\.com",
    r"googletagmanager\.com",
    r"pagead2\.googlesyndication",
    r"adservice\.google",
    r"securepubads\.g\.doubleclick",
    r"cm\.g\.doubleclick",
    r"bid\.g\.doubleclick",
    r"exoclick\.com",
    r"popads\.net",
    r"popad\.net",
    r"popcash\.net",
    r"adsterra\.com",
    r"propellerads\.com",
    r"trafficfactory\.biz",
    r"adf\.ly",
    r"adnxs\.com",
    r"pubmatic\.com",
    r"openx\.net",
    r"appnexus\.com",
    r"rubiconproject\.com",
    r"casalemedia\.com",
    r"moatads\.com",
    r"adsrvr\.org",
    r"adzerk\.net",
    r"outbrain\.com",
    r"taboola\.com",
    r"revcontent\.com",
    r"mgid\.com",
    r"criteo\.com",
    r"amazon-adsystem\.com",
    r"bidswitch\.net",
    r"adsafeprotected\.com",
    r"bluekai\.com",
    r"demdex\.net",
    r"krxd\.net",
    r"media\.net",
    r"sovrn\.com",
    r"contextweb\.com",
    r"indexww\.com",
    r"1rx\.io",
    r"owneriq\.net",
    r"facebook\.com/tr",
    r"facebook\.net",
    r"ads\.yahoo\.com",
    r"ads\.yimg\.com",
    r"ad-delivery\.net",
    r"an\.yandex\.ru",
    r"mc\.yandex\.ru",
    r"adriver\.ru",
    r"adliver\.com",
    r"/pop(up|under|ad)",
    r"exoclick",
    r"popunder",
    r"popcash",
    r"exoclk",
]


def _should_block_request(url):
    url_lower = url.lower()
    for p in AD_PATTERNS:
        if re.search(p, url_lower):
            return True
    return False


def _is_drm_detected(page, url):
    url_lower = url.lower()
    for d in DRM_DOMAINS:
        if d in url_lower:
            return True
    return False


def _is_non_video_url(raw_url):
    clean = raw_url.split("?")[0].lower()
    ext = "." + clean.rsplit(".", 1)[-1] if "." in clean else ""
    return ext in NON_VIDEO_EXTS


def _handle_route(route):
    """Block ad/tracking requests, allow everything else."""
    url = route.request.url
    resource_type = route.request.resource_type
    if resource_type == "document":
        route.continue_()
        return
    if _should_block_request(url):
        route.abort("blockedbyclient")
        return
    route.continue_()


def _parse_cookie_header(raw_cookie, target_url):
    """Ham 'k=v; k2=v2' cerez basligini Playwright add_cookies formatina cevir.
    Domain/path bilgisi olmadigi icin hedef URL'nin origin'ini kullanir."""
    cookies = []
    try:
        parts = urlsplit(target_url)
        domain = parts.hostname
        if not domain:
            return cookies
        for pair in raw_cookie.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name = name.strip()
            if not name:
                continue
            cookies.append({
                "name": name,
                "value": value.strip(),
                "domain": domain,
                "path": "/",
            })
    except Exception:
        pass
    return cookies


def _origin_of(u):
    try:
        p = urlsplit(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


_CF_STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cf_state")
CF_CHALLENGE_MARKERS = (
    "just a moment", "cf-chl", "challenge-platform", "cf_chl_opt",
    "checking your browser", "attention required", "enable javascript and cookies",
    "cf-browser-verification", "__cf_chl_",
)


def _cf_state_path(domain):
    h = hashlib.sha1(domain.encode()).hexdigest()[:16]
    return os.path.join(_CF_STATE_DIR, f"{h}.json")


def _load_cf_storage_state(domain):
    """Onceki basarili/denenmis oturumdan cf_clearance vb. cerezleri geri yukle.
    Best-effort: dosya yoksa/bozuksa None doner, yeni oturum acilir."""
    try:
        path = _cf_state_path(domain)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_cf_storage_state(domain, context):
    """Cloudflare cerezlerini (cf_clearance dahil) diske yaz; sonraki cagrida
    challenge'i atlamaya yardimci olur. Basarisiz olursa sessizce gec."""
    try:
        os.makedirs(_CF_STATE_DIR, exist_ok=True)
        state = context.storage_state()
        with open(_cf_state_path(domain), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _is_cloudflare_challenge(page):
    """Sayfa icerigi Cloudflare 'Just a moment' / bot-check challenge'ina mi
    benziyor kontrol et. Baslik+govde metnini kucuk harfe cevirip bilinen
    isaretlerle kiyaslar."""
    try:
        title = (page.title() or "").lower()
        if any(m in title for m in ("just a moment", "attention required")):
            return True
        body = page.evaluate("() => document.body ? document.body.innerText.slice(0, 2000) : ''") or ""
        body_lower = body.lower()
        html = page.content()[:3000].lower()
        return any(m in body_lower or m in html for m in CF_CHALLENGE_MARKERS)
    except Exception:
        return False


def _wait_out_cloudflare(page, max_wait_ms=20000):
    """Cloudflare JS challenge cozulene kadar (veya zaman asimina kadar) bekle.
    Gercek tarayicida bircok JS-tabanli challenge birkaç saniyede otomatik
    cozulur; headless'ta bu garanti degildir (best-effort)."""
    if not _is_cloudflare_challenge(page):
        return False
    waited = 0
    step = 1000
    while waited < max_wait_ms:
        try:
            page.wait_for_timeout(step)
        except Exception:
            break
        waited += step
        if not _is_cloudflare_challenge(page):
            return True
    return not _is_cloudflare_challenge(page)


def extract_from_page(url, timeout_ms=30000, referer="", cookies="", _depth=0):
    if not HAS_PLAYWRIGHT:
        return {"error": "Playwright not installed. Run: pip install playwright && playwright install chromium"}

    embed_candidates = []

    # Embed barindiran siteler (rapidvid, vb.) dogru Referer olmadan bos sayfa
    # dondurur. referer verilmediyse embed URL'nin kendi origin'ini kullan.
    if not referer:
        try:
            p = urlsplit(url)
            if p.scheme and p.netloc:
                referer = f"{p.scheme}://{p.netloc}/"
        except Exception:
            referer = ""

    result = {"title": "", "video_url": "", "thumbnail": "", "duration": 0, "formats": []}
    captured_urls = []
    browser = None
    diagnostics = {
        "http_status": None,
        "page_loaded": False,
        "video_tags_found": 0,
        "iframes_scanned": 0,
        "media_requests_seen": 0,
        "blocked_requests": 0,
        "drm_keywords_found": False,
    }
    blocked_counter = [0]
    network_media_counter = [0]
    body_scan_counter = [0]
    MAX_BODY_SCANS = 40

    def _scan_response_body_for_media_urls(response):
        """Generic fallback: many embed players (legitimate ones included -
        JW Player config endpoints, self-hosted CDN widgets, LMS video
        components) load the real source through a JSON/JS response fetched
        via XHR rather than a <video src> or an obviously-named URL. Scan the
        body text for embedded absolute URLs ending in a known media
        extension. This only *widens what counts as found* the same way
        Video DownloadHelper-style extensions do; it does nothing if the URL
        itself is encrypted, tokenized client-side, or otherwise obfuscated."""
        if body_scan_counter[0] >= MAX_BODY_SCANS:
            return
        body_scan_counter[0] += 1
        try:
            body = response.text()
        except Exception:
            return
        if len(body) > 2_000_000:
            body = body[:2_000_000]
        for m in re.finditer(
            r'https?://[^\s"\'<>\\]+?\.(?:m3u8|mp4|mpd|webm|ts)(?:\?[^\s"\'<>\\]*)?',
            body,
        ):
            found_url = m.group(0)
            if not _should_skip_url(found_url):
                captured_urls.append({"url": found_url, "type": "response_body_scan", "priority": True})
                network_media_counter[0] += 1

    def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            response_url = response.url
            if _is_non_video_url(response_url):
                return
            ct_lower = ct.lower()
            if any(m in ct_lower for m in VIDEO_MIMES_LOWER):
                captured_urls.append({
                    "url": response_url, "type": "mime_match", "priority": True,
                    "content_type": ct_lower,
                })
                network_media_counter[0] += 1
            elif any(kw in response_url.lower() for kw in ["m3u8", ".mp4", ".ts?", "manifest", ".mpd", "stream", "playlist"]):
                captured_urls.append({
                    "url": response_url, "type": "url_pattern_match", "priority": True,
                    "content_type": ct_lower,
                })
                network_media_counter[0] += 1
            elif any(t in ct_lower for t in ("json", "javascript", "text/plain")):
                _scan_response_body_for_media_urls(response)
        except Exception:
            pass

    def on_request(request):
        try:
            req_url = request.url
            if _is_non_video_url(req_url):
                return
            url_lower = req_url.lower()
            if any(kw in url_lower for kw in [".m3u8", ".mpd", "master.json", "manifest"]):
                captured_urls.append({"url": req_url, "type": "request_manifest", "priority": True})
                network_media_counter[0] += 1
            elif any(kw in url_lower for kw in [".mp4?", ".webm?", ".ts?"]):
                captured_urls.append({"url": req_url, "type": "request_video", "priority": True})
        except Exception:
            pass

    try:
        with sync_playwright() as pw:
            viewport_w = random.randint(1700, 2100)
            viewport_h = random.randint(900, 1100)
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu",
                    "--mute-audio",
                    "--autoplay-policy=no-user-gesture-required",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            page_domain = urlsplit(url).hostname or ""
            cf_storage_state = _load_cf_storage_state(page_domain) if page_domain else None

            context_kwargs = dict(
                user_agent=f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": viewport_w, "height": viewport_h},
                ignore_https_errors=True,
                bypass_csp=True,
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"},
            )
            if cf_storage_state:
                # Onceki oturumdan cf_clearance vb. cerezleri geri yukle
                # (Cloudflare challenge'ini bazen atlamaya yardimci olur).
                context_kwargs["storage_state"] = cf_storage_state
            context = browser.new_context(**context_kwargs)

            context.add_init_script(STEALTH_SCRIPT)

            if cookies:
                cookie_objs = _parse_cookie_header(cookies, url)
                if cookie_objs:
                    try:
                        context.add_cookies(cookie_objs)
                    except Exception:
                        pass

            def _handle_route_counted(route):
                if _should_block_request(route.request.url) and route.request.resource_type != "document":
                    blocked_counter[0] += 1
                _handle_route(route)

            context.route("**/*", _handle_route_counted)

            page = context.new_page()
            page.on("response", on_response)
            page.on("request", on_request)

            try:
                goto_resp = page.goto(url, timeout=timeout_ms, wait_until="load", referer=referer or None)
                diagnostics["http_status"] = goto_resp.status if goto_resp else None
                diagnostics["page_loaded"] = True
                if goto_resp and goto_resp.status in (403, 404, 401):
                    # Page returned forbidden - try with extra headers
                    page.close()
                    page = context.new_page()
                    page.set_extra_http_headers({
                        "Referer": referer or url,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "cross-site" if referer else "none",
                    })
                    page.on("response", on_response)
                    page.on("request", on_request)
                    try:
                        goto_resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded", referer=referer or None)
                        diagnostics["http_status"] = goto_resp.status if goto_resp else diagnostics["http_status"]
                    except PlaywrightTimeout:
                        pass
            except PlaywrightTimeout:
                try:
                    goto_resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded", referer=referer or None)
                    diagnostics["http_status"] = goto_resp.status if goto_resp else None
                    diagnostics["page_loaded"] = True
                except PlaywrightTimeout:
                    return {"error": "Sayfa yuklenemedi (timeout)", "diagnostics": diagnostics}

            # Cloudflare "Just a moment" challenge tespit edilirse cozulmesini bekle
            # (best-effort; headless'ta garanti degil). Basarili olursa cerezleri
            # kaydet ki sonraki cagrida challenge tekrarlanma ihtimali azalsin.
            diagnostics["cloudflare_challenge"] = False
            if _is_cloudflare_challenge(page):
                diagnostics["cloudflare_challenge"] = True
                cleared = _wait_out_cloudflare(page, max_wait_ms=20000)
                diagnostics["cloudflare_cleared"] = cleared
                if page_domain:
                    _save_cf_storage_state(page_domain, context)
                if not cleared:
                    return {"error": "Cloudflare bot korumasi asilamadi (challenge cozulmedi). Site erisimi engelliyor olabilir.", "diagnostics": diagnostics}
            elif page_domain:
                _save_cf_storage_state(page_domain, context)

            if _is_drm_detected(page, url):
                return {"error": "DRM korumasi algilandi. Bu site (Netflix/Disney+ vb.) korumali icerik sagliyor. DRM korumali videolar indirilemez.", "diagnostics": diagnostics}

            _dismiss_popups(page)
            _try_click_play(page)
            _wait_for_video(page, timeout_ms=20000)

            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeout:
                pass

            _auto_scroll(page)
            time.sleep(2)

            _extract_title(page, result, url)
            _extract_thumbnail(page, result)

            _scan_main_page(page, result, captured_urls)
            _scan_all_iframes(page, result, captured_urls, depth=0, max_depth=4, diagnostics=diagnostics)
            _scan_network_captures(captured_urls, result)

            # Stream bulunamadiysa, sayfada tespit edilen embed iframe URL'lerini
            # (rapidvid vb. baska origin'deki oynatici sayfalari) aday olarak topla.
            # Playwright iframe'i cogu zaman about:blank birakir; embed'i DOGRUDAN,
            # asil sayfayi Referer vererek acmak gercek stream'i ortaya cikarir.
            if not result.get("formats") and not result.get("video_url"):
                page_origin = _origin_of(url)
                for c in captured_urls:
                    ctype = c.get("type", "")
                    cand = c.get("url", "")
                    if not ctype.startswith("iframe"):
                        continue
                    if not cand.startswith("http"):
                        continue
                    if _origin_of(cand) == page_origin:
                        continue
                    if _should_block_request(cand):
                        continue
                    if _should_skip_url(cand):
                        continue
                    if cand not in embed_candidates:
                        embed_candidates.append(cand)

            _try_parse_manifest(result)
            _expand_hls_formats(result, referer=referer, cookies=cookies)

            diagnostics["video_tags_found"] = _count_video_tags(page)
            diagnostics["media_requests_seen"] = len(captured_urls)
            diagnostics["blocked_requests"] = blocked_counter[0]
            diagnostics["drm_keywords_found"] = _scan_for_drm_keywords(page)

    except PlaywrightTimeout:
        result["error"] = "Sayfa yukleme zaman asimi."
    except Exception as e:
        msg = str(e)
        if "net::ERR" in msg:
            result["error"] = "Ag hatasi: Siteye erisilemedi."
        elif "Target closed" in msg:
            result["error"] = "Tarayici kapatildi."
        else:
            result["error"] = f"Tarayici hatasi: {msg[:200]}"
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    result["diagnostics"] = diagnostics

    # Asil sayfada stream cikmadi ama embed oynatici URL'leri bulunduysa,
    # embed'i dogrudan (asil sayfayi Referer vererek) yeni bir Playwright
    # oturumunda ac. Bu, rapidvid gibi barindiricilardaki gercek m3u8/mp4'u
    # ortaya cikarir. _depth ile sonsuz ozyineleme engellenir.
    if (not result.get("formats") and not result.get("video_url")
            and embed_candidates and _depth < 2):
        for cand in embed_candidates[:3]:
            sub = extract_from_page(cand, timeout_ms=timeout_ms,
                                    referer=url, cookies=cookies, _depth=_depth + 1)
            if sub and not sub.get("error") and (sub.get("video_url") or sub.get("formats")):
                # Asil sayfadan alinan basligi koru (embed sayfasinda genelde yok)
                if result.get("title") and not sub.get("title"):
                    sub["title"] = result["title"]
                if result.get("thumbnail") and not sub.get("thumbnail"):
                    sub["thumbnail"] = result["thumbnail"]
                return sub

    if not result.get("error") and not result.get("formats") and not result.get("video_url"):
        result["error"] = _build_not_found_message(diagnostics)

    return result


def _count_video_tags(page):
    try:
        return len(page.query_selector_all("video"))
    except Exception:
        return 0


def _scan_for_drm_keywords(page):
    try:
        html = page.content()
    except Exception:
        return False
    html_lower = html.lower()
    return any(kw in html_lower for kw in ("requestmediakeysystemaccess", "widevine", "playready", "fairplay"))


def _build_not_found_message(diagnostics):
    status = diagnostics.get("http_status")
    status_txt = f"HTTP {status}" if status else "durum bilinmiyor"
    parts = [
        f"Video bulunamadi. Teshis: sayfa {'yuklendi' if diagnostics.get('page_loaded') else 'yuklenemedi'} ({status_txt})",
        f"{diagnostics.get('iframes_scanned', 0)} iframe tarandi",
        f"{diagnostics.get('video_tags_found', 0)} video etiketi bulundu",
        f"{diagnostics.get('media_requests_seen', 0)} medya istegi gozlendi",
        f"{diagnostics.get('blocked_requests', 0)} reklam/izleyici istegi engellendi",
    ]
    msg = ", ".join(parts) + "."
    if diagnostics.get("drm_keywords_found"):
        msg += " Sayfada DRM/EME (Widevine/PlayReady/FairPlay) referansi tespit edildi - bu video sifrelenmis olabilir."
    return msg


def _dismiss_popups(page):
    for sel in POPUP_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=2000, force=True)
                time.sleep(0.3)
        except Exception:
            pass

    try:
        page.evaluate("""() => {
            const remove = (s) => document.querySelectorAll(s).forEach(e => e.remove());
            remove('[class*="cookie"], [id*="cookie"], [class*="consent"], [id*="consent"], [class*="gdpr"]');
            remove('[class*="modal"], [class*="popup"], [class*="overlay"]');
            remove('[class*="ad-"], [id*="ad-"], [class*="reklam"], [id*="reklam"]');
            document.querySelectorAll('div, iframe').forEach(e => {
                const s = e.style;
                if (s.position === 'fixed' && (s.zIndex > 100 || s.top === '0' || s.bottom === '0')) e.remove();
            });
            document.body.style.overflow = 'auto';
        }""")
    except Exception:
        pass


def _try_click_play(page):
    for sel in PLAY_SELECTORS:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(timeout=2000, force=True)
                time.sleep(0.5)
                break
        except Exception:
            pass

    try:
        page.evaluate("""() => {
            const elements = document.querySelectorAll('video, .vjs-big-play-button, .jw-icon-display, .play-button, [aria-label*="play" i], [aria-label*="oynat" i]');
            elements.forEach(el => {
                el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            });
        }""")
    except Exception:
        pass

    try:
        page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            videos.forEach(v => {
                try { v.muted = true; v.play(); } catch(e) {}
                try { v.currentTime = 0; } catch(e) {}
            });
            if (typeof hlsjs !== 'undefined') {
                try { hlsjs.trigger('play'); } catch(e) {}
            }
        }""")
    except Exception:
        pass


def _wait_for_video(page, timeout_ms):
    try:
        page.wait_for_selector("video[src], video source[src], video[currentsrc]", timeout=min(timeout_ms, 15000))
        time.sleep(1)
        return
    except PlaywrightTimeout:
        pass

    for _ in range(5):
        has_src = page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            for (const v of videos) {
                if (v.src || v.currentSrc || v.querySelector('source[src]')) return true;
            }
            return false;
        }""")
        if has_src:
            time.sleep(1)
            return
        time.sleep(1)

    try:
        _try_click_play(page)
        time.sleep(2)
    except Exception:
        pass


def _extract_title(page, result, url):
    for sel in TITLE_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.get_attribute("content") or el.inner_text()
                text = text.strip()
                if text and len(text) > 2:
                    result["title"] = _clean_title(text, url)
                    return
        except Exception:
            pass

    try:
        json_ld = page.evaluate("""() => {
            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
            for (const s of scripts) {
                try {
                    const data = JSON.parse(s.textContent);
                    if (data.name) return data.name;
                    if (data.itemListElement && data.itemListElement.length) {
                        return data.itemListElement[data.itemListElement.length-1].item?.name || '';
                    }
                } catch(e) {}
            }
            return '';
        }""")
        if json_ld and len(json_ld) > 2:
            result["title"] = _clean_title(json_ld, url)
            return
    except Exception:
        pass

    try:
        title = page.title()
        if title:
            result["title"] = _clean_title(title, url)
    except Exception:
        pass


def _extract_thumbnail(page, result):
    selectors = ['meta[property="og:image"]', 'meta[name="og:image"]', 'meta[name="twitter:image"]']
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                content = el.get_attribute("content")
                if content:
                    result["thumbnail"] = content
                    return
        except Exception:
            pass

    try:
        video = page.query_selector("video[poster]")
        if video:
            poster = video.get_attribute("poster")
            if poster:
                result["thumbnail"] = poster
    except Exception:
        pass


def _should_skip_url(raw_url):
    if not raw_url or raw_url.startswith(("data:", "blob:")):
        return True
    if _is_non_video_url(raw_url):
        return True
    return False


def _scan_main_page(page, result, captured_urls):
    try:
        for video in page.query_selector_all("video"):
            src = video.get_attribute("src")
            current_src = video.evaluate("el => el.currentSrc") or ""
            if src and not _should_skip_url(src):
                captured_urls.append({"url": src, "type": "video_src", "priority": True})
            if current_src and current_src != src and not _should_skip_url(current_src):
                captured_urls.append({"url": current_src, "type": "video_current_src", "priority": True})
            for source in video.query_selector_all("source"):
                ssrc = source.get_attribute("src")
                if ssrc and not _should_skip_url(ssrc):
                    captured_urls.append({"url": ssrc, "type": "video_source_tag", "priority": True})

        try:
            page.evaluate("""() => {
                document.querySelectorAll('iframe[data-src]').forEach(iframe => {
                    if (!iframe.src || iframe.src === 'about:blank' || iframe.src === '') {
                        iframe.src = iframe.getAttribute('data-src');
                    }
                });
                document.querySelectorAll('iframe[data-lazy-src]').forEach(iframe => {
                    if (!iframe.src || iframe.src === 'about:blank' || iframe.src === '') {
                        iframe.src = iframe.getAttribute('data-lazy-src');
                    }
                });
            }""")
            time.sleep(2)
        except Exception:
            pass

        for iframe in page.query_selector_all("iframe"):
            src = iframe.get_attribute("src") or ""
            data_src = iframe.get_attribute("data-src") or ""
            data_lazy = iframe.get_attribute("data-lazy-src") or ""
            for iframe_url in [src, data_src, data_lazy]:
                if iframe_url and iframe_url != "about:blank":
                    captured_urls.append({"url": iframe_url, "type": "iframe_src", "priority": True})

    except Exception:
        pass


def _scan_all_iframes(page, result, captured_urls, depth=0, max_depth=4, diagnostics=None):
    if depth >= max_depth:
        return

    try:
        iframes = page.query_selector_all("iframe")
    except Exception:
        return

    for iframe in iframes:
        if diagnostics is not None:
            diagnostics["iframes_scanned"] += 1
        try:
            frame = iframe.content_frame()
            if not frame:
                continue

            frame_url = frame.url
            if frame_url and frame_url != "about:blank":
                captured_urls.append({"url": frame_url, "type": f"iframe_url_d{depth}", "priority": True})

            for video in frame.query_selector_all("video"):
                src = video.get_attribute("src")
                current_src = video.evaluate("el => el.currentSrc") or ""
                if src and not _should_skip_url(src):
                    captured_urls.append({"url": src, "type": f"iframe_video_src_d{depth}", "priority": True})
                if current_src and current_src != src and not _should_skip_url(current_src):
                    captured_urls.append({"url": current_src, "type": f"iframe_video_currentSrc_d{depth}", "priority": True})
                for source in video.query_selector_all("source"):
                    ssrc = source.get_attribute("src")
                    if ssrc and not _should_skip_url(ssrc):
                        captured_urls.append({"url": ssrc, "type": f"iframe_video_source_d{depth}", "priority": True})

            try:
                video_url = frame.evaluate("""() => {
                    try {
                        if (window.jwplayer) {
                            let p;
                            try { p = jwplayer(); } catch(e) {}
                            if (p && p.getPlaylistItem) {
                                const item = p.getPlaylistItem();
                                if (item) {
                                    if (item.file) return item.file;
                                    if (item.sources && item.sources.length) return item.sources[0].file;
                                }
                            }
                            const els = document.querySelectorAll('[id^="player"], [id$="player"]');
                            for (const el of els) {
                                try {
                                    const jw = jwplayer(el.id);
                                    if (jw && jw.getPlaylistItem) {
                                        const item = jw.getPlaylistItem();
                                        if (item && item.file) return item.file;
                                    }
                                } catch(e) {}
                            }
                        }
                    } catch(e) {}

                    try {
                        if (window.videojs) {
                            const players = document.querySelectorAll('.video-js');
                            for (const p of players) {
                                if (!p.id) continue;
                                try {
                                    const vjs = videojs(p.id);
                                    if (vjs && vjs.src()) return vjs.src();
                                } catch(e) {}
                            }
                        }
                    } catch(e) {}

                    try {
                        if (window.Hls) {
                            const v = document.querySelector('video');
                            if (v && v.src && !v.src.startsWith('blob:')) return v.src;
                            if (v && v.currentSrc && !v.currentSrc.startsWith('blob:')) return v.currentSrc;
                        }
                    } catch(e) {}

                    try {
                        if (window.dashjs) {
                            const player = dashjs.MediaPlayer().getActiveStream();
                            if (player && player.getStreamInfo()) {
                                const info = player.getStreamInfo();
                                if (info && info.manifestInfo && info.manifestInfo.url) {
                                    return info.manifestInfo.url;
                                }
                            }
                        }
                    } catch(e) {}

                    try {
                        const v = document.querySelector('video');
                        if (v) {
                            if (v.currentSrc && !v.currentSrc.startsWith('blob:')) return v.currentSrc;
                            if (v.src && !v.src.startsWith('blob:')) return v.src;
                            const s = v.querySelector('source[src]');
                            if (s && s.src && !s.src.startsWith('blob:')) return s.src;
                        }
                    } catch(e) {}
                    return '';
                }""")
                if video_url and not _should_skip_url(video_url):
                    captured_urls.append({"url": video_url, "type": f"iframe_player_api_d{depth}", "priority": True})
            except Exception:
                pass

            _scan_all_iframes(frame, result, captured_urls, depth + 1, max_depth, diagnostics=diagnostics)
        except Exception:
            pass


def _scan_network_captures(captured_urls, result):
    priority_urls = [c for c in captured_urls if c.get("priority")]
    normal_urls = [c for c in captured_urls if not c.get("priority")]

    if priority_urls:
        all_urls = priority_urls + normal_urls
    else:
        all_urls = normal_urls

    video_urls = []
    seen = set()

    for c in all_urls:
        raw_url = c["url"]
        if _should_skip_url(raw_url) or raw_url in seen:
            continue
        clean = raw_url.split("?")[0].lower()
        ext = "." + clean.rsplit(".", 1)[-1] if "." in clean else ""
        is_m3u8 = ".m3u8" in clean or "m3u8" in raw_url.lower()
        is_dash = ".mpd" in clean
        is_video_ext = ext in VIDEO_EXTS
        is_player_api = c.get("type", "").startswith("iframe_player_api")
        is_mime_captured = c.get("type", "") == "mime_match"
        is_url_pattern = c.get("type", "") == "url_pattern_match"
        is_body_scan = c.get("type", "") == "response_body_scan"

        if is_player_api:
            video_urls.append({"id": "best", "label": "Player Source", "detail": raw_url, "priority": 5})
        elif is_mime_captured:
            ct_info = c.get("content_type", "")
            label = f"Stream ({ct_info.split('/')[-1]})" if ct_info else "Stream"
            video_urls.append({"id": "best", "label": label, "detail": raw_url, "priority": 4})
        elif is_m3u8:
            video_urls.append({"id": "best", "label": "HLS (m3u8)", "detail": raw_url, "priority": 3})
        elif is_dash:
            video_urls.append({"id": "best", "label": "DASH (mpd)", "detail": raw_url, "priority": 2})
        elif is_video_ext:
            label = ext.upper().replace(".", "")
            video_urls.append({"id": "best", "label": f"{label} Video", "detail": raw_url, "priority": 1})
        elif is_url_pattern:
            video_urls.append({"id": "best", "label": "Stream", "detail": raw_url, "priority": 1})
        elif is_body_scan:
            video_urls.append({"id": "best", "label": "Media URL", "detail": raw_url, "priority": 1})
        elif any(kw in raw_url.lower() for kw in ["m3u8", "manifest", ".ts?", "playlist", "master.json"]):
            video_urls.append({"id": "best", "label": "Stream", "detail": raw_url, "priority": 1})

        seen.add(raw_url)

    # FALLBACK: hicbir URL eslesmediyse ama yakalanmis URL varsa hepsini goster
    if not video_urls and all_urls:
        fallback_added = 0
        for c in all_urls:
            raw_url = c["url"]
            if _should_skip_url(raw_url):
                continue
            clean = raw_url.split("?")[0].lower()
            ext = "." + clean.rsplit(".", 1)[-1] if "." in clean else ""
            if ext in VIDEO_EXTS or not ext or ext in (".bin", ".dat", ".raw", ""):
                ctype_label = c.get("content_type", "unknown")
                if ctype_label and ctype_label != "unknown":
                    label = f"Media ({ctype_label})"
                else:
                    label = "Media URL"
                video_urls.append({"id": "best", "label": label, "detail": raw_url, "priority": 1})
                fallback_added += 1
                if fallback_added >= 10:
                    break

    video_urls.sort(key=lambda x: x.get("priority", 0), reverse=True)

    if video_urls:
        result["formats"] = [{"id": v["id"], "label": v["label"], "detail": v["detail"]} for v in video_urls]
        result["video_url"] = video_urls[0]["detail"]


def _auto_scroll(page):
    """Scroll the page to trigger lazy-loaded content."""
    try:
        page.evaluate("""() => {
            const scroll = (y) => { window.scrollTo(0, y); };
            scroll(400);
            setTimeout(() => scroll(800), 500);
            setTimeout(() => scroll(1200), 1000);
            setTimeout(() => scroll(0), 1500);
        }""")
        time.sleep(2)
    except Exception:
        pass


def _expand_hls_formats(result, referer="", cookies=""):
    """video_url bir HLS MASTER playlist ise (uzantisiz olsa bile) cozunurluk
    seceneklerini formatlara donustur. Her formatin detail'i MASTER URL olur;
    indirme aninda master taze fetch edilip istenen cozunurluk secilir."""
    video_url = result.get("video_url", "")
    if not video_url:
        return
    low = video_url.lower().split("?")[0]
    if low.endswith((".mp4", ".mkv", ".webm", ".ts", ".mpd", ".m4v", ".mov")):
        return
    try:
        import requests
        from backend.native_downloader import _build_headers, parse_hls_variants
        headers = _build_headers(referer=referer, cookies=cookies)
        r = requests.get(video_url, headers=headers, timeout=12)
        if r.status_code != 200 or "#EXT-X-STREAM-INF" not in r.text:
            return
        variants, _ = parse_hls_variants(video_url, r.text)
        formats, seen = [], set()
        for v in variants:
            h = v.get("height") or 0
            if h in seen:
                continue
            seen.add(h)
            formats.append({
                "id": str(h) if h else "best",
                "label": f"{h}p" if h else "Stream",
                "detail": video_url, "height": h,
            })
        if formats:
            # En ustune "4K (buyut)" secenegi ekle: kaynak 1080p'yi indirip GPU
            # ile 4K'ya buyutur (format_id=2160). detail yine master URL.
            top_h = max((f.get("height") or 0) for f in formats)
            if top_h and top_h < 2160:
                formats.insert(0, {"id": "2160", "label": "4K (büyüt) ⚡",
                                   "detail": video_url, "height": 2160})
            result["formats"] = formats  # video_url master olarak kalir
    except Exception:
        pass


def _try_parse_manifest(result):
    """If video_url is a manifest file, parse it for quality options."""
    video_url = result.get("video_url", "")
    if not video_url:
        return

    is_m3u8 = ".m3u8" in video_url.lower()
    is_mpd = ".mpd" in video_url.lower()

    if not is_m3u8 and not is_mpd:
        return

    try:
        from backend.manifest_parser import parse_m3u8, parse_mpd

        if is_m3u8:
            formats = parse_m3u8(video_url)
        else:
            formats = parse_mpd(video_url)

        if formats and len(formats) > 1:
            result["formats"] = [
                {"id": f["id"], "label": f["label"], "detail": f["url"]}
                for f in formats
            ]
            result["video_url"] = formats[0]["url"]
    except Exception:
        pass


def _clean_title(title, url):
    title = re.sub(
        r"\s*[-–|]\s*(fullhdfilmizlesene|tranimeizle|anizm|dizigom|dizibox|dizipal|dizilla"
        r"|filmmodu|filmmakinesi|hdfilmcehennemi|filmizlesene|netflix|youtube"
        r"|izle|film|dizi|anime|watch|stream|download|hd|full|online|free).*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = title.strip()

    if not title or len(title) < 3:
        path = url.rstrip("/").rsplit("/", 2)
        if len(path) >= 2:
            slug = path[-1] if path[-1] else path[-2]
            slug = slug.split("?")[0]
            slug = re.sub(r"[-_]", " ", slug)
            slug = re.sub(r"\s+", " ", slug).strip()
            if len(slug) > 3:
                title = slug

    if not title or len(title) < 3:
        title = url

    return title
