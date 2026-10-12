# -*- coding: utf-8 -*-
"""Scrapling Spider Extractor — Multi-page crawling for playlists, catalogs, channel listings.

Scrapling'in Spider framework ile:
- Playlist sayfalarından tüm video linklerini toplar
- Katalog/channel sayfalarında otomatik link izler
- Pause/resume + checkpoint desteği
- Concurrent crawling
"""
import re
import json
import sys
import os
from typing import List, Dict, Any, Optional

from backend.extractors import BaseExtractor, ExtractorRegistry

try:
    from scrapling.spiders import Spider, Response, Request
    from scrapling.fetchers import FetcherSession
    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False


class _VideoLinkSpider(Spider):
    """Genel video link spider'ı — belirtilen sayfada tüm video linklerini toplar."""
    name = "video_link_spider"
    start_urls: List[str] = []
    concurrent_requests = 10
    custom_settings = {
        "robots_txt_obey": False,
        "download_delay": 0.5,
        "timeout": 30,
    }

    # Video URL pattern — .mp4, .m3u8, .mpd, .webm ve video sayfa linkleri
    VIDEO_URL_PATTERN = re.compile(
        r'https?://[^\s"\'<>]+?\.(?:mp4|webm|mov|avi|mkv|flv|m4v|m3u8|mpd)',
        re.IGNORECASE,
    )

    # Video sayfası link patternleri (örneğin /watch?v=..., /video/...)
    PAGE_LINK_PATTERNS = [
        r'href\s*=\s*["\']([^"\']*watch\?v=[^"\']*)["\']',
        r'href\s*=\s*["\']([^"\']*/video/[^"\']*)["\']',
        r'href\s*=\s*["\']([^"\']*/episode/[^"\']*)["\']',
        r'href\s*=\s*["\']([^"\']*/play/[^"\']*)["\']',
    ]

    found_links: List[str] = []
    found_video_urls: List[str] = []

    def configure_sessions(self, manager):
        manager.add("default", FetcherSession(impersonate="chrome"))

    async def parse(self, response: Response):
        # Video elementlerini topla
        for video in response.css('video'):
            src = video.css('::attr(src)').get()
            if src and src not in self.found_video_urls:
                self.found_video_urls.append(src)

        # Sayfa içindeki video URL'lerini tara
        text = response.text or ""
        for m in self.VIDEO_URL_PATTERN.finditer(text):
            url = m.group(0)
            if url not in self.found_video_urls:
                self.found_video_urls.append(url)

        # Linkleri topla
        for pattern in self.PAGE_LINK_PATTERNS:
            for m in re.finditer(pattern, text):
                link = m.group(1)
                if link.startswith("http") and link not in self.found_links:
                    self.found_links.append(link)
                    yield Request(link, callback=self.parse_if_video_page)

    async def parse_if_video_page(self, response: Response):
        """Video sayfası mı? (sayfa başlığı video, watch, vs. içeriyorsa)"""
        if not self.found_video_urls:
            title = response.css("title::text").get() or ""
            if "video" in title.lower() or "watch" in response.url:
                # Video elementlerini kontrol et
                for video in response.css('video'):
                    src = video.css('::attr(src)').get()
                    if src and src not in self.found_video_urls:
                        self.found_video_urls.append(src)

                # Text tarama
                text = response.text or ""
                for m in self.VIDEO_URL_PATTERN.finditer(text):
                    url = m.group(0)
                    if url not in self.found_video_urls:
                        self.found_video_urls.append(url)


class ScraplingSpiderExtractor(BaseExtractor):
    name = "scrapling_spider"
    domains = []
    patterns = []

    def extract(self, url):
        """Multi-page crawl: playlist/catalog sayfalarından video linkleri toplar."""
        if not HAS_SCRAPLING:
            return {"error": "Scrapling spider'ları yüklü değil. pip install scrapling[fetchers]"}

        try:
            # Spider'ı yapılandır
            _VideoLinkSpider.start_urls = [url]
            _VideoLinkSpider.found_links = []
            _VideoLinkSpider.found_video_urls = []

            # Crawl başlat
            spider = _VideoLinkSpider(crawldir="./.scrapling_crawls")
            spider.start()

            result = {
                "title": f"Crawled: {url}",
                "source": "scrapling-spider",
                "url": url,
                "page_links_found": _VideoLinkSpider.found_links,
                "video_urls_found": _VideoLinkSpider.found_video_urls,
            }

            if _VideoLinkSpider.found_video_urls:
                result["video_url"] = _VideoLinkSpider.found_video_urls[0]
                result["formats"] = [
                    {"id": f"spider_{i}", "label": "Crawled Video", "detail": v}
                    for i, v in enumerate(_VideoLinkSpider.found_video_urls[:20])
                ]
            else:
                result["note"] = "Spider video URL bulamadı"

            return result
        except Exception as e:
            return {"error": f"Scrapling spider hata: {str(e)[:200]}", "url": url}

    def get_formats(self, url):
        """Kanaldaki tüm video linklerini döndürür."""
        return self.extract(url)


ExtractorRegistry.register(ScraplingSpiderExtractor)