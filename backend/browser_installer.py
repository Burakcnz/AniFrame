"""PC'deki tarayicilari bulma ve AniFrame extension kurulumu.

Hibrit kurulum sistemi:
- Chrome <137: --load-extension ile session-only kurulum
- Chrome 137+ (calismiyorsa): HMAC forgery ile persistent kurulum
- Chrome 137+ (calisiyorsa): CDP Extensions.loadUnpacked ile session-only
- Edge/Brave/Vivaldi/Opera/Chromium: --load-extension ile kurulum
"""

import os
import sys
import subprocess
import logging
import json
import hashlib
import hmac as hmac_module
import base64
import struct
import datetime
import re
import time
from pathlib import Path

logger = logging.getLogger("aniframe.browser_installer")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION_DIR = os.path.join(BASE_DIR, "extension")
EXTENSION_ID = "pjbgdkaoclbeelkffcdcnlfbdgcmaneo"

IS_WIN = sys.platform.startswith("win")

# HMAC seed - Chrome resources.pak'dan cikarilmis, 2020'den beri sabit
# Kaynak: CANS 2020 paper + Chrome kaynak kodu (resource ID 146)
_HMAC_SEED = bytes([
    0xe7, 0x48, 0xf3, 0x36, 0xd8, 0x5e, 0xa5, 0xf9,
    0xdc, 0xdf, 0x25, 0xd8, 0xf3, 0x47, 0xa6, 0x5b,
    0x4c, 0xdf, 0x66, 0x76, 0x00, 0xf0, 0x2d, 0xf6,
    0x72, 0x4a, 0x2a, 0xf1, 0x8a, 0x21, 0x2d, 0x26,
    0xb7, 0x88, 0xa2, 0x50, 0x86, 0x91, 0x0c, 0xf3,
    0xa9, 0x03, 0x13, 0x69, 0x68, 0x71, 0xf3, 0xdc,
    0x05, 0x82, 0x37, 0x30, 0xc9, 0x1d, 0xf8, 0xba,
    0x5c, 0x4f, 0xd9, 0xc8, 0x84, 0xb5, 0x05, 0xa8,
])

BROWSER_DEFS = {
    "chrome": {
        "name": "Google Chrome",
        "exe_names": ["chrome.exe"],
        "paths": [
            r"C:\Program Files\Google\Chrome\Application",
            r"C:\Program Files (x86)\Google\Chrome\Application",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application"),
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application"),
        ],
        "reg_uninstall": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Google Chrome",
        "reg_version_key": r"SOFTWARE\Google\Chrome\BLBeacon",
        "reg_version_val": "version",
        "user_data_name": "Google\\Chrome",
        "supports_load_extension": True,
        "min_load_extension_version": None,
        "max_load_extension_version": (137, 0, 0, 0),
    },
    "edge": {
        "name": "Microsoft Edge",
        "exe_names": ["msedge.exe"],
        "paths": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application",
            r"C:\Program Files\Microsoft\Edge\Application",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application"),
        ],
        "reg_uninstall": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft Edge",
        "reg_version_key": r"SOFTWARE\Microsoft\Edge\BLBeacon",
        "reg_version_val": "version",
        "user_data_name": "Microsoft\\Edge",
        "supports_load_extension": True,
        "min_load_extension_version": None,
        "max_load_extension_version": None,
    },
    "brave": {
        "name": "Brave Browser",
        "exe_names": ["brave.exe"],
        "paths": [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application",
            os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application"),
        ],
        "reg_version_key": r"SOFTWARE\BraveSoftware\Brave-Browser",
        "reg_version_val": "Version",
        "user_data_name": "BraveSoftware\\Brave-Browser",
        "supports_load_extension": True,
        "min_load_extension_version": None,
        "max_load_extension_version": None,
    },
    "opera": {
        "name": "Opera Browser",
        "exe_names": ["opera.exe", "launcher.exe"],
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Opera"),
            r"C:\Program Files\Opera",
        ],
        "reg_version_key": r"SOFTWARE\Clients\StartMenuInternet\OperaStable\Capabilities",
        "reg_version_val": "ApplicationName",
        "user_data_name": "Opera Software\\Opera Stable",
        "supports_load_extension": True,
        "min_load_extension_version": None,
        "max_load_extension_version": None,
    },
    "vivaldi": {
        "name": "Vivaldi",
        "exe_names": ["vivaldi.exe"],
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Vivaldi\Application"),
            r"C:\Program Files\Vivaldi\Application",
        ],
        "reg_version_key": r"SOFTWARE\Vivaldi",
        "reg_version_val": "Version",
        "user_data_name": "Vivaldi",
        "supports_load_extension": True,
        "min_load_extension_version": None,
        "max_load_extension_version": None,
    },
    "chromium": {
        "name": "Chromium",
        "exe_names": ["chrome.exe"],
        "paths": [
            r"C:\Program Files\Chromium\Application",
            r"C:\Program Files (x86)\Chromium\Application",
        ],
        "user_data_name": "Chromium",
        "supports_load_extension": True,
        "min_load_extension_version": None,
        "max_load_extension_version": None,
    },
}


# ---------------------------------------------------------------------------
# Temel Yardimci Fonksiyonlar
# ---------------------------------------------------------------------------

def _find_exe_in_dir(directory, exe_names):
    if not os.path.isdir(directory):
        return None
    for name in exe_names:
        for root, dirs, files in os.walk(directory):
            if name in files:
                return os.path.join(root, name)
    return None


def _read_registry_string(key_path, value_name):
    if not IS_WIN:
        return None
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    val, _ = winreg.QueryValueEx(key, value_name)
                    return val
            except OSError:
                continue
    except Exception:
        pass
    return None


def _get_browser_version_str(browser_def):
    version = None
    if IS_WIN:
        vk = browser_def.get("reg_version_key")
        vv = browser_def.get("reg_version_val", "Version")
        if vk:
            version = _read_registry_string(vk, vv)
    if not version:
        exe_path = _find_exe_in_dir(browser_def["paths"][0], browser_def.get("exe_names", [])) if browser_def.get("paths") else None
        if exe_path:
            try:
                import win32api
                info = win32api.GetFileVersionInfo(exe_path, "\\")
                ms = info.get("FileVersionMS", 0)
                ls = info.get("FileVersionLS", 0)
                version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
            except Exception:
                pass
    return version or "?"


def _parse_version(version_str):
    """Version string'ini tuple'a cevir: '137.0.6543.23' -> (137, 0, 6543, 23)"""
    try:
        parts = re.sub(r'[^\d.]', '', version_str).split('.')
        return tuple(int(p) for p in parts[:4]) if len(parts) >= 4 else tuple(int(p) for p in parts) + (0,) * (4 - len(parts))
    except (ValueError, AttributeError):
        return (0, 0, 0, 0)


def _get_user_data_dir(browser_key):
    local_app = os.path.expandvars("%LOCALAPPDATA%")
    ud_name = BROWSER_DEFS.get(browser_key, {}).get("user_data_name", browser_key)
    return os.path.join(local_app, ud_name, "User Data")


def _get_exe_path(browser_key):
    """Tarayici executable yolunu bul."""
    bdef = BROWSER_DEFS.get(browser_key)
    if not bdef:
        return None
    for p in bdef.get("paths", []):
        exe = _find_exe_in_dir(p, bdef.get("exe_names", []))
        if exe:
            return exe
    return None


def _is_browser_running(browser_key):
    """Tarayicinin calisip calismadigini kontrol et."""
    bdef = BROWSER_DEFS.get(browser_key)
    if not bdef:
        return False
    exe_names = bdef.get("exe_names", [])
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
        )
        for line in result.stdout.strip().split('\n'):
            for exe_name in exe_names:
                if exe_name.lower() in line.lower():
                    return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Extension ID Hesaplama
# ---------------------------------------------------------------------------

def get_extension_id_from_manifest():
    """manifest.json'daki key field'indan extension ID hesapla."""
    manifest_path = os.path.join(EXTENSION_DIR, "manifest.json")
    if not os.path.isfile(manifest_path):
        return EXTENSION_ID
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        key_b64 = manifest.get("key")
        if key_b64:
            key_der = base64.b64decode(key_b64)
            sha = hashlib.sha256(key_der).hexdigest()[:32]
            return ''.join(chr(int(c, 16) + ord('a')) for c in sha)
    except Exception:
        pass
    return EXTENSION_ID


def get_extension_id_from_path(path):
    """Filesystem path'inden extension ID hesapla (Windows UTF-16-LE)."""
    if len(path) >= 2 and path[1] == ':' and path[0].islower():
        path = path[0].upper() + path[1:]
    path_bytes = path.encode('utf-16-le')
    sha = hashlib.sha256(path_bytes).digest()[:16]
    hex_str = sha.hex()
    return ''.join(chr(int(c, 16) + ord('a')) for c in hex_str)


# ---------------------------------------------------------------------------
# Chrome Surum Kontrolu
# ---------------------------------------------------------------------------

def detect_chrome_version(browser_key):
    """Tarayici surumunu tuple olarak dondur: (major, minor, build, patch)"""
    bdef = BROWSER_DEFS.get(browser_key)
    if not bdef:
        return (0, 0, 0, 0)
    version_str = _get_browser_version_str(bdef)
    return _parse_version(version_str)


def is_chrome_137_plus(browser_key):
    """Chrome 137+ ise True dondur."""
    if browser_key != "chrome":
        return False
    version = detect_chrome_version(browser_key)
    return version >= (137, 0, 0, 0)


def supports_load_extension(browser_key):
    """Tarayicinin --load-extension destekleyip desteklemedigini kontrol et."""
    bdef = BROWSER_DEFS.get(browser_key)
    if not bdef:
        return False
    if not bdef.get("supports_load_extension", False):
        return False
    max_ver = bdef.get("max_load_extension_version")
    if max_ver is None:
        return True
    version = detect_chrome_version(browser_key)
    return version < max_ver


# ---------------------------------------------------------------------------
# HMAC Hesaplama (Secure Preferences)
# ---------------------------------------------------------------------------

def _get_windows_sid():
    """Windows Security Identifier (SID) al."""
    if not IS_WIN:
        return ""
    try:
        import win32security
        import win32api
        handle = win32api.OpenProcess(0x0020000, False, win32api.GetCurrentProcessId())  # PROCESS_QUERY_LIMITED_INFORMATION
        token = win32security.OpenProcessToken(handle, win32security.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        sid_str = win32security.ConvertSidToStringSid(sid)
        win32api.CloseHandle(handle)
        parts = sid_str.split('-')
        return '-'.join(parts[:-1])
    except Exception:
        return ""


def _chrome_hmac(data_bytes):
    """Chrome HMAC-SHA256 hesapla."""
    return hmac_module.new(_HMAC_SEED, data_bytes, hashlib.sha256).hexdigest().upper()


def _calculate_protection_mac(sid, path, value):
    """Tek bir node icin HMAC hesapla."""
    serialized = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    serialized = serialized.replace('<', '\\u003C').replace('\\u2122', '\u2122')
    message = sid + path + serialized
    return _chrome_hmac(message.encode('utf-8'))


def _calculate_super_mac(sid, macs_dict):
    """Root super_mac hesapla."""
    macs_json = json.dumps(macs_dict, separators=(',', ':'))
    message = sid + macs_json
    return _chrome_hmac(message.encode('utf-8'))


# ---------------------------------------------------------------------------
# Profil Tarama
# ---------------------------------------------------------------------------

def find_all_profiles(browser_key):
    """Tum tarayici profillerini bul."""
    profiles = []
    user_data_dir = _get_user_data_dir(browser_key)

    if not os.path.isdir(user_data_dir):
        return profiles

    # Local State dosyasindan profil isimlerini oku
    local_state_path = os.path.join(user_data_dir, "Local State")
    profile_names = {}
    if os.path.isfile(local_state_path):
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                local_state = json.load(f)
            info_cache = local_state.get("profile", {}).get("info_cache", {})
            for dir_name, info in info_cache.items():
                profile_names[dir_name] = info.get("name", dir_name)
        except Exception:
            pass

    # Profil klasorlerini tara
    for item in os.listdir(user_data_dir):
        item_path = os.path.join(user_data_dir, item)
        if not os.path.isdir(item_path):
            continue
        if item in ("Default",) or re.match(r'^Profile \d+$', item):
            prefs_file = os.path.join(item_path, "Preferences")
            has_prefs = os.path.isfile(prefs_file)
            display_name = profile_names.get(item, item)
            profiles.append({
                "dir_name": item,
                "display_name": display_name,
                "path": item_path,
                "has_preferences": has_prefs,
            })

    return profiles


def check_extension_in_profile(browser_key, profile_dir_name):
    """Belirli bir profilde extension'in yuklu olup olmadigini kontrol et."""
    user_data_dir = _get_user_data_dir(browser_key)
    prefs_file = os.path.join(user_data_dir, profile_dir_name, "Preferences")

    if os.path.isfile(prefs_file):
        try:
            with open(prefs_file, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            settings = prefs.get("extensions", {}).get("settings", {})
            for ext_id, info in settings.items():
                if isinstance(info, dict):
                    path_val = info.get("path", "")
                    name_val = info.get("manifest", {}).get("name", "")
                    if "AniFrame" in name_val or EXTENSION_DIR.replace("\\", "/") in path_val.replace("\\", "/"):
                        return True
        except Exception:
            pass

    # Secure Preferences kontrolu
    secure_prefs_file = os.path.join(user_data_dir, profile_dir_name, "Secure Preferences")
    if os.path.isfile(secure_prefs_file):
        try:
            with open(secure_prefs_file, "r", encoding="utf-8") as f:
                content = f.read()
            if EXTENSION_DIR.replace("\\", "\\\\") in content or EXTENSION_DIR.replace("\\", "/") in content:
                return True
        except Exception:
            pass

    return False


# ---------------------------------------------------------------------------
# HMAC Forgery ile Persistent Kurulum (Chrome 137+ icin)
# ---------------------------------------------------------------------------

def install_via_hmac_forgery(browser_key):
    """Chrome calismazken HMAC forgery ile persistent kurulum.

    Returns: dict with success/error + profiles
    """
    if _is_browser_running(browser_key):
        return {
            "success": False,
            "error": "Chrome calisiyor once kapatmaniz gerekiyor",
            "requires_close": True,
        }

    user_data_dir = _get_user_data_dir(browser_key)
    if not os.path.isdir(user_data_dir):
        return {"success": False, "error": f"Kullanici veri dizini bulunamadi: {user_data_dir}"}

    profiles = find_all_profiles(browser_key)
    if not profiles:
        return {"success": False, "error": "Profil bulunamadi"}

    ext_path = EXTENSION_DIR.replace("\\", "\\\\")
    sid = _get_windows_sid()
    now_ts = str(int((datetime.datetime.now() - datetime.datetime(1601, 1, 1)).total_seconds() * 1_000_000))

    installed_profiles = []
    errors = []

    for profile in profiles:
        profile_dir = profile["path"]
        secure_prefs_path = os.path.join(profile_dir, "Secure Preferences")
        prefs_path = os.path.join(profile_dir, "Preferences")

        # Secure Preferences oku veya olustur
        secure_data = {}
        if os.path.isfile(secure_prefs_path):
            try:
                with open(secure_prefs_path, "r", encoding="utf-8") as f:
                    secure_data = json.load(f)
            except Exception:
                secure_data = {}

        # Preferences oku (developer mode icin)
        prefs_data = {}
        if os.path.isfile(prefs_path):
            try:
                with open(prefs_path, "r", encoding="utf-8") as f:
                    prefs_data = json.load(f)
            except Exception:
                prefs_data = {}

        # Extension ayarlarini ekle
        ext_settings = secure_data.setdefault("extensions", {}).setdefault("settings", {})
        ext_settings[EXTENSION_ID] = {
            "active_permissions": {
                "api": ["activeTab", "scripting", "storage", "tabs", "contextMenus", "notifications", "webRequest", "alarms"],
                "explicit_host": ["<all_urls>"],
                "manifest_permissions": [],
                "scriptable_host": [],
            },
            "commands": {},
            "content_settings": [],
            "creation_flags": 38,
            "first_install_time": now_ts,
            "from_webstore": False,
            "granted_permissions": {
                "api": ["activeTab", "scripting", "storage", "tabs", "contextMenus", "notifications", "webRequest", "alarms"],
                "explicit_host": ["<all_urls>"],
                "manifest_permissions": [],
                "scriptable_host": [],
            },
            "incognito": True,
            "incognito_content_settings": [],
            "incognito_preferences": {},
            "last_update_time": now_ts,
            "location": 4,
            "newAllowFileAccess": True,
            "path": ext_path,
            "preferences": {},
            "regular_only_preferences": {},
            "service_worker_registration_info": {"version": "2.0"},
            "state": 1,
            "was_installed_by_default": False,
            "was_installed_by_oem": False,
            "withholding_permissions": False,
        }

        # Developer mode ac
        secure_data.setdefault("extensions", {}).setdefault("ui", {})["developer_mode"] = True
        prefs_data.setdefault("extensions", {}).setdefault("ui", {})["developer_mode"] = True

        # HMAC hesapla
        protection = secure_data.setdefault("protection", {})
        macs = protection.setdefault("macs", {})

        # Extension icin HMAC
        ext_macs = macs.setdefault("extensions", {}).setdefault("settings", {})
        ext_mac = _calculate_protection_mac(
            sid,
            "extensions.settings." + EXTENSION_ID,
            ext_settings[EXTENSION_ID],
        )
        ext_macs[EXTENSION_ID] = ext_mac

        # Developer mode icin HMAC
        ui_mac = _calculate_protection_mac(
            sid,
            "extensions.ui.developer_mode",
            True,
        )
        macs.setdefault("extensions", {}).setdefault("ui", {})["developer_mode"] = ui_mac

        # Super MAC hesapla
        protection["super_mac"] = _calculate_super_mac(sid, macs)

        # Dosyalari yaz
        try:
            with open(secure_prefs_path, "w", encoding="utf-8") as f:
                json.dump(secure_data, f, separators=(',', ':'))
        except Exception as e:
            errors.append(f"{profile['display_name']}: Secure Preferences yazma hatasi: {e}")
            continue

        try:
            with open(prefs_path, "w", encoding="utf-8") as f:
                json.dump(prefs_data, f, separators=(',', ':'))
        except Exception as e:
            errors.append(f"{profile['display_name']}: Preferences yazma hatasi: {e}")
            continue

        installed_profiles.append(profile["display_name"])

    if installed_profiles:
        return {
            "success": True,
            "method": "hmac_forgery",
            "persistent": True,
            "message": f"Extension {len(installed_profiles)} profile kuruldu",
            "profiles": installed_profiles,
            "errors": errors,
            "instructions": [
                "Extension kalici olarak kuruldu",
                "Chrome'i baslatinca extension otomatik yuklenecek",
            ],
        }
    else:
        return {
            "success": False,
            "error": "Hiçbir profile kurulamadi",
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# CDP ile Session-Only Kurulum (Chrome 137+ icin)
# ---------------------------------------------------------------------------

def install_via_cdp(browser_key):
    """Chrome calisirken CDP Extensions.loadUnpacked ile kurulum.

    Returns: dict with success/error
    """
    exe_path = _get_exe_path(browser_key)
    if not exe_path:
        return {"success": False, "error": "Tarayici bulunamadi"}

    ext_path_abs = os.path.abspath(EXTENSION_DIR)

    # Chrome'u CDP modunda baslat (eager launch - mevcut instance'a baglan veya yeni baslat)
    try:
        import tempfile
        import threading
        import socket

        # Bos port bul
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()

        # Remote debugging pipe yerine port kullan
        si = subprocess.STARTUPINFO() if IS_WIN else None
        if IS_WIN:
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 1

        cmd = [
            exe_path,
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=si,
        )

        # CDP'nin baslamasi icin bekle
        time.sleep(2)

        # WebSocket ile CDP'ye baglan
        import urllib.request
        import urllib.error

        ws_url = None
        for attempt in range(10):
            try:
                resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
                data = json.loads(resp.read())
                ws_url = data.get("webSocketDebuggerUrl")
                if ws_url:
                    break
            except (urllib.error.URLError, ConnectionRefusedError):
                time.sleep(1)

        if not ws_url:
            proc.terminate()
            return {"success": False, "error": "CDP baglantisi kurulamadi"}

        # Basit WebSocket client ile loadUnpacked gonder
        import websocket
        ws = websocket.create_connection(ws_url, timeout=10)

        load_cmd = json.dumps({
            "id": 1,
            "method": "Extensions.loadUnpacked",
            "params": {
                "path": ext_path_abs,
                "enableInIncognito": False,
            },
        })
        ws.send(load_cmd)
        result = json.loads(ws.recv())
        ws.close()

        if "result" in result:
            ext_id = result["result"].get("id", "")
            return {
                "success": True,
                "method": "cdp",
                "persistent": False,
                "message": "Extension bu oturum icin yuklendi",
                "extension_id": ext_id,
                "pid": proc.pid,
                "instructions": [
                    "Extension basariyla yuklendi",
                    "Bu kurulum kalici degil, Chrome kapatilinca silinir",
                    "Kalici kurulum icin Chrome'u kapatip tekrar kurun",
                ],
            }
        else:
            error_msg = result.get("error", {}).get("message", "Bilinmeyen hata")
            proc.terminate()
            return {"success": False, "error": f"CDP hatasi: {error_msg}"}

    except ImportError:
        return {"success": False, "error": "websocket modulu bulunamadi: pip install websocket-client"}
    except Exception as e:
        logger.error(f"CDP kurulum hatasi: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# --load-extension ile Kurulum (Eski tarayicilar icin)
# ---------------------------------------------------------------------------

def install_via_load_extension(browser_key):
    """--load-extension ile session-only kurulum.

    Returns: dict with success/error
    """
    exe_path = _get_exe_path(browser_key)
    if not exe_path:
        return {"success": False, "error": "Tarayici bulunamadi"}

    if not os.path.isdir(EXTENSION_DIR):
        return {"success": False, "error": "Extension dizini bulunamadi"}

    if not os.path.isfile(os.path.join(EXTENSION_DIR, "manifest.json")):
        return {"success": False, "error": "manifest.json bulunamadi"}

    ext_path_abs = os.path.abspath(EXTENSION_DIR)
    ud_path = _get_user_data_dir(browser_key)
    bdef = BROWSER_DEFS[browser_key]

    try:
        si = None
        if IS_WIN:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 1

        cmd = [
            exe_path,
            f"--load-extension={ext_path_abs}",
            f"--disable-extensions-except={ext_path_abs}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            f"--user-data-dir={ud_path}",
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=si,
        )

        logger.info(f"Tarayici baslatildi: {bdef['name']} (PID: {proc.pid})")

        return {
            "success": True,
            "method": "load_extension",
            "persistent": False,
            "browser": bdef["name"],
            "pid": proc.pid,
            "message": f"{bdef['name']} extension ile baslatildi",
            "instructions": [
                "Tarayici extension ile birlikte aciliyor",
                "Extension bu oturum icin yuklendi",
            ],
        }

    except Exception as e:
        logger.error(f"Tarayici baslatma hatasi: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Ana Kurulum Fonksiyonu (Hibrit Mantik)
# ---------------------------------------------------------------------------

def install_extension(browser_key, launch_browser=True):
    """Extension'i tarayiciya kurar - hibrit yontem.

    Tarayici turu ve surumune gore en uygun yontemi secer:
    - Chrome <137: --load-extension
    - Chrome 137+ (calismiyorsa): HMAC forgery
    - Chrome 137+ (calisiyorsa): CDP
    - Diger tarayicilar: --load-extension

    Args:
        browser_key: Tarayici anahtari (chrome, edge, brave, vb.)
        launch_browser: True ise tarayiciyi baslat, False ise sadece durumu dondur

    Returns: dict with success/error + method + instructions
    """
    bdef = BROWSER_DEFS.get(browser_key)
    if not bdef:
        return {"success": False, "error": f"Bilinmeyen tarayici: {browser_key}"}

    exe_path = _get_exe_path(browser_key)
    if not exe_path:
        return {"success": False, "error": f"{bdef['name']} bulunamadi"}

    if not os.path.isdir(EXTENSION_DIR):
        return {"success": False, "error": "Extension dizini bulunamadi"}

    if not os.path.isfile(os.path.join(EXTENSION_DIR, "manifest.json")):
        return {"success": False, "error": "manifest.json bulunamadi"}

    if not launch_browser:
        version = detect_chrome_version(browser_key)
        version_str = '.'.join(str(v) for v in version)
        return {
            "success": True,
            "browser": bdef["name"],
            "version": version_str,
            "chrome_137_plus": is_chrome_137_plus(browser_key),
            "message": "Kuruluma hazir",
        }

    # Hibrit karar mantigi
    chrome_137 = is_chrome_137_plus(browser_key)

    if chrome_137:
        # Chrome 137+ - yontem sec
        running = _is_browser_running(browser_key)

        if running:
            # Chrome calisiyor - CDP dene
            logger.info(f"Chrome 137+ calisiyor, CDP deneniyor")
            result = install_via_cdp(browser_key)
            if result["success"]:
                return result
            # CDP basarisiz ise manuel yönlendirme
            return {
                "success": False,
                "error": "Chrome calisiyor ve CDP baglantisi kurulamadi",
                "requires_close": True,
                "fallback_instructions": [
                    "Chrome'i kapatip tekrar deneyin",
                    "Veya chrome://extensions adresinde Developer Mode acip",
                    f"'Load unpacked' ile {EXTENSION_DIR} klasorunu secin",
                ],
            }
        else:
            # Chrome calismiyor - HMAC forgery dene
            logger.info(f"Chrome 137+ calismiyor, HMAC forgery deneniyor")
            result = install_via_hmac_forgery(browser_key)
            if result["success"]:
                return result
            # HMAC basarisiz ise load-extension dene (fallback)
            logger.info("HMAC forgery basarisiz, load-extension deneniyor")
            return install_via_load_extension(browser_key)
    else:
        # Chrome <137 veya diger tarayici - load-extension
        return install_via_load_extension(browser_key)


# ---------------------------------------------------------------------------
# Durum Fonksiyonlari
# ---------------------------------------------------------------------------

def check_extension_installed(browser_key):
    """Belirtilen tarayicida extension'in yuklu olup olmadigini kontrol et."""
    profiles = find_all_profiles(browser_key)
    if not profiles:
        return False

    for profile in profiles:
        if check_extension_in_profile(browser_key, profile["dir_name"]):
            return True
    return False


def get_browser_extension_status():
    """Tum tarayicilarda extension durumunu dondurur."""
    browsers = detect_installed_browsers()
    result = []

    for b in browsers:
        key = b["key"]
        version = detect_chrome_version(key)
        version_str = '.'.join(str(v) for v in version)
        chrome_137 = is_chrome_137_plus(key)
        load_ext = supports_load_extension(key)
        running = _is_browser_running(key)

        profiles = find_all_profiles(key)
        profile_status = []
        for p in profiles:
            ext_installed = check_extension_in_profile(key, p["dir_name"])
            profile_status.append({
                "name": p["display_name"],
                "dir_name": p["dir_name"],
                "extension_installed": ext_installed,
            })

        ext_installed_any = any(p["extension_installed"] for p in profile_status)

        b.update({
            "version": version_str,
            "chrome_137_plus": chrome_137,
            "supports_load_extension": load_ext,
            "browser_running": running,
            "profiles": profile_status,
            "profile_count": len(profile_status),
            "extension_installed": ext_installed_any,
            "installation_method": _get_recommended_method(key),
        })
        result.append(b)

    return result


def _get_recommended_method(browser_key):
    """Tarayici icin onerilen kurulum yontemini dondur."""
    if is_chrome_137_plus(browser_key):
        if _is_browser_running(browser_key):
            return "cdp"
        return "hmac_forgery"
    return "load_extension"


def detect_installed_browsers():
    """PC'deki tum yuklu Chromium tabanli tarayicilari bulur."""
    browsers = []

    for key, bdef in BROWSER_DEFS.items():
        exe_path = _get_exe_path(key)
        if exe_path and os.path.isfile(exe_path):
            version_str = _get_browser_version_str(bdef)
            browsers.append({
                "key": key,
                "name": bdef["name"],
                "path": exe_path,
                "version": version_str,
                "user_data_dir": _get_user_data_dir(key),
                "installed": True,
            })

    return browsers
