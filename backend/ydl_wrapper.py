import os
import sys
import json
import shutil
import subprocess
import threading
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NON_VIDEO_FORMAT_EXTS = {"svg", "png", "jpg", "jpeg", "gif", "webp", "ico", "bmp", "avif"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".m3u8", ".ts", ".flv", ".avi", ".mov", ".wmv", ".m4v", ".mpd", ".ogv", ".3gp"}


def find_ytdlp_binary():
    """Find yt-dlp binary. Returns (path, type) where type is 'module', 'binary', or None."""
    # 1. Try module (installed via pip)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return sys.executable, "module"
    except Exception:
        pass

    # 2. Try standalone binary
    for name in ["yt-dlp.exe", "yt-dlp"]:
        path = shutil.which(name)
        if path:
            try:
                result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    return path, "binary"
            except Exception:
                pass

    return None, None


def get_ytdlp_version():
    """Get yt-dlp version string."""
    binary, btype = find_ytdlp_binary()
    if not binary:
        return None
    try:
        if btype == "module":
            result = subprocess.run(
                [binary, "-m", "yt_dlp", "--version"],
                capture_output=True, text=True, timeout=5
            )
        else:
            result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _ytdlp_js_args():
    """Return JS runtime args for yt-dlp."""
    if shutil.which("deno"):
        return []
    node = shutil.which("node")
    if not node and os.path.isfile(r"C:\nvm4w\nodejs\node.exe"):
        node = r"C:\nvm4w\nodejs\node.exe"
    if node:
        return ["--js-runtimes", f"node:{node}"]
    return []


YTDLP_JS_ARGS = _ytdlp_js_args()


def _ytdlp_cmd(*extra_args):
    binary, btype = find_ytdlp_binary()
    if not binary:
        raise RuntimeError("yt-dlp bulunamadi")
    if btype == "module":
        return [binary, "-m", "yt_dlp", *YTDLP_JS_ARGS, *extra_args]
    return [binary, *YTDLP_JS_ARGS, *extra_args]


