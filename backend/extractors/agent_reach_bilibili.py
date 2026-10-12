# -*- coding: utf-8 -*-
"""Agent-Reach Bilibili Extractor — gercek API (channel.check + public view API).

yt-dlp Bilibili'de 412 engeline takildigi icin Agent-Reach kanali kullanilir.
Gercek cagri: agent_reach.channels.get_channel("bilibili").check()
  -> (status, message); aktif backend: bili-cli / OpenCLI / Bilibili Search API.

Dogrudan stream (playurl) giris/imza gerektirir, bu yuzden bu extractor
gercek metadata (baslik, thumbnail, sure, yukleyici) + backend durumu dondurur
ve zincirin devam etmesi icin her zaman "note" icerir. Stream bulundugunda
scrapling/browser sonraki adimlarda dener; `bili` CLI kuruluysa kurulum
yonergesi mesajda yer alir.
"""
import json
import re
import shutil
import urllib.request
from urllib.parse import urlparse

from backend.extractors import BaseExtractor, ExtractorRegistry

try:
    from agent_reach.channels import get_channel
    HAS_AGENT_REACH = True
except ImportError:
    HAS_AGENT_REACH = False

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_VIEW_API = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
_BVID_RE = re.compile(r"(BV[a-zA-Z0-9]+)")


def _resolve_bvid(url):
    """URL'den BVID cikarir; b23.tv kisa linklerini redirect ile cozer."""
    m = _BVID_RE.search(url or "")
    if m:
        return m.group(1)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl()
        m = _BVID_RE.search(final_url or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _fetch_view_metadata(bvid):
    """Public view API'den gercek metadata alir (giris gerekmez)."""
    req = urllib.request.Request(
        _VIEW_API.format(bvid=bvid), headers={"User-Agent": _UA}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    if data.get("code") != 0:
        return {}
    info = data.get("data") or {}
    owner = info.get("owner") or {}
    return {
        "title": info.get("title", ""),
        "thumbnail": info.get("pic", ""),
        "duration": info.get("duration", 0),
        "uploader": owner.get("name", ""),
    }


class AgentReachBilibiliExtractor(BaseExtractor):
    name = "agent_reach_bilibili"
    domains = ["bilibili.com", "b23.tv"]
    patterns = ["bilibili.com/video", "b23.tv/"]

    def extract(self, url):
        if not HAS_AGENT_REACH:
            return {"error": "agent-reach kurulu degil", "url": url}

        backend_status, backend_msg, active = self._check_backend()

        bvid = _resolve_bvid(url)
        meta = {}
        if bvid:
            try:
                meta = _fetch_view_metadata(bvid)
            except Exception:
                meta = {}

        bili_cli_present = shutil.which("bili") is not None

        if not meta:
            # Hicbir gercek veri yok: zincir devam etsin
            if backend_status in ("off", "error"):
                return {
                    "error": f"Bilibili backend kullanilamiyor ({backend_status})",
                    "url": url,
                    "hint": backend_msg,
                }
            return {
                "url": url,
                "source": "agent-reach-bilibili",
                "note": "Video metadata alinamadi. Oneri: pipx install bilibili-cli",
                "backend_status": backend_status,
                "active_backend": active,
            }

        result = {
            "title": meta.get("title") or f"Bilibili video {bvid}",
            "thumbnail": meta.get("thumbnail", ""),
            "duration": meta.get("duration", 0),
            "uploader": meta.get("uploader", ""),
            "bvid": bvid,
            "source": "agent-reach-bilibili",
            "url": url,
            "backend_status": backend_status,
            "active_backend": active,
            "note": (
                "Dogrudan stream yok (giris/imza gerekir). "
                + (
                    "bili CLI kurulu, `bili download` ile indirmeyi deneyin."
                    if bili_cli_present
                    else "Oneri: pipx install bilibili-cli, sonra `bili download <url>`."
                )
            ),
        }
        return result

    def _check_backend(self):
        """Gercek Agent-Reach channel.check() cagrisi. Donus: (status, msg, active)."""
        try:
            channel = get_channel("bilibili")
            if channel is None:
                return "error", "Bilibili kanali bulunamadi", None
            status, message = channel.check()
            return status, message, channel.active_backend
        except Exception as e:
            return "error", str(e)[:200], None


ExtractorRegistry.register(AgentReachBilibiliExtractor)
