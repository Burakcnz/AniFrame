import os
import json
import asyncio
import time
import hashlib
import secrets
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTEGRATION_CONFIG = os.path.join(BASE_DIR, "integration_config.json")

_DEFAULTS = {
    "enabled": True,
    "port": 8597,
    "api_key": secrets.token_hex(16),
    "allowed_origins": ["chrome-extension://*", "moz-extension://*"],
}


def _load_config():
    if not os.path.isfile(INTEGRATION_CONFIG):
        return dict(_DEFAULTS)
    try:
        with open(INTEGRATION_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = dict(_DEFAULTS)
        result.update(data)
        return result
    except Exception:
        return dict(_DEFAULTS)


def _save_config(data):
    with open(INTEGRATION_CONFIG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_api_key():
    return _load_config().get("api_key", "")


def is_enabled():
    return _load_config().get("enabled", True)


def verify_api_key(key):
    expected = _load_config().get("api_key", "")
    return expected and key == expected


class IntegrationSession:
    """Tracks extension connection state, similar to XDM connector.js."""

    def __init__(self):
        self.connected = False
        self.last_sync = None
        self.detected_videos = []
        self.tab_videos = {}
        self._video_index = 0

    def mark_connected(self):
        self.connected = True
        self.last_sync = datetime.now()

    def add_detected_video(self, data):
        entry = {
            "id": self._video_index,
            "url": data.get("video_url", data.get("url", "")),
            "page_url": data.get("page_url", ""),
            "title": data.get("title", ""),
            "type": data.get("video_type", ""),
            "quality": data.get("quality", ""),
            "hostname": data.get("hostname", ""),
            "tab_id": data.get("tab_id"),
            "detected_at": datetime.now().isoformat(),
        }
        self._video_index += 1
        self.detected_videos.append(entry)

        tab_id = str(data.get("tab_id", ""))
        if tab_id:
            if tab_id not in self.tab_videos:
                self.tab_videos[tab_id] = []
            self.tab_videos[tab_id].append(entry)

        return entry

    def get_videos_for_tab(self, tab_id):
        return self.tab_videos.get(str(tab_id), [])

    def clear_tab(self, tab_id):
        self.tab_videos.pop(str(tab_id), None)

    def get_state(self):
        return {
            "connected": self.connected,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "detected_count": len(self.detected_videos),
            "active_tabs": len(self.tab_videos),
        }


integration_session = IntegrationSession()
