import os
import sys
import json
import asyncio
import logging
import mimetypes
import time
from pathlib import Path

if sys.platform == "win32":
    # Windows subprocess stdout defaults to the system codepage (e.g. cp1252),
    # which crashes on Turkish/unicode video titles from yt-dlp with
    # UnicodeEncodeError. Force UTF-8 so print() never raises on odd titles.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState


class _WebSocketSendFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "socket.send() raised exception" not in msg

for _name in ("websockets", "uvicorn", "uvicorn.error", "uvicorn.logging"):
    _logger = logging.getLogger(_name)
    if _logger:
        _logger.addFilter(_WebSocketSendFilter())

_ws_last_send: dict = {}
_WS_THROTTLE_MS = 200


def _safe_remove_client(client):
    try:
        connected_clients.remove(client)
    except ValueError:
        pass


def _future_suppress(future):
    try:
        future.result()
    except Exception:
        pass


def _is_ws_alive(client):
    try:
        return client.client_state == WebSocketState.CONNECTED
    except Exception:
        return False


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import (
    get_all_settings, set_setting, get_history, get_stats,
    get_category_videos, get_all_videos, get_folders, add_folder,
    remove_folder, update_folder_scan, get_setting, get_db, clear_history,
)
from backend.downloader import manager, analyze_url_sync
from backend.scanner import scan_folder, scan_all

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "backend", "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "backend", "templates")

