import re
import json
import subprocess
import sys
import os
from backend.extractors import BaseExtractor, ExtractorRegistry

ytdlp_js_args = []
try:
    import shutil
    if not shutil.which("deno"):
        node = shutil.which("node")
        if not node and os.path.isfile(r"C:\nvm4w\nodejs\node.exe"):
            node = r"C:\nvm4w\nodejs\node.exe"
        if node:
            ytdlp_js_args = ["--js-runtimes", f"node:{node}"]
except Exception:
    pass


class YouTubeExtractor(BaseExtractor):
    name = "youtube"
    domains = ["youtube.com", "youtu.be", "m.youtube.com", "www.youtube.com"]
    patterns = ["youtube.com/watch", "youtu.be/"]

    def extract(self, url):
        cmd = [sys.executable, "-m", "yt_dlp", *ytdlp_js_args, "--dump-json", "--no-playlist", url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": "YouTube verisi alinamadi"}
            data = json.loads(result.stdout.strip().split("\n")[-1])
            formats = data.get("formats", [])
            video_formats = []
            audio_formats = []
            for f in formats:
                fid = f.get("format_id", "")
                vcodec = (f.get("vcodec") or "none").lower()
                acodec = (f.get("acodec") or "none").lower()
                ext = (f.get("ext") or "").lower()
                height = f.get("height") or 0
                filesize = f.get("filesize") or f.get("filesize_approx") or 0
                if vcodec != "none" and acodec == "none":
                    video_formats.append({"id": fid, "height": height, "ext": ext, "filesize": filesize})
                elif vcodec == "none" and acodec != "none":
                    audio_formats.append({"id": fid, "ext": ext, "filesize": filesize, "abr": f.get("abr") or 0})
            video_formats.sort(key=lambda x: x["height"], reverse=True)
            audio_formats.sort(key=lambda x: x.get("abr", 0), reverse=True)
            return {
                "title": data.get("title", ""),
                "duration": data.get("duration", 0),
                "thumbnail": data.get("thumbnail", ""),
                "uploader": data.get("uploader", ""),
                "video_formats": video_formats,
                "audio_formats": audio_formats,
                "source": "youtube_extractor",
            }
        except Exception as e:
            return {"error": str(e)}


class VimeoExtractor(BaseExtractor):
    name = "vimeo"
    domains = ["vimeo.com", "player.vimeo.com"]
    patterns = ["vimeo.com/"]

    def extract(self, url):
        cmd = [sys.executable, "-m", "yt_dlp", *ytdlp_js_args, "--dump-json", "--no-playlist", url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": "Vimeo verisi alinamadi"}
            data = json.loads(result.stdout.strip().split("\n")[-1])
            formats = []
            for f in data.get("formats", []):
                fid = f.get("format_id", "")
                ext = (f.get("ext") or "").lower()
                height = f.get("height") or 0
                if height > 0 and ext in ("mp4", "webm"):
                    formats.append({"id": fid, "label": f"{height}p", "ext": ext})
            formats.sort(key=lambda x: int(x.get("label", "0").replace("p", "")), reverse=True)
            return {
                "title": data.get("title", ""),
                "duration": data.get("duration", 0),
                "thumbnail": data.get("thumbnail", ""),
                "formats": formats,
                "source": "vimeo_extractor",
            }
        except Exception as e:
            return {"error": str(e)}


class DailymotionExtractor(BaseExtractor):
    name = "dailymotion"
    domains = ["dailymotion.com", "www.dailymotion.com"]
    patterns = ["dailymotion.com/video"]

    def extract(self, url):
        cmd = [sys.executable, "-m", "yt_dlp", *ytdlp_js_args, "--dump-json", "--no-playlist", url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": "Dailymotion verisi alinamadi"}
            data = json.loads(result.stdout.strip().split("\n")[-1])
            formats = []
            for f in data.get("formats", []):
                fid = f.get("format_id", "")
                height = f.get("height") or 0
                ext = (f.get("ext") or "").lower()
                if height > 0:
                    formats.append({"id": fid, "label": f"{height}p", "ext": ext})
            formats.sort(key=lambda x: int(x.get("label", "0").replace("p", "")), reverse=True)
            return {
                "title": data.get("title", ""),
                "duration": data.get("duration", 0),
                "thumbnail": data.get("thumbnail", ""),
                "formats": formats,
                "source": "dailymotion_extractor",
            }
        except Exception as e:
            return {"error": str(e)}


class TwitterXExtractor(BaseExtractor):
    name = "twitter"
    domains = ["twitter.com", "x.com", "t.co"]
    patterns = ["twitter.com/", "x.com/"]

    def extract(self, url):
        cmd = [sys.executable, "-m", "yt_dlp", *ytdlp_js_args, "--dump-json", "--no-playlist", url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": "Twitter/X verisi alinamadi"}
            data = json.loads(result.stdout.strip().split("\n")[-1])
            formats = data.get("formats", [])
            found = []
            for f in formats:
                ext = (f.get("ext") or "").lower()
                if ext in ("mp4", "webm"):
                    found.append({"id": f.get("format_id", ""), "label": f"{f.get('height', 0)}p" if f.get("height") else ext, "ext": ext})
            return {
                "title": data.get("title", ""),
                "duration": data.get("duration", 0),
                "thumbnail": data.get("thumbnail", ""),
                "formats": found or [{"id": "best", "label": "Best", "ext": "mp4"}],
                "source": "twitter_extractor",
            }
        except Exception as e:
            return {"error": str(e)}


class InstagramExtractor(BaseExtractor):
    name = "instagram"
    domains = ["instagram.com", "www.instagram.com", "cdninstagram.com"]
    patterns = ["instagram.com/reel", "instagram.com/p/", "instagram.com/stories"]

    def extract(self, url):
        cmd = [sys.executable, "-m", "yt_dlp", *ytdlp_js_args, "--dump-json", "--no-playlist", url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": "Instagram verisi alinamadi"}
            data = json.loads(result.stdout.strip().split("\n")[-1])
            formats = []
            for f in data.get("formats", []):
                ext = (f.get("ext") or "").lower()
                if ext in ("mp4", "webm"):
                    formats.append({"id": f.get("format_id", ""), "label": f"{f.get('height', 0)}p" if f.get("height") else ext, "ext": ext})
            return {
                "title": data.get("title", ""),
                "duration": data.get("duration", 0),
                "thumbnail": data.get("thumbnail", ""),
                "formats": formats or [{"id": "best", "label": "Best", "ext": "mp4"}],
                "source": "instagram_extractor",
            }
        except Exception as e:
            return {"error": str(e)}


class TikTokExtractor(BaseExtractor):
    name = "tiktok"
    domains = ["tiktok.com", "www.tiktok.com", "vm.tiktok.com", "m.tiktok.com"]
    patterns = ["tiktok.com/@"]

    def extract(self, url):
        cmd = [sys.executable, "-m", "yt_dlp", *ytdlp_js_args, "--dump-json", "--no-playlist", url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return {"error": "TikTok verisi alinamadi"}
            data = json.loads(result.stdout.strip().split("\n")[-1])
            formats = []
            for f in data.get("formats", []):
                ext = (f.get("ext") or "").lower()
                if ext in ("mp4", "webm"):
                    formats.append({"id": f.get("format_id", ""), "label": f"{f.get('height', 0)}p" if f.get("height") else ext, "ext": ext})
            return {
                "title": data.get("title", ""),
                "duration": data.get("duration", 0),
                "thumbnail": data.get("thumbnail", ""),
                "formats": formats or [{"id": "best", "label": "Best", "ext": "mp4"}],
                "source": "tiktok_extractor",
            }
        except Exception as e:
            return {"error": str(e)}


ExtractorRegistry.register(YouTubeExtractor)
ExtractorRegistry.register(VimeoExtractor)
ExtractorRegistry.register(DailymotionExtractor)
ExtractorRegistry.register(TwitterXExtractor)
ExtractorRegistry.register(InstagramExtractor)
ExtractorRegistry.register(TikTokExtractor)