class YDLOutputParser:
    """Parse yt-dlp JSON output into structured download info."""

    @staticmethod
    def parse_json_output(stdout_text):
        """Parse yt-dlp --dump-json output. Returns structured dict."""
        if not stdout_text:
            return {"error": "Bos cikti"}
        try:
            data = json.loads(stdout_text.strip().split("\n")[-1])
        except json.JSONDecodeError:
            return {"error": "JSON parse hatasi"}

        formats = data.get("formats") or []
        entry_type = YDLOutputParser._classify_entry(formats, data)
        available_formats = YDLOutputParser._extract_formats(data, formats)

        return {
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "duration": data.get("duration", 0),
            "thumbnail": data.get("thumbnail", ""),
            "uploader": data.get("uploader", ""),
            "upload_date": data.get("upload_date", ""),
            "view_count": data.get("view_count", 0),
            "webpage_url": data.get("webpage_url", ""),
            "extractor": data.get("extractor", ""),
            "entry_type": entry_type,
            "formats": available_formats,
            "is_live": data.get("is_live", False),
            "was_live": data.get("was_live", False),
        }

    @staticmethod
    def _classify_entry(formats, data):
        protocol = data.get("protocol", "")
        ext = (data.get("ext") or "").lower()

        if data.get("is_live") or protocol in ("m3u8", "m3u8_native"):
            return "hls_live"
        if protocol == "http_dash_segments" or ext == "mpd":
            return "dash"
        if ext == "m3u8" or any(f.get("protocol", "").startswith("m3u8") for f in formats):
            return "hls"

        has_video = any(
            (f.get("vcodec") or "none") != "none" and (f.get("ext") or "").lower() not in NON_VIDEO_FORMAT_EXTS
            for f in formats
        )
        has_audio = any(
            (f.get("acodec") or "none") != "none"
            for f in formats
        )
        has_separate_streams = any(
            (f.get("vcodec") or "none") != "none" and (f.get("acodec") or "none") == "none"
            for f in formats
        )

        if has_separate_streams:
            return "dual_http"  # video-only + audio-only streams
        if has_video and has_audio:
            return "single_http"
        if has_video:
            return "single_http"
        return "unknown"

    @staticmethod
    def _extract_formats(data, formats):
        """Extract available format options grouped and sorted."""
        result = []

        # Add auto best
        result.append({
            "id": "best",
            "label": "En Iyi (Otomatik)",
            "detail": "Otomatik en iyi kalite",
            "ext": "",
            "resolution": "",
            "filesize": 0,
            "type": "best",
        })

        seen = set()
        for f in formats:
            fid = f.get("format_id", "")
            ext = (f.get("ext") or "").lower()
            if ext in NON_VIDEO_FORMAT_EXTS:
                continue
            if fid in seen:
                continue
            seen.add(fid)

            vcodec = (f.get("vcodec") or "none").lower()
            acodec = (f.get("acodec") or "none").lower()
            width = f.get("width") or 0
            height = f.get("height") or 0
            filesize = f.get("filesize") or f.get("filesize_approx") or 0
            fps = f.get("fps") or 0
            tbr = f.get("tbr") or 0
            resolution_str = f.get("resolution", "")
            format_note = f.get("format_note", "")

            if vcodec != "none" and acodec != "none" and ext in ("mp4", "webm"):
                res_str = resolution_str or f"{height}p"
                label = f"{res_str} {format_note}" if format_note else res_str
                result.append({
                    "id": fid, "label": label, "detail": f"{ext.upper()} {tbr:.0f}kbps",
                    "ext": ext, "resolution": res_str, "filesize": filesize,
                    "type": "video+audio",
                })

        for f in formats:
            fid = f.get("format_id", "")
            ext = (f.get("ext") or "").lower()
            if ext in NON_VIDEO_FORMAT_EXTS:
                continue
            vcodec = (f.get("vcodec") or "none").lower()
            acodec = (f.get("acodec") or "none").lower()
            height = f.get("height") or 0
            fps = f.get("fps") or 0
            tbr = f.get("tbr") or 0
            filesize = f.get("filesize") or f.get("filesize_approx") or 0
            format_note = f.get("format_note", "")

            if vcodec != "none" and acodec == "none":
                res = f"{height}p" if height else format_note
                label = f"{res} (video only)"
                result.append({
                    "id": fid, "label": label, "detail": f"{ext.upper()} {fps}fps" if fps else ext.upper(),
                    "ext": ext, "resolution": res or "", "filesize": filesize,
                    "type": "video_only",
                })

        for f in formats:
            fid = f.get("format_id", "")
            ext = (f.get("ext") or "").lower()
            if ext in NON_VIDEO_FORMAT_EXTS:
                continue
            vcodec = (f.get("vcodec") or "none").lower()
            acodec = (f.get("acodec") or "none").lower()
            tbr = f.get("tbr") or 0
            filesize = f.get("filesize") or f.get("filesize_approx") or 0
            abr = f.get("abr") or 0

            if vcodec == "none" and acodec != "none":
                label = f"{abr:.0f}kbps" if abr else f"{tbr:.0f}kbps"
                result.append({
                    "id": fid, "label": f"Ses ({label})", "detail": ext.upper(),
                    "ext": ext, "resolution": "", "filesize": filesize,
                    "type": "audio_only",
                })

        return result

    @staticmethod
    def find_best_dual_formats(formats_data):
        """Find best video-only + audio-only format pair from parsed data."""
        video_formats = [f for f in formats_data if f["type"] == "video_only"]
        audio_formats = [f for f in formats_data if f["type"] == "audio_only"]

        video_formats.sort(key=lambda x: (
            int(x.get("resolution", "0").replace("p", "")) if x.get("resolution", "").endswith("p") else 0,
            x.get("filesize", 0)
        ), reverse=True)

        audio_formats.sort(key=lambda x: x.get("filesize", 0), reverse=True)

        best_video = video_formats[0] if video_formats else None
        best_audio = audio_formats[0] if audio_formats else None

        return best_video, best_audio
