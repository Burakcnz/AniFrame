# -*- coding: utf-8 -*-
"""Scrapling Stealth Extractor — Cloudflare bypass + anti-bot + fingerprint spoofing.

Scrapling'in StealthyFetcher ve DynamicFetcher ile sayfalardan video URL'lerini çıkarır.
Cloudflare Turnstile bypass, fingerprint spoofing, session management.
"""
import re
import json
import sys
import os

from backend.extractors import BaseExtractor, ExtractorRegistry

# Scrapling modüllerini import et
try:
    from scrapling.fetchers import Fetcher, AsyncFetcher, StealthyFetcher, DynamicFetcher
    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False


class ScraplingStealthExtractor(BaseExtractor):
    name = "scrapling_stealth"
    domains = []  # Tüm domainler için geçerli
    patterns = []

    def extract(self, url):
        """Scrapling ile sayfayı fetch edip video URL'lerini tespit eder."""
        if not HAS_SCRAPLING:
            return {"error": "Scrapling fetcher'ları yüklü değil. pip install scrapling[fetchers]"}

        try:
            # StealthyFetcher ile sayfayı fetch et (Cloudflare bypass)
            page = StealthyFetcher.fetch(
                url,
                headless=True,
                solve_cloudflare=True,
                network_idle=True,
            )

            # Video elementlerini tespit et
            videos = self._extract_videos(page)
            title = self._extract_title(page)
            thumbnail = self._extract_thumbnail(page)

            if videos:
                return {
                    "title": title,
                    "video_url": videos[0]["url"] if videos else "",
                    "formats": videos,
                    "source": "scrapling-stealth",
                    "url": url,
                }
            else:
                return {
                    "title": title,
                    "source": "scrapling-stealth",
                    "url": url,
                    "thumbnail": thumbnail,
                    "note": "Video bulunamadı",
                }
        except Exception as e:
            return {"error": f"Scrapling hata: {str(e)[:200]}", "url": url}

    def _extract_videos(self, page):
        """Scrapling sayfasından video URL'lerini çıkarır."""
        video_urls = []
        seen = set()

        # 1. <video> elementlerinden src al
        try:
            video_elements = page.css("video")
            for video in video_elements:
                src = video.get_attribute("src")
                if src and src not in seen and not src.startswith(("data:", "blob:")):
                    seen.add(src)
                    video_urls.append({
                        "id": f"video_{len(video_urls)+1}",
                        "label": "Video Element",
                        "detail": src,
                        "priority": 5,
                    })
                # source alt elementleri
                sources = video.css("source")
                for source in sources:
                    ssrc = source.get_attribute("src")
                    if ssrc and ssrc not in seen and not ssrc.startswith(("data:", "blob:")):
                        seen.add(ssrc)
                        video_urls.append({
                            "id": f"source_{len(video_urls)+1}",
                            "label": "Source Element",
                            "detail": ssrc,
                            "priority": 5,
                        })
        except Exception:
            pass

        # 2. Network capture (XHR/fetch responses)
        try:
            # Scrapling'in response capture feature'ını kullan
            # response.captured_xhr contains all XHR/fetch responses
            if hasattr(page, 'captured_xhr'):
                for response in page.captured_xhr:
                    resp_url = response.url if hasattr(response, 'url') else ""
                    if resp_url and resp_url not in seen and self._is_video_url(resp_url):
                        seen.add(resp_url)
                        video_urls.append({
                            "id": f"xhr_{len(video_urls)+1}",
                            "label": "API Response",
                            "detail": resp_url,
                            "priority": 4,
                        })
        except Exception:
            pass

        # 3. Sayfa içeriğinden video URL'leri ara
        try:
            html = page.html or ""
            video_patterns = [
                r'https?://[^\s\'"<>\\]+\.(?:m3u8|mp4|webm|mov|avi|mkv|flv|mpd)(?:\?[^\s\'"<>\\]*)?',
                r'(?:src|href|data-src)=["\']([^"\']*\.(?:mp4|webm|mov|avi|mkv|flv|m3u8|mpd)[^"\']*)["\']',
            ]
            for pattern in video_patterns:
                matches = re.findall(pattern, html, re.IGNORECASE)
                for match in matches:
                    url_val = match if isinstance(match, str) else match[0] if match else ""
                    if url_val and url_val not in seen and not url_val.startswith(("data:", "blob:")):
                        seen.add(url_val)
                        ext = url_val.split(".")[-1].lower().split("?")[0]
                        label = "HLS Stream" if "m3u8" in ext else "DASH Stream" if "mpd" in ext else ext.upper() + " Video"
                        video_urls.append({
                            "id": f"html_{len(video_urls)+1}",
                            "label": label,
                            "detail": url_val,
                            "priority": 3,
                        })
        except Exception:
            pass

        # Öncelik sırasına göre sırala
        video_urls.sort(key=lambda x: x.get("priority", 0), reverse=True)
        return video_urls

    def _is_video_url(self, url):
        """URL'nin video olup olmadığını kontrol et."""
        video_exts = (".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".m4v", ".ts", ".m3u8", ".mpd")
        url_lower = url.lower().split("?")[0]
        return any(url_lower.endswith(ext) for ext in video_exts)

    def _extract_title(self, page):
        """Sayfa başlığı."""
        try:
            title = page.css("title::text").get()
            if title and len(title) > 2:
                return title.strip()
        except Exception:
            pass
        try:
            og_title = page.css('meta[property="og:title"]::attr(content)').get()
            if og_title:
                return og_title.strip()
        except Exception:
            pass
        return "Web sayfası"

    def _extract_thumbnail(self, page):
        """Sayfa thumbnail."""
        try:
            thumb = page.css('meta[property="og:image"]::attr(content)').get()
            if thumb:
                return thumb.strip()
        except Exception:
            pass
        return ""


ExtractorRegistry.register(ScraplingStealthExtractor)