import json
import os
from backend.database import get_db, get_setting

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_SETTINGS_FILE = os.path.join(BASE_DIR, "host_settings.json")

_DEFAULTS = {
    "max_connections": 4,
    "speed_limit_kbps": 0,
    "proxy": "",
    "user_agent_override": "",
    "custom_cookies": "",
    "skip_ytdlp": False,
}


def _load():
    if not os.path.isfile(HOST_SETTINGS_FILE):
        return {}
    try:
        with open(HOST_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    with open(HOST_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_host_settings(hostname):
    """Get settings for a specific host. Falls back to defaults."""
    data = _load()
    host = _normalize_host(hostname)
    settings = data.get(host, {})
    result = dict(_DEFAULTS)
    result.update(settings)
    return result


def set_host_setting(hostname, key, value):
    """Set a setting for a specific host."""
    data = _load()
    host = _normalize_host(hostname)
    if host not in data:
        data[host] = {}
    data[host][key] = value
    _save(data)


def delete_host_settings(hostname):
    """Remove all settings for a host."""
    data = _load()
    host = _normalize_host(hostname)
    data.pop(host, None)
    _save(data)


def _normalize_host(hostname):
    """Normalize hostname: remove www., keep domain only."""
    hostname = (hostname or "").lower().strip()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def get_all_host_settings():
    """Return all host settings."""
    return _load()


def get_max_connections_for_host(hostname):
    """Get max parallel connections for a host."""
    settings = get_host_settings(hostname)
    return max(1, int(settings.get("max_connections", 4)))


def get_speed_limit_for_host(hostname):
    """Get speed limit in bytes/sec for a host. 0 means no limit."""
    settings = get_host_settings(hostname)
    kbps = int(settings.get("speed_limit_kbps", 0))
    if kbps <= 0:
        return 0
    return kbps * 1024
