# -*- coding: utf-8 -*-
"""Universal Fetcher — Oncelik sirasi ile tum extractor'lari deneyen merkezi motor.

Siralama:
1. yt-dlp (YouTube, Vimeo, Dailymotion, Twitter, Instagram, TikTok — bilinen 1800+ site)
2. Agent-Reach Bilibili (kanal durumu + public metadata; stream icin not birakir)
3. Agent-Reach Social (kanal durumu; yapilandirma yoksa not birakir)
4. Agent-Reach Web (Jina Reader — genel web sayfasi)
5. Scrapling Stealth (Cloudflare bypass + anti-bot)
6. Scrapling Spider (multi-page crawl)
7. Mevcut Browser Extractor (Playwright — son care)

Basari kriteri: oynatilabilir veri (video_url / formats / video_formats /
audio_formats). Sadece baslik/metadata donduren sonuc zinciri durdurmaz;
daha sonraki extractor stream bulamazsa en iyi metadata geri dondurulur.
"""
import re
import json
import sys
import os
import logging
from typing import Dict, Any, Optional, List, Tuple

from backend.extractors import ExtractorRegistry

logger = logging.getLogger("universal_fetcher")


class UniversalFetcher:
    """Tüm extractor'ları öncelik sırası ile deneyen merkezi video çekme motoru."""

    def __init__(self):
        self.extractors = self._load_extractors()
        self.priority_chain = self._build_priority_chain()

    def _load_extractors(self) -> List[Any]:
        """Tüm extractor'ları yükle."""
        try:
            from backend.extractors import _auto_register
            _auto_register()
        except Exception:
            pass
        return ExtractorRegistry.get_all()

    def _build_priority_chain(self) -> List[Tuple[str, type]]:
        """Öncelik sırası ile extractor listesi."""
        chain = []
        # Mevcut extractor'lar
        for ext in self.extractors:
            name = ext.name
            if name == "youtube":
                chain.append(("yt-dlp", ext))
            elif name == "vimeo":
                chain.append(("yt-dlp", ext))
            elif name == "dailymotion":
                chain.append(("yt-dlp", ext))
            elif name == "twitter":
                chain.append(("yt-dlp", ext))
            elif name == "instagram":
                chain.append(("yt-dlp", ext))
            elif name == "tiktok":
                chain.append(("yt-dlp", ext))
            elif name == "agent_reach_bilibili":
                chain.append(("agent-reach-bilibili", ext))
            elif name == "agent_reach_social":
                chain.append(("agent-reach-social", ext))
            elif name == "agent_reach_web":
                chain.append(("agent-reach-web", ext))
            elif name == "scrapling_stealth":
                chain.append(("scrapling-stealth", ext))
            elif name == "scrapling_spider":
                chain.append(("scrapling-spider", ext))
        return chain

    def fetch(self, url: str, prefer: Optional[str] = None) -> Dict[str, Any]:
        """URL'den video bilgisi çıkarır. Öncelik sırası ile extractor'ları dener.

        Args:
            url: Çekilecek URL
            prefer: Belirli bir extractor'ı öncelikli kullanmak için isim (örn: "scrapling-stealth")

        Returns:
            Dict: {title, video_url, formats, source, ...}
        """
        # Belirli bir extractor'ı öncelikli kullan
        if prefer:
            for label, ext in self.priority_chain:
                if label == prefer:
                    result = ext.extract(url)
                    result.setdefault("priority_used", prefer)
                    return result

        # Normal oncelik sirasi. Basari = oynatilabilir veri.
        errors = []
        best_meta = None
        for label, ext in self.priority_chain:
            try:
                result = ext.extract(url)
                if result.get("error"):
                    errors.append({"source": label, "error": result["error"]})
                    continue
                if (
                    result.get("video_url")
                    or result.get("formats")
                    or result.get("video_formats")
                    or result.get("audio_formats")
                ):
                    result["priority_used"] = label
                    return result
                # Sadece metadata var: stream icin zincire devam et,
                # en iyi metadata'yi yedekte tut.
                if result.get("title") and best_meta is None:
                    best_meta = (label, result)
                if result.get("note"):
                    errors.append({"source": label, "note": result["note"]})
                    continue
            except Exception as e:
                errors.append({"source": label, "error": str(e)[:200]})
                continue

        # Son çare: Browser Extractor
        try:
            from backend.browser_extractor import extract_from_page
            result = extract_from_page(url)
            if result.get("video_url") or result.get("formats"):
                result["priority_used"] = "browser-extractor"
                return result
        except Exception as e:
            errors.append({"source": "browser-extractor", "error": str(e)[:200]})

        # Stream bulunamadi ama metadata varsa onu dondur
        if best_meta is not None:
            label, meta = best_meta
            meta["priority_used"] = label
            meta.setdefault("note", "Stream bulunamadi, sadece metadata donduruldu")
            return meta

        # Hiçbir extractor çalışmazsa
        return {
            "error": "Tum extractor'lar basarisiz oldu",
            "url": url,
            "tried": errors,
            "priority_used": None,
        }

    def fetch_with_fallback(self, url: str, exclude: List[str] = None) -> Dict[str, Any]:
        """Belirli extractor'lari haric tutarak fetch yapar (oynatilabilir veri arar)."""
        if exclude is None:
            exclude = []
        for label, ext in self.priority_chain:
            if label in exclude:
                continue
            try:
                result = ext.extract(url)
                if result.get("error"):
                    continue
                if (
                    result.get("video_url")
                    or result.get("formats")
                    or result.get("video_formats")
                    or result.get("audio_formats")
                ):
                    result["priority_used"] = label
                    return result
            except Exception:
                continue
        return {"error": "Fallback extractor'lar da basarisiz oldu", "url": url}


# Singleton instance
_fetcher_instance = None

def get_universal_fetcher() -> UniversalFetcher:
    """Singleton UniversalFetcher instance'ı döndürür."""
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = UniversalFetcher()
    return _fetcher_instance


def fetch_video(url: str, prefer: Optional[str] = None) -> Dict[str, Any]:
    """Kolayca video bilgisi almak için shortcut fonksiyon."""
    return get_universal_fetcher().fetch(url, prefer=prefer)


def fetch_video_with_fallback(url: str, exclude: List[str] = None) -> Dict[str, Any]:
    """Fallback ile video bilgisi al."""
    return get_universal_fetcher().fetch_with_fallback(url, exclude=exclude)