import os
import json
import urllib.request
import socket
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY_FILE = os.path.join(BASE_DIR, "proxy_settings.json")


def _load_proxies():
    if not os.path.isfile(PROXY_FILE):
        return {}
    try:
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_proxies(data):
    with open(PROXY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_proxy_for_url(url):
    """Get proxy dict for requests library."""
    settings = _load_proxies()
    hostname = urlparse(url).hostname or ""

    for pattern, proxy_url in settings.items():
        if _host_matches(hostname, pattern):
            return {"http": proxy_url, "https": proxy_url}
    return None


def get_proxy_url_for_url(url):
    """Get raw proxy URL string for yt-dlp --proxy flag."""
    settings = _load_proxies()
    hostname = urlparse(url).hostname or ""

    for pattern, proxy_url in settings.items():
        if _host_matches(hostname, pattern):
            return proxy_url
    return None


def _host_matches(hostname, pattern):
    """Check if hostname matches a pattern (domain or wildcard)."""
    hostname = hostname.lower()
    pattern = pattern.lower()
    if pattern == "*" or pattern == hostname:
        return True
    if pattern.startswith("*.") and hostname.endswith(pattern[1:]) or hostname == pattern[2:]:
        return True
    return hostname == pattern


def get_system_proxy():
    """Detect system proxy settings (Windows)."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if enabled:
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if server:
                scheme = "https" if "https" in server.lower() else "http"
                return f"{scheme}://{server.replace('=', '://')}"
    except Exception:
        pass

    # Check environment variables
    for var in ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"]:
        val = os.environ.get(var)
        if val:
            return val
    return None


def set_proxy(pattern, proxy_url):
    settings = _load_proxies()
    if proxy_url:
        settings[pattern] = proxy_url
    else:
        settings.pop(pattern, None)
    _save_proxies(settings)


def remove_proxy(pattern):
    settings = _load_proxies()
    settings.pop(pattern, None)
    _save_proxies(settings)


def get_all_proxies():
    return _load_proxies()
