import os
import re
import time
import hashlib
import threading
from urllib.parse import urlparse, parse_qs


def parse_magnet_uri(uri):
    """Parse magnet:?xt=urn:btih:... URI. Returns dict with info_hash, dn, tr."""
    if not uri or not uri.startswith("magnet:?"):
        return None

    result = {"info_hash": "", "dn": "", "tr": []}
    qs = uri[8:]

    for part in qs.split("&"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.lower()
        if k == "xt":
            if v.startswith("urn:btih:"):
                result["info_hash"] = v[9:].lower()
            elif v.startswith("urn:sha1:"):
                result["info_hash"] = v[9:].lower()
        elif k == "dn":
            result["dn"] = v.replace("+", " ").strip()
        elif k == "tr":
            result["tr"].append(v)

    return result if result["info_hash"] else None


def download_magnet(magnet_uri, output_dir, progress_callback=None, cancel_event=None):
    """Download via magnet link using libtorrent (if available) or yt-dlp fallback."""
    import subprocess
    import sys

    info = parse_magnet_uri(magnet_uri)
    if not info:
        return False, "Gecersiz magnet URI"

    # Try libtorrent first
    try:
        import libtorrent as lt
        return _download_magnet_lt(magnet_uri, output_dir, info, progress_callback, cancel_event)
    except ImportError:
        pass

    # Fallback: yt-dlp (supports magnet links)
    try:
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "-o", os.path.join(output_dir, "%(title)s.%(ext)s"),
            magnet_uri,
        ]

        si = None
        import platform
        if platform.system() == "Windows":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", startupinfo=si)

        output = []
        for line in proc.stdout:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                return False, "Iptal edildi"
            line = line.strip()
            if line:
                output.append(line)
                if progress_callback and "%" in line:
                    try:
                        pct = float(line.split("%")[0].split()[-1])
                        progress_callback(min(99, pct))
                    except (ValueError, IndexError):
                        pass

        proc.wait()
        if proc.returncode == 0:
            if progress_callback:
                progress_callback(100, "", "")
            return True, ""
        return False, "\n".join(output[-5:]) if output else f"Hata kodu: {proc.returncode}"
    except Exception as e:
        return False, str(e)


def _download_magnet_lt(magnet_uri, output_dir, info, progress_callback=None, cancel_event=None):
    """Download using libtorrent Python binding."""
    import libtorrent as lt

    ses = lt.session()
    ses.listen_on(6881, 6891)

    settings = ses.get_settings()
    settings["enable_dht"] = True
    settings["enable_lsd"] = True
    settings["enable_upnp"] = True
    settings["enable_natpmp"] = True
    ses.set_settings(settings)

    params = lt.parse_magnet_uri(magnet_uri)
    params.save_path = output_dir
    handle = ses.add_torrent(params)

    info_hash = info["info_hash"]
    print(f"[TORRENT] Basladi: {info_hash[:16]}... ({info.get('dn', 'bilinmeyen')})", flush=True)

    timeout = time.time() + 300
    downloading = False

    while not handle.is_seed():
        if cancel_event and cancel_event.is_set():
            ses.remove_torrent(handle)
            return False, "Iptal edildi"

        if time.time() > timeout and not downloading:
            ses.remove_torrent(handle)
            return False, "Zaman asimi: peer bulunamadi"

        s = handle.status()
        if s.state == lt.torrent_status.downloading:
            downloading = True
            timeout = time.time() + 7200

        if progress_callback:
            pct = s.progress * 100
            speed = s.download_rate
            from backend.native_downloader import _format_speed as fs
            progress_callback(min(99, pct), fs(speed), "")

        time.sleep(1)

    if progress_callback:
        progress_callback(100, "", "")

    ses.remove_torrent(handle)
    return True, ""


def is_magnet_uri(url):
    return (url or "").startswith("magnet:?")


def is_torrent_file(url):
    return (url or "").lower().endswith(".torrent")
