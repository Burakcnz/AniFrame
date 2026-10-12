# -*- coding: utf-8 -*-
"""Agent-Reach Social Extractor — gercek API (channel.check per platform).

Desteklenen platformlar: Twitter/X, Instagram, Facebook, LinkedIn, Reddit.
Gercek cagri: agent_reach.channels.get_channel("<ad>").check()
  -> (status, message); aktif backend: twitter-cli / OpenCLI / bird / rdt ...

Bu platformlar giris/cookie gerektirir; upstream CLI kurulu ve yapilandirilmis
degilse extractor "note" dondurur ve zincir sonraki adima devam eder.
Sahte format URL'si uretilmez.
"""
import shutil

from backend.extractors import BaseExtractor, ExtractorRegistry

try:
    from agent_reach.channels import get_channel
    HAS_AGENT_REACH = True
except ImportError:
    HAS_AGENT_REACH = False

SOCIAL_DOMAINS = {
    "twitter": ["twitter.com", "x.com", "t.co"],
    "instagram": ["instagram.com", "www.instagram.com", "cdninstagram.com"],
    "facebook": ["facebook.com", "fb.watch", "fb.com", "m.facebook.com"],
    "reddit": ["reddit.com", "www.reddit.com", "old.reddit.com"],
    "linkedin": ["linkedin.com", "www.linkedin.com"],
}

# Kanal adi -> kontrol edilecek yardimci binary'ler (bilgi amacli)
_HELPER_BINARIES = {
    "twitter": ["twitter", "bird", "birdx"],
    "instagram": [],
    "facebook": [],
    "reddit": ["rdt"],
    "linkedin": [],
}


class AgentReachSocialExtractor(BaseExtractor):
    name = "agent_reach_social"
    domains = sum(SOCIAL_DOMAINS.values(), [])
    patterns = [
        "twitter.com/", "x.com/",
        "instagram.com/reel", "instagram.com/p/", "instagram.com/stories",
        "facebook.com/", "fb.watch/", "fb.com/",
        "reddit.com/r/", "reddit.com/user/",
        "linkedin.com/posts", "linkedin.com/feed",
    ]

    def _detect_platform(self, url):
        for platform, domains in SOCIAL_DOMAINS.items():
            for domain in domains:
                if domain in (url or ""):
                    return platform
        return "unknown"

    def extract(self, url):
        if not HAS_AGENT_REACH:
            return {"error": "agent-reach kurulu degil", "url": url}

        platform = self._detect_platform(url)
        if platform == "unknown":
            return {"error": "Desteklenmeyen platform", "url": url}

        status, message, active = self._check_channel(platform)
        helpers = {
            b: shutil.which(b) is not None for b in _HELPER_BINARIES.get(platform, [])
        }

        if status == "ok" and active:
            # Backend saglikli ama bu kanal dogrudan stream vermez; indirme
            # AniFrame manager (yt-dlp/native) uzerinden yurur.
            return {
                "title": f"{platform} icerigi",
                "source": "agent-reach-social",
                "platform": platform,
                "url": url,
                "backend_status": status,
                "active_backend": active,
                "helpers": helpers,
                "note": f"{platform} backend hazir ({active}); stream cikarma AniFrame manager ile devam eder.",
            }

        return {
            "source": "agent-reach-social",
            "platform": platform,
            "url": url,
            "backend_status": status,
            "active_backend": active,
            "helpers": helpers,
            "note": f"{platform} henuz yapilandirilmamis ({status}). Detay: {(message or '')[:300]}",
        }

    def _check_channel(self, platform):
        """Gercek Agent-Reach channel.check() cagrisi. Donus: (status, msg, active)."""
        try:
            channel = get_channel(platform)
            if channel is None:
                return "error", f"{platform} kanali bulunamadi", None
            status, message = channel.check()
            return status, message, channel.active_backend
        except Exception as e:
            return "error", str(e)[:200], None


ExtractorRegistry.register(AgentReachSocialExtractor)
