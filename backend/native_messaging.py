import os
import sys
import json
import struct
import threading
import traceback
from io import BytesIO

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NativeMessagingHost:
    """Implements Chrome native messaging protocol (stdio-based).

    Browser extensions communicate via 4-byte length prefix + JSON message
    over stdin/stdout. The host reads messages from stdin and writes responses
    to stdout.

    Reference: https://developer.chrome.com/docs/apps/nativeMessaging/
    """

    def __init__(self):
        self.handlers = {}
        self.running = False
        self._thread = None
        self._register_default_handlers()

    def _register_default_handlers(self):
        self.handlers["ping"] = self._handle_ping
        self.handlers["add_download"] = self._handle_add_download
        self.handlers["get_queue"] = self._handle_get_queue
        self.handlers["get_status"] = self._handle_get_status
        self.handlers["video_detected"] = self._handle_video_detected

    def register_handler(self, msg_type, handler):
        self.handlers[msg_type] = handler

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _read_loop(self):
        stdin = sys.stdin.buffer if hasattr(sys.stdin, 'buffer') else sys.stdin
        while self.running:
            try:
                raw_length = stdin.read(4)
                if not raw_length or len(raw_length) < 4:
                    break
                msg_length = struct.unpack('=I', raw_length)[0]
                if msg_length > 1024 * 1024:
                    continue
                raw_msg = stdin.read(msg_length)
                if not raw_msg:
                    break
                msg = json.loads(raw_msg.decode("utf-8"))
                self._process_message(msg)
            except Exception:
                traceback.print_exc()
                break

    def _process_message(self, msg):
        msg_type = msg.get("type", "")
        handler = self.handlers.get(msg_type)
        if not handler:
            response = {"type": "error", "error": f"Unknown message type: {msg_type}"}
        else:
            try:
                response = handler(msg)
            except Exception as e:
                response = {"type": "error", "error": str(e)}
        self._send_response(response)

    def _send_response(self, data):
        try:
            encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
            length_bytes = struct.pack('=I', len(encoded))
            sys.stdout.buffer.write(length_bytes)
            sys.stdout.buffer.write(encoded)
            sys.stdout.buffer.flush()
        except Exception:
            pass

    def _handle_ping(self, msg):
        return {"type": "pong", "timestamp": __import__("time").time()}

    def _handle_add_download(self, msg):
        url = msg.get("url", "")
        options = msg.get("options", {})
        import requests
        try:
            r = requests.post(
                "http://localhost:8000/api/download",
                json={
                    "url": options.get("page_url") or url,
                    "category": options.get("category", "video"),
                    "format_id": options.get("format", "best"),
                    "is_browser_url": True,
                    "custom_video_url": url,
                    "custom_title": options.get("title", ""),
                    "page_url": options.get("page_url", ""),
                    "cookies": options.get("cookies", ""),
                    "cookies_netscape": options.get("cookies_netscape", ""),
                },
                timeout=5,
            )
            return r.json()
        except Exception as e:
            return {"type": "error", "error": str(e)}

    def _handle_get_queue(self, msg):
        import requests
        try:
            r = requests.get("http://localhost:8000/api/queue", timeout=5)
            return r.json()
        except Exception as e:
            return {"type": "error", "error": str(e)}

    def _handle_get_status(self, msg):
        import requests
        try:
            r = requests.get("http://localhost:8000/api/extension/status", timeout=5)
            return r.json()
        except Exception as e:
            return {"type": "error", "error": str(e)}

    def _handle_video_detected(self, msg):
        data = msg.get("data", {})
        import requests
        try:
            r = requests.post(
                "http://localhost:8000/api/extension/detect",
                json={
                    "page_url": data.get("pageUrl", ""),
                    "video_url": data.get("videoUrl", ""),
                    "title": data.get("title", ""),
                    "video_type": data.get("videoType", ""),
                    "hostname": data.get("hostname", ""),
                    "drm_detected": data.get("drm", False),
                },
                timeout=5,
            )
            return r.json()
        except Exception as e:
            return {"type": "error", "error": str(e)}


def generate_manifest(browser, extension_id, host_name="com.aniframe.native_host"):
    """Generate native messaging host manifest for browser."""
    base_dir = BASE_DIR
    if sys.platform == "win32":
        host_path = os.path.join(base_dir, "native_host.bat")
    else:
        host_path = os.path.join(base_dir, "native_host.sh")

    manifest = {
        "name": host_name,
        "description": "AniFrame Native Messaging Host",
        "path": host_path,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }

    if browser == "firefox":
        manifest["allowed_extensions"] = [extension_id]

    return manifest


def get_manifest_path(browser):
    """Get the path where the native messaging manifest should be installed."""
    if sys.platform == "win32":
        if browser == "chrome" or browser == "edge":
            return os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Google", "Chrome", "User Data", "NativeMessagingHosts",
                "com.aniframe.native_host.json"
            )
        elif browser == "firefox":
            return os.path.join(
                os.environ.get("APPDATA", ""),
                "Mozilla", "Firefox", "Profiles", "native-messaging-hosts",
                "com.aniframe.native_host.json"
            )
    elif sys.platform == "darwin":
        return os.path.join(
            os.path.expanduser("~"),
            "Library", "Application Support",
            "Google" if browser != "firefox" else "Mozilla",
            "Chrome" if browser != "firefox" else "Firefox",
            "NativeMessagingHosts",
            "com.aniframe.native_host.json"
        )
    else:
        return os.path.join(
            os.path.expanduser("~"),
            ".config" if browser == "chrome" else ".mozilla",
            "google-chrome" if browser == "chrome" else "firefox",
            "NativeMessagingHosts",
            "com.aniframe.native_host.json"
        )
