# -*- coding: utf-8 -*-
"""Agent-Reach Web Extractor — Jina Reader entegrasyonu (gercek API).

Herhangi bir web sayfasini temiz metin olarak okur ve video URL'lerini tespit eder.
Gercek cagri: agent_reach.channels.WebChannel.read(url) -> Markdown metin.
Agirlik: fallback zincirinde genel yakalayici; stream bulamazsa "note" dondurur
boylece zincir sonraki extractor'a devam eder.
"""
import re

from backend.extractors import BaseExtractor, ExtractorRegistry

try:
    from agent_reach.channels import get_channel
    HAS_AGENT_REACH = True
except ImportError:
    HAS_AGENT_REACH = False


class AgentReachWebExtractor(BaseExtractor):
    name = "agent_reach_web"
    domains = []  # Tum domainler icin gecerli (fallback)
    patterns = []

    def extract(self, url):
        """Web sayfasini Jina Reader ile okuyup video tespiti yapar."""
        if not HAS_AGENT_REACH:
            return {"error": "agent-reach kurulu degil", "url": url}

        try:
            channel = get_channel("web")
            if channel is None:
                return {"error": "Agent-Reach web kanali bulunamadi", "url": url}
            content = channel.read(url)
        except RuntimeError as e:
            # Jina antibot/verification sayfasi dondurdu
            return {"error": f"Jina Reader engellendi: {str(e)[:150]}", "url": url}
        except ValueError as e:
            return {"error": f"Jina Reader limit: {str(e)[:150]}", "url": url}
        except Exception as e:
            return {"error": f"Web sayfasi okunamadi: {str(e)[:150]}", "url": url}

        video_urls = self._extract_video_urls(content, url)
        if video_urls:
            return {
                "title": self._extract_title(content),
                "video_url": video_urls[0].get("detail", ""),
                "formats": video_urls,
                "source": "agent-reach-web",
                "url": url,
            }
        return {
            "title": self._extract_title(content),
            "source": "agent-reach-web",
            "url": url,
            "note": "Video bulunamadi, metin icerigi mevcut",
            "text_preview": content[:500] if content else "",
        }

    def _extract_video_urls(self, content, base_url):
        """Icerikten video URL'lerini cikarir."""
        video_patterns = [
            r'https?://[^\s\'"<>]+\.(?:mp4|webm|mov|avi|mkv|flv)(?:\?[^\s\'"<>]*)?',
            r'https?://[^\s\'"<>]+\.(?:m3u8|mpd)(?:\?[^\s\'"<>]*)?',
            r'(?:src|href|data-src)=["\']([^"\']*\.(?:mp4|webm|mov|avi|mkv|flv|m3u8|mpd)[^"\']*)["\']',
        ]

        found_urls = set()
        formats = []

        for pattern in video_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                url_val = match if isinstance(match, str) else match[0] if match else ""
                if url_val and url_val not in found_urls:
                    found_urls.add(url_val)
                    ext = url_val.split(".")[-1].lower().split("?")[0]
                    if "m3u8" in ext:
                        label = "HLS Stream"
                    elif "mpd" in ext:
                        label = "DASH Stream"
                    else:
                        label = ext.upper() + " Video"

                    formats.append({
                        "id": f"format_{len(formats)+1}",
                        "label": label,
                        "detail": url_val,
                        "priority": 1 if ext in ["mp4", "webm"] else 2 if "m3u8" in ext else 3,
                    })

        formats.sort(key=lambda x: x.get("priority", 99), reverse=True)
        return formats

    def _extract_title(self, content):
        """Markdown/HTML icerikten basligi cikarir."""
        # Jina Markdown ciktisinda ilk "# Baslik" satiri
        for line in (content or "").splitlines():
            s = line.strip()
            if s.startswith("# "):
                title = s[2:].strip()
                if len(title) > 2:
                    return title[:200]
        title_patterns = [
            r'<title[^>]*>([^<]+)</title>',
            r'<h1[^>]*>([^<]+)</h1>',
            r'meta\s+property=["\']og:title["\']\s+content=["\']([^"\']*)["\']',
            r'meta\s+name=["\']title["\']\s+content=["\']([^"\']*)["\']',
        ]
        for pattern in title_patterns:
            match = re.search(pattern, content or "", re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                if title and len(title) > 2:
                    return title[:200]
        return "Web sayfasi"


ExtractorRegistry.register(AgentReachWebExtractor)