app = FastAPI(title="AniFrame Video Downloader", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

connected_clients = []


@app.on_event("startup")
async def startup():
    manager.set_event_loop(asyncio.get_running_loop())
    # Onceki oturumdan yarim kalan indirmeleri 'failed' yap (yoksa 'Indiriliyor'
    # durumunda sonsuza dek takili kaliyorlar).
    try:
        from backend.database import reset_stale_downloads
        n = reset_stale_downloads()
        if n:
            print(f"[STARTUP] {n} yarim kalan indirme 'failed' olarak isaretlendi", flush=True)
    except Exception:
        pass
    if get_setting("scan_on_startup", "true") == "true":
        try:
            scan_all()
        except Exception:
            pass
    asyncio.get_running_loop().call_soon(_periodic_ws_cleanup)


def _periodic_ws_cleanup():
    removed = 0
    for client in list(connected_clients):
        if not _is_ws_alive(client):
            _safe_remove_client(client)
            removed += 1
    for cid in list(_ws_last_send.keys()):
        if not any(id(c) == cid for c in connected_clients):
            _ws_last_send.pop(cid, None)
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.call_later(10, _periodic_ws_cleanup)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.isfile(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


@app.get("/api/videos")
async def api_videos(category: str = Query(default="all"), source: str = Query(default="all")):
    if category and category != "all":
        videos = get_category_videos(category)
    else:
        videos = get_all_videos()

    if source and source != "all":
        videos = [v for v in videos if v.get("source") == source]

    return {"videos": videos}


@app.get("/api/play-local/{video_id}")
async def api_play_local(video_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT file_path FROM external_files WHERE id=?", (video_id,)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT file_path FROM download_history WHERE id=? AND status='completed'",
                (video_id,),
            ).fetchone()
    finally:
        conn.close()

    if not row or not row["file_path"]:
        return JSONResponse({"error": "File not found"}, status_code=404)

    file_path = row["file_path"]
    if not os.path.isfile(file_path):
        return JSONResponse({"error": "File does not exist on disk"}, status_code=404)

    try:
        os.startfile(file_path)
        return {"success": True, "message": "Opened in default player"}
    except Exception as e:
        return JSONResponse({"error": f"Cannot open file: {str(e)}"}, status_code=500)


@app.api_route("/api/video/{video_id}", methods=["GET", "HEAD"])
async def api_stream_video(video_id: int, request: Request):
    conn = get_db()
    try:
        row = conn.execute("SELECT file_path FROM external_files WHERE id=?", (video_id,)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT file_path FROM download_history WHERE id=? AND status='completed'",
                (video_id,),
            ).fetchone()
    finally:
        conn.close()

    if not row or not row["file_path"]:
        return JSONResponse({"error": "File not found"}, status_code=404)

    file_path = row["file_path"]
    if not os.path.isfile(file_path):
        return JSONResponse({"error": "File does not exist on disk"}, status_code=404)

    file_size = os.path.getsize(file_path)
    content_type, _ = mimetypes.guess_type(file_path)
    if not content_type:
        content_type = "video/mp4"

    range_header = request.headers.get("range")
    if not range_header or file_size <= 0:
        return FileResponse(file_path, media_type=content_type)

    try:
        range_str = range_header.replace("bytes=", "")
        if "," in range_str:
            range_str = range_str.split(",")[0]
        if "-" in range_str:
            parts = range_str.split("-", 1)
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
        else:
            start = int(range_str)
            end = file_size - 1
    except (ValueError, IndexError):
        return JSONResponse({"error": "Invalid range header"}, status_code=416)

    if start < 0:
        start = 0
    if end >= file_size:
        end = file_size - 1
    if start > end:
        return JSONResponse({"error": "Invalid range"}, status_code=416)

    chunk_size = end - start + 1

    async def ranged_stream():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = chunk_size
            while remaining > 0:
                buf = f.read(min(65536, remaining))
                if not buf:
                    break
                remaining -= len(buf)
                yield buf

    return StreamingResponse(
        ranged_stream(),
        status_code=206,
        media_type=content_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
        },
    )


@app.get("/api/thumbnail/{video_id}")
async def api_thumbnail(video_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT thumb_path FROM external_files WHERE id=?", (video_id,)).fetchone()
        if not row or not row["thumb_path"]:
            row = conn.execute("SELECT thumb_path FROM download_history WHERE id=?", (video_id,)).fetchone()
    finally:
        conn.close()
    if row and row["thumb_path"] and os.path.isfile(row["thumb_path"]):
        ext = os.path.splitext(row["thumb_path"])[1].lower()
        media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
        media_type = media_map.get(ext, "image/jpeg")
        return FileResponse(row["thumb_path"], media_type=media_type)
    return JSONResponse({"error": "No thumbnail"}, status_code=404)


@app.post("/api/analyze")
async def api_analyze(data: dict):
    url = data.get("url", "").strip()
    if not url:
        return {"error": "URL is required"}
    result = analyze_url_sync(url)
    return result


@app.post("/api/download")
async def api_download(data: dict):
    url = data.get("url", "").strip()
    category = data.get("category", "uncategorized")
    format_id = data.get("format_id", "best")
    output_path = data.get("output_path") or None
    is_browser_url = data.get("is_browser_url", False)
    custom_video_url = data.get("custom_video_url", "")
    custom_title = data.get("custom_title", "")
    page_url = data.get("page_url", "")
    cookies = data.get("cookies", "")
    cookies_netscape = data.get("cookies_netscape", "")

    if not url:
        return {"error": "URL is required"}

    hid = manager.enqueue(
        url, category=category, format_id=format_id, output_path=output_path,
        is_browser_url=is_browser_url, custom_video_url=custom_video_url, custom_title=custom_title,
        page_url=page_url, cookies=cookies, cookies_netscape=cookies_netscape,
    )
    return {"id": hid, "status": "queued"}


@app.post("/api/download/batch")
async def api_download_batch(data: dict):
    items = data.get("items", [])
    if not items:
        return {"error": "No items provided"}
    ids = manager.enqueue_batch(items)
    return {"ids": ids, "count": len(ids)}


@app.get("/api/queue")
async def api_queue():
    return manager.get_queue_state()


@app.delete("/api/queue/{task_id}")
async def api_cancel(task_id: int):
    ok = manager.cancel(task_id)
    return {"success": ok}


@app.get("/api/stats")
async def api_stats():
    return get_stats()


@app.get("/api/history")
async def api_history(category: str = Query(default=None)):
    return {"history": get_history(category=category)}


@app.delete("/api/history")
async def api_clear_history():
    clear_history()
    return {"ok": True}


@app.get("/api/folders")
async def api_folders():
    return {"folders": get_folders()}


@app.post("/api/folders")
async def api_add_folder(data: dict):
    path = data.get("path", "").strip()
    category = data.get("category", "uncategorized")
    label = data.get("label", "")

    if not path or not os.path.isdir(path):
        return {"error": "Geçerli bir klasör yolu belirtin"}

    fid = add_folder(path, category, label)
    if fid:
        import threading
        t = threading.Thread(target=lambda: scan_folder(fid, path, category), daemon=True)
        t.start()
        return {"id": fid, "status": "scanning"}
    return {"error": "Klasör zaten eklenmiş"}


@app.delete("/api/folders/{folder_id}")
async def api_remove_folder(folder_id: int):
    remove_folder(folder_id)
    return {"success": True}


@app.post("/api/folders/scan")
async def api_scan_all():
    import threading
    result = {"found": 0}

    def _scan():
        result["found"] = scan_all()

    t = threading.Thread(target=_scan, daemon=True)
    t.start()
    t.join(timeout=300)
    return {"found": result["found"]}


@app.get("/api/settings")
async def api_get_settings():
    return {"settings": get_all_settings()}


@app.post("/api/settings")
async def api_save_settings(data: dict):
    for k, v in data.items():
        set_setting(k, v)
    return {"success": True}


@app.post("/api/extension/toggle")
async def api_extension_toggle(data: dict):
    enabled = data.get("enabled", False)
    set_setting("extension_enabled", "true" if enabled else "false")
    return {"success": True, "enabled": enabled}


@app.get("/api/extension/status")
async def api_extension_status():
    enabled = get_setting("extension_enabled", "false")
    return {
        "enabled": enabled == "true",
        "extension_dir": os.path.join(BASE_DIR, "extension"),
        "backend": "running",
    }


# ---------------------------------------------------------------------------
# Tarayici ve Extension Yonetimi
# ---------------------------------------------------------------------------

@app.get("/api/browsers")
async def api_browsers():
    try:
        from backend.browser_installer import get_browser_extension_status
        browsers = get_browser_extension_status()
        return {"browsers": browsers}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/extension/install")
async def api_extension_install(data: dict):
    browser_key = data.get("browser", "")
    launch_browser = data.get("launch_browser", True)
    if not browser_key:
        return {"error": "Tarayici secilmedi"}
    try:
        from backend.browser_installer import install_extension
        result = install_extension(browser_key, launch_browser=launch_browser)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/extension/uninstall")
async def api_extension_uninstall(data: dict):
    browser_key = data.get("browser", "")
    if not browser_key:
        return {"error": "Tarayici secilmedi"}
    try:
        from backend.browser_installer import (
            find_all_profiles, _get_user_data_dir, EXTENSION_ID, EXTENSION_DIR
        )
        import json as _json

        profiles = find_all_profiles(browser_key)
        removed = []

        for profile in profiles:
            profile_dir = profile["path"]
            for prefs_name in ("Secure Preferences", "Preferences"):
                prefs_path = os.path.join(profile_dir, prefs_name)
                if not os.path.isfile(prefs_path):
                    continue
                try:
                    with open(prefs_path, "r", encoding="utf-8") as f:
                        data_obj = _json.load(f)
                    settings = data_obj.get("extensions", {}).get("settings", {})
                    if EXTENSION_ID in settings:
                        del settings[EXTENSION_ID]
                        protection = data_obj.get("protection", {}).get("macs", {})
                        ext_macs = protection.get("extensions", {}).get("settings", {})
                        if EXTENSION_ID in ext_macs:
                            del ext_macs[EXTENSION_ID]
                        with open(prefs_path, "w", encoding="utf-8") as f:
                            _json.dump(data_obj, f, separators=(',', ':'))
                        removed.append(profile["display_name"])
                        break
                except Exception:
                    continue

        return {"success": len(removed) > 0, "removed_profiles": removed}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Extension'dan Video Bildirimi (IDM modu)
# ---------------------------------------------------------------------------

@app.post("/api/extension/detect")
async def api_extension_detect(data: dict):
    page_url = data.get("page_url", "")
    video_url = data.get("video_url", "")
    title = data.get("title", "")
    drm_detected = data.get("drm_detected", False)
    video_type = data.get("video_type", "")
    hostname = data.get("hostname", "")

    if drm_detected:
        return {"error": "DRM korumalı icerik - indirilemez", "drm": True}

    if not video_url and not page_url:
        return {"error": "URL gerekli"}

    # DRM domain kontrolu
    drm_domains = {
        "netflix.com", "disneyplus.com", "hulu.com", "amazon.com", "primevideo.com",
        "hbomax.com", "max.com", "peacocktv.com", "paramountplus.com",
        "tv.apple.com", "hotstar.com", "spotify.com",
    }
    host_lower = hostname.lower()
    for d in drm_domains:
        if d in host_lower:
            return {"error": f"DRM korumali site: {hostname}. Bu siteden video indirilemez.", "drm": True}

    category = _guess_category_from_hostname(hostname)

    # Integration session'a kaydet
    try:
        from backend.integration import integration_session
        integration_session.add_detected_video(data)
        integration_session.mark_connected()
    except Exception:
        pass

    return {"recorded": True, "category": category}


def _guess_category_from_hostname(hostname):
    hostname = hostname.lower()
    if any(kw in hostname for kw in ["dizi", "film", "izle", "sinema", "movie"]):
        if "anime" in hostname:
            return "anime"
        return "film"
    if "anime" in hostname:
        return "anime"
    if "youtube" in hostname or "video" in hostname:
        return "video"
    return "uncategorized"


# ═══════════════════════════════════════════════════════════
# INTEGRATION API (IDM modu - HTTP connector)
# ═══════════════════════════════════════════════════════════

@app.get("/api/integration/ping")
async def api_integration_ping():
    """Health check for browser extension connector."""
    return {"status": "ok", "version": "2.5", "timestamp": __import__("time").time()}


@app.get("/api/integration/sync")
async def api_integration_sync():
    """Sync endpoint - extension periodically polls this."""
    try:
        from backend.integration import integration_session
        integration_session.mark_connected()
        return integration_session.get_state()
    except Exception:
        return {"connected": False, "error": "integration module not loaded"}


@app.post("/api/integration/video")
async def api_integration_video(data: dict):
    """Receive video detection from integration connector."""
    try:
        from backend.integration import integration_session
        entry = integration_session.add_detected_video(data)
        return {"recorded": True, "id": entry["id"]}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/integration/videos")
async def api_integration_videos(tab_id: str = ""):
    """Get detected videos for a tab."""
    try:
        from backend.integration import integration_session
        if tab_id:
            return {"videos": integration_session.get_videos_for_tab(tab_id)}
        return {"videos": integration_session.detected_videos[-50:]}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# HOST SETTINGS API
# ═══════════════════════════════════════════════════════════

@app.get("/api/host-settings")
async def api_host_settings():
    try:
        from backend.host_settings import get_all_host_settings
        return {"hosts": get_all_host_settings()}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/host-settings/{hostname}")
async def api_get_host_setting(hostname: str):
    try:
        from backend.host_settings import get_host_settings
        return get_host_settings(hostname)
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/host-settings/{hostname}")
async def api_set_host_setting(hostname: str, data: dict):
    try:
        from backend.host_settings import set_host_setting, delete_host_settings
        for key, value in data.items():
            if value is None:
                delete_host_settings(hostname)
            else:
                set_host_setting(hostname, key, value)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# PROXY API
# ═══════════════════════════════════════════════════════════

@app.get("/api/proxy")
async def api_get_proxies():
    try:
        from backend.proxy_manager import get_all_proxies, get_system_proxy
        return {"proxies": get_all_proxies(), "system_proxy": get_system_proxy()}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/proxy")
async def api_set_proxy(data: dict):
    try:
        from backend.proxy_manager import set_proxy
        pattern = data.get("pattern", "*")
        proxy_url = data.get("proxy_url", "")
        set_proxy(pattern, proxy_url)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.delete("/api/proxy/{pattern}")
async def api_remove_proxy(pattern: str):
    try:
        from backend.proxy_manager import remove_proxy
        remove_proxy(pattern)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# DOWNLOAD PRIORITY & QUEUE ENHANCEMENTS
# ═══════════════════════════════════════════════════════════

@app.post("/api/download/priority")
async def api_download_priority(data: dict):
    url = data.get("url", "").strip()
    category = data.get("category", "uncategorized")
    format_id = data.get("format_id", "best")
    priority = data.get("priority", "normal")
    is_browser_url = data.get("is_browser_url", False)
    custom_video_url = data.get("custom_video_url", "")
    custom_title = data.get("custom_title", "")
    page_url = data.get("page_url", "")
    cookies = data.get("cookies", "")
    cookies_netscape = data.get("cookies_netscape", "")

    if not url:
        return {"error": "URL is required"}

    hid = manager.enqueue(
        url, category=category, format_id=format_id,
        is_browser_url=is_browser_url, custom_video_url=custom_video_url,
        custom_title=custom_title, page_url=page_url, cookies=cookies,
        cookies_netscape=cookies_netscape, priority=priority,
    )
    return {"id": hid, "status": "queued", "priority": priority}


# ═══════════════════════════════════════════════════════════
# SPEED LIMITER API
# ═══════════════════════════════════════════════════════════

@app.get("/api/speed-limit")
async def api_get_speed_limit():
    try:
        from backend.speed_limiter import get_global_limiter
        limiter = get_global_limiter()
        return {"enabled": limiter.is_enabled(), "rate_bps": limiter._bucket.rate}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/speed-limit")
async def api_set_speed_limit(data: dict):
    try:
        from backend.speed_limiter import get_global_limiter
        kbps = int(data.get("kbps", 0))
        limiter = get_global_limiter()
        limiter.set_global_limit(kbps * 1024)
        set_setting("speed_limit_kbps", str(kbps))
        return {"success": True, "kbps": kbps}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# UNIVERSAL EXTRACTOR API (yt-dlp -> Agent-Reach -> Scrapling -> browser)
# ═══════════════════════════════════════════════════════════

@app.post("/api/extract/universal")
async def api_extract_universal(data: dict):
    """Universal extractor using priority chain (yt-dlp -> Agent-Reach -> Scrapling -> browser)."""
    url = data.get("url", "").strip()
    prefer = data.get("prefer", None)

    if not url:
        return {"error": "URL is required"}

    try:
        from backend.universal_fetcher import fetch_video
        result = fetch_video(url, prefer=prefer)
        return result
    except Exception as e:
        return {"error": str(e), "url": url}


@app.post("/api/extract/stealth")
async def api_extract_stealth(data: dict):
    """Scrapling Stealth extractor (Cloudflare bypass, anti-bot)."""
    url = data.get("url", "").strip()

    if not url:
        return {"error": "URL is required"}

    try:
        from backend.extractors.scrapling_stealth import ScraplingStealthExtractor
        extractor = ScraplingStealthExtractor()
        result = extractor.extract(url)
        return result
    except Exception as e:
        return {"error": str(e), "url": url}


@app.post("/api/agent-reach/status")
async def api_agent_reach_status(data: dict):
    """Agent-Reach kanal durumu (bilibili, twitter, web, youtube, ...).

    Agent-Reach video indirmez; platform erisim araclarinin (bili-cli,
    twitter-cli, OpenCLI, Jina Reader) kurulu/yapilandirilmis olup olmadigini
    raporlar. Indirme islemi AniFrame manager (yt-dlp/native) uzerinden yurur.
    """
    url = (data.get("url", "") or "").strip()
    channel_name = (data.get("channel", "") or "").strip()

    try:
        from agent_reach.channels import get_channel, get_all_channels
    except ImportError:
        return {"error": "agent-reach kurulu degil"}

    try:
        if channel_name:
            channel = get_channel(channel_name)
            if channel is None:
                return {"error": f"Bilinmeyen kanal: {channel_name}"}
            channels = [channel]
        elif url:
            channels = [ch for ch in get_all_channels() if ch.can_handle(url)]
            if not channels:
                return {"error": "URL icin kanal bulunamadi", "url": url}
        else:
            channels = get_all_channels()

        result = []
        for ch in channels:
            try:
                status, message = ch.check()
            except Exception as e:
                status, message = "error", str(e)[:300]
            result.append({
                "name": ch.name,
                "description": ch.description,
                "tier": ch.tier,
                "backends": ch.backends,
                "active_backend": ch.active_backend,
                "status": status,
                "message": (message or "")[:500],
            })
        return {"channels": result, "count": len(result), "url": url or None}
    except Exception as e:
        return {"error": str(e), "url": url}


@app.get("/api/extract/list")
async def api_extract_list():
    """List all available extractors."""
    try:
        from backend.extractors import ExtractorRegistry
        from backend.universal_fetcher import get_universal_fetcher
        extractors = ExtractorRegistry.get_all()
        result = []
        for ext in extractors:
            result.append({
                "name": ext.name,
                "domains": ext.domains,
                "patterns": ext.patterns,
                "class": ext.__class__.__name__,
                "module": ext.__class__.__module__,
            })
        fetcher = get_universal_fetcher()
        chain_labels = [label for label, _ext in fetcher.priority_chain]
        return {
            "extractors": result,
            "count": len(result),
            "priority_chain": chain_labels,
        }
    except Exception as e:
        return {"error": str(e)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                try:
                    await ws.send_json({"type": "pong"})
                except Exception:
                    break
    except WebSocketDisconnect:
        _safe_remove_client(ws)
    except Exception:
        _safe_remove_client(ws)
    finally:
        _safe_remove_client(ws)
        _ws_last_send.pop(id(ws), None)


async def broadcast_queue_update(data: dict):
    now_ms = time.time() * 1000
    for client in list(connected_clients):
        if not _is_ws_alive(client):
            _safe_remove_client(client)
            continue
        cid = id(client)
        last = _ws_last_send.get(cid, 0)
        if now_ms - last < _WS_THROTTLE_MS:
            continue
        _ws_last_send[cid] = now_ms
        try:
            await client.send_json(data)
        except Exception:
            _safe_remove_client(client)
            _ws_last_send.pop(cid, None)


manager.ws_callback_func = None

import threading

def _send_ws_patched(task):
    data = {
        "id": task.id,
        "status": task.status,
        "progress": task.progress,
        "speed": task.speed,
        "eta": task.eta,
        "error": task.error,
    }
    loop = manager._event_loop
    if not loop or loop.is_closed():
        return
    now_ms = time.time() * 1000
    for client in list(connected_clients):
        if not _is_ws_alive(client):
            _safe_remove_client(client)
            continue
        cid = id(client)
        last = _ws_last_send.get(cid, 0)
        if now_ms - last < _WS_THROTTLE_MS:
            continue
        _ws_last_send[cid] = now_ms
        try:
            future = asyncio.run_coroutine_threadsafe(client.send_json(data), loop)
            future.add_done_callback(_future_suppress)
        except Exception:
            _safe_remove_client(client)
            _ws_last_send.pop(cid, None)


manager._send_ws = _send_ws_patched
