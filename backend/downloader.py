import os
import sys
import json
import re
import time
import asyncio
import threading
import subprocess
import platform
import shutil
from datetime import datetime

from backend.database import add_history, update_history, get_setting
from backend.browser_extractor import extract_from_page, HAS_PLAYWRIGHT

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NON_VIDEO_FORMAT_EXTS = {"svg", "png", "jpg", "jpeg", "gif", "webp", "ico", "bmp", "avif"}


def _ytdlp_js_args():
    """YouTube artik nsig/PO token cozumleri icin bir JS runtime istiyor.
    Deno varsayilan ve yt-dlp'nin varsayilani; yoksa Node'u dene."""
    if shutil.which("deno"):
        return []
    node = shutil.which("node")
    if not node and os.path.isfile(r"C:\nvm4w\nodejs\node.exe"):
        node = r"C:\nvm4w\nodejs\node.exe"
    if node:
        return ["--js-runtimes", f"node:{node}"]
    print("[UYARI] JS runtime (node/deno) bulunamadi - YouTube indirmeleri basarisiz olabilir", flush=True)
    return []


YTDLP_JS_ARGS = _ytdlp_js_args()


def _has_real_video_formats(data):
    """Detect yt-dlp's generic extractor grabbing page images (favicons/og:image)
    as fake 'formats' instead of an actual video, so we know to fall back to the
    browser extractor rather than trusting a bogus success."""
    formats = data.get("formats") or []
    if not formats:
        ext = (data.get("ext") or "").lower()
        return bool(data.get("url")) and ext not in NON_VIDEO_FORMAT_EXTS
    for f in formats:
        ext = (f.get("ext") or "").lower()
        vcodec = (f.get("vcodec") or "none").lower()
        acodec = (f.get("acodec") or "none").lower()
        if ext in NON_VIDEO_FORMAT_EXTS:
            continue
        if vcodec != "none" or acodec != "none":
            return True
    return False


class DownloadTask:
    def __init__(self, task_id, url, category, format_id, output_path, priority="normal", ws_callback=None):
        self.id = task_id
        self.url = url
        self.category = category
        self.format_id = format_id
        self.output_path = output_path
        self.ws_callback = ws_callback
        self.status = "pending"
        self.progress = 0
        self.speed = ""
        self.eta = ""
        self.error = None
        self.process = None
        self.is_browser_url = False
        self.custom_video_url = ""
        self.custom_title = ""
        self.thumb_url = ""
        self.page_url = ""
        self.cookies = ""
        self.cookies_netscape = ""
        self.cancel_event = threading.Event()
        self.priority = priority
        self.retry_count = 0
        self.max_retries = 3
        self.skip_ytdlp = False
        self.created_at = time.time()


class DownloadManager:
    def __init__(self):
        self._queue = []
        self._active = []
        self._lock = threading.Lock()
        self._speed_samples = []
        self._last_speed_check = time.time()
        self._event_loop = None

    def set_event_loop(self, loop):
        self._event_loop = loop

    def get_adaptive_max(self):
        if get_setting("max_concurrent") != "auto":
            return int(get_setting("max_concurrent"))

        if len(self._speed_samples) < 3:
            return 1

        avg_speed = sum(self._speed_samples) / len(self._speed_samples)
        if avg_speed > 5_000_000:
            return 3
        elif avg_speed > 2_000_000:
            return 2
        return 1

    def add_batch_speed_sample(self, bytes_per_sec):
        self._speed_samples.append(bytes_per_sec)
        if len(self._speed_samples) > 20:
            self._speed_samples.pop(0)

    def enqueue(self, url, category="uncategorized", format_id="best", output_path=None, is_browser_url=False, custom_video_url="", custom_title="", page_url="", cookies="", cookies_netscape="", priority="normal"):
        # Ayni URL zaten kuyruktaysa/inmekteyse tekrar ekleme (mukerrer indirme
        # hem YouTube rate-limit'ine takiliyor hem ayni dosyaya yazip cakisiyor)
        with self._lock:
            for t in self._active + self._queue:
                if t.url == url and t.status in ("pending", "downloading"):
                    print(f"[DL] Mukerrer atlandi: #{t.id} zaten kuyrukta ({url[:80]})", flush=True)
                    return t.id

        if output_path is None:
            output_path = get_setting("download_path", os.path.join(BASE_DIR, "downloads"))

        os.makedirs(output_path, exist_ok=True)

        hid = add_history(url, title=custom_title or None, category=category)
        task = DownloadTask(
            task_id=hid,
            url=url,
            category=category,
            format_id=format_id,
            output_path=output_path,
            priority=priority,
        )
        task.is_browser_url = is_browser_url
        task.custom_video_url = custom_video_url
        task.custom_title = custom_title
        task.page_url = page_url
        task.cookies = cookies
        task.cookies_netscape = cookies_netscape

        # Per-host settings'ten max_retries al
        try:
            from backend.host_settings import get_host_settings
            hostname = ""
            try:
                from urllib.parse import urlparse
                hostname = urlparse(url).hostname or ""
            except Exception:
                pass
            hs = get_host_settings(hostname)
            if hs.get("skip_ytdlp"):
                task.format_id = "native"
            task.max_retries = int(hs.get("retry_count", 3))
        except Exception:
            pass

        with self._lock:
            self._queue.append(task)
        self._process_queue()
        return hid

    def enqueue_batch(self, items):
        ids = []
        for item in items:
            hid = self.enqueue(
                url=item.get("url"),
                category=item.get("category", "uncategorized"),
                format_id=item.get("format_id", "best"),
                output_path=item.get("output_path"),
                custom_title=item.get("title", ""),
            )
            ids.append(hid)
        return ids

    def analyze_url(self, url, referer="", cookies=""):
        cmd = [
            sys.executable, "-m", "yt_dlp", *YTDLP_JS_ARGS,
            "--dump-json",
            "--no-download",
            "--no-playlist",
            url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                err_msg = result.stderr.strip() or "Analysis failed"
                if "Sign in to confirm" in err_msg or "JavaScript runtime" in err_msg:
                    return {"error": "YouTube dogrulama hatasi: JS runtime (Node/Deno) veya bot korumasi sorunu. Node kurulumunu kontrol edin."}
                unsupported = "Unsupported URL" in err_msg or "not supported" in err_msg.lower()
                forbidden = any(kw in err_msg for kw in ["HTTP Error 403", "HTTP Error 404", "HTTP Error 401", "Forbidden", "Unable to download webpage", "HTTP Error 4"])
                if unsupported or forbidden:
                    return self._analyze_with_browser(url, referer, cookies)
                return {"error": err_msg}

            data = json.loads(result.stdout.strip().split("\n")[-1])

            if not _has_real_video_formats(data):
                return self._analyze_with_browser(url, referer, cookies)

            cmd_formats = [
                sys.executable, "-m", "yt_dlp", *YTDLP_JS_ARGS,
                "--list-formats",
                "--no-playlist",
                url,
            ]
            fmt_result = subprocess.run(cmd_formats, capture_output=True, text=True, timeout=30)

            formats = _parse_formats(fmt_result.stdout)
            if not formats:
                return self._analyze_with_browser(url, referer, cookies)

            formats.insert(0, {"id": "best", "label": "En İyi (Otomatik)", "detail": "Otomatik en iyi kalite"})
            playlist_info = self._check_playlist(url)

            return {
                "title": data.get("title", ""),
                "duration": data.get("duration", 0),
                "thumbnail": data.get("thumbnail", ""),
                "uploader": data.get("uploader", ""),
                "formats": formats,
                "playlist": playlist_info,
                "source": "yt_dlp",
            }
        except json.JSONDecodeError:
            print(f"[YTDL] analyze_url: JSON parse hatasi, tarayiciya dusuluyor: {url[:100]}", flush=True)
            return self._analyze_with_browser(url, referer, cookies)
        except subprocess.TimeoutExpired:
            print(f"[YTDL] analyze_url: zaman asimi, tarayiciya dusuluyor: {url[:100]}", flush=True)
            return self._analyze_with_browser(url, referer, cookies)
        except Exception as e:
            print(f"[YTDL] analyze_url: beklenmeyen hata ({e}), tarayiciya dusuluyor: {url[:100]}", flush=True)
            return self._analyze_with_browser(url, referer, cookies)

    def _browser_extract(self, task):
        """Tarayici ile gercek stream URL'sini bul.

        Embed barindiran siteler (rapidvid, vb.) dogru Referer olmadan bos sayfa
        dondurur. Bu yuzden once embed sayfasini ASIL sayfanin (page_url) Referer'i
        ile yukleriz; bulunamazsa asil sayfayi yukleyip iframe'leri tararyiz."""
        page_url = task.page_url or ""
        embed = task.custom_video_url if (task.is_browser_url and task.custom_video_url) else task.url
        cookies = task.cookies or ""
        embed_is_media = bool(embed) and embed.lower().split("?")[0].endswith(
            (".mp4", ".mkv", ".webm", ".m3u8", ".mpd", ".ts", ".flv", ".mov")
        )

        def _capture(res):
            # Cikarilan film adi/kapak gorselini goreve yaz; dosya adi ve
            # thumbnail bunlardan uretilecek (yoksa "video.mp4" kaliyordu).
            if res and not res.get("error"):
                if not (task.custom_title or "").strip() and res.get("title"):
                    task.custom_title = res["title"]
                if res.get("thumbnail"):
                    task.thumb_url = res["thumbnail"]
            return res

        # 1) Embed sayfasini asil sayfa referer'i ile dene (asıl duzeltme)
        if embed and not embed_is_media and embed != page_url:
            res = self.analyze_url(embed, referer=page_url, cookies=cookies)
            if not res.get("error") and res.get("video_url"):
                return _capture(res)

        # 2) Asil sayfayi yukleyip iframe'leri tara
        if page_url:
            return _capture(self.analyze_url(page_url, referer="", cookies=cookies))

        # 3) page_url yoksa embed'i kendi origin referer'i ile
        return _capture(self.analyze_url(embed, referer=page_url, cookies=cookies))

    def _analyze_with_browser(self, url, referer="", cookies=""):
        if not HAS_PLAYWRIGHT:
            return {"error": "Bu site yt-dlp tarafindan desteklenmiyor. Tarayici tabanli indirme icin Playwright kurulumu gerekli: playwright install chromium"}

        result_holder = [None]
        exception_holder = [None]

        def _run_in_thread():
            try:
                result_holder[0] = extract_from_page(url, timeout_ms=25000, referer=referer, cookies=cookies)
            except Exception as e:
                exception_holder[0] = e

        # extract_from_page embed'e ozyineleme yapabilir (asil sayfa + embed = iki
        # sayfa yuklemesi), bu yuzden zaman asimini buna gore genis tut.
        t = threading.Thread(target=_run_in_thread, daemon=True)
        t.start()
        t.join(timeout=80)

        if t.is_alive():
            return {"error": "Tarayici analizi zaman asimina ugradi (80sn). Site cok yavas olabilir."}

        if exception_holder[0]:
            return {"error": f"Tarayici analizi basarisiz: {str(exception_holder[0])[:200]}"}

        result = result_holder[0]
        if not result:
            return {"error": "Tarayici analizi sonuc donmedi."}

        if result.get("error"):
            # extract_from_page already builds a diagnostic message (page load
            # status, iframes/video tags scanned, media requests seen, DRM
            # keyword hits) when nothing was found - see _build_not_found_message
            return {"error": result["error"], "diagnostics": result.get("diagnostics")}

        if not result.get("formats") and not result.get("video_url"):
            return {"error": "Video bulunamadi (teshis bilgisi yok)."}

        if not result.get("formats"):
            result["formats"] = [{"id": "best", "label": "En Iyi (Tarayici)", "detail": result.get("video_url", url)}]

        result["source"] = "browser"
        return result

    def _check_playlist(self, url):
        cmd = [
            sys.executable, "-m", "yt_dlp", *YTDLP_JS_ARGS,
            "--dump-json",
            "--flat-playlist",
            "--playlist-end", "1",
            url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if "playlist" not in result.stdout.lower():
                return None

            cmd2 = [
                sys.executable, "-m", "yt_dlp", *YTDLP_JS_ARGS,
                "--dump-json",
                "--flat-playlist",
                url,
            ]
            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=60)
            entries = []
            for line in result2.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append({
                        "title": entry.get("title", "Unknown"),
                        "url": entry.get("webpage_url") or entry.get("url", ""),
                        "duration": entry.get("duration", 0),
                    })
                except json.JSONDecodeError:
                    continue

            return {"count": len(entries), "entries": entries[:100]} if entries else None
        except Exception:
            return None

    def _process_queue(self):
        with self._lock:
            max_active = self.get_adaptive_max()
            # Priority ordering: high > normal > low (FIFO within same priority)
            priority_order = {"high": 0, "normal": 1, "low": 2}
            self._queue.sort(key=lambda t: (priority_order.get(t.priority, 1), t.created_at))
            while len(self._active) < max_active and self._queue:
                task = self._queue.pop(0)
                self._active.append(task)
                t = threading.Thread(target=self._run_download, args=(task,), daemon=True)
                t.start()

    def _finalize_success(self, task, category_folder):
        file_path = task.native_output_path if hasattr(task, 'native_output_path') else ""
        file_size = os.path.getsize(file_path) if file_path and os.path.isfile(file_path) else 0
        print(f"[TAMAMLANDI] #{task.id}: {file_path or 'indirildi'} ({file_size // 1024 // 1024}MB)", flush=True)
        task.status = "completed"
        task.progress = 100
        thumb_path = self._find_thumbnail(file_path, category_folder)
        update_history(task.id, status="completed", file_path=file_path or "",
                       file_size=file_size, thumb_path=thumb_path,
                       completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._send_ws(task)
        # Aktif slotu birak ve kuyrugu ilerlet. Bu yapilmazsa gorev _active'de
        # takili kalir, max_concurrent dolar ve sonraki gorevler "Bekliyor"da kalir.
        with self._lock:
            if task in self._active:
                self._active.remove(task)
        self._process_queue()

    def _run_download(self, task):
        task.status = "downloading"
        update_history(task.id, status="downloading")

        category_folder = os.path.join(task.output_path, task.category)
        os.makedirs(category_folder, exist_ok=True)

        download_url = task.url

        title_tag = f" ({task.custom_title})" if task.custom_title else ""
        print(f"[DL] Başlıyor #{task.id}: {task.category} {download_url[:120]}{title_tag}", flush=True)

        cookiefile_path = None
        if task.is_browser_url and task.cookies_netscape:
            cookiefile_path = os.path.join(category_folder, f".af_cookies_{task.id}.txt")
            try:
                with open(cookiefile_path, "w", encoding="utf-8") as f:
                    f.write(task.cookies_netscape)
            except Exception:
                cookiefile_path = None
        cookie_args = ["--cookies", cookiefile_path] if cookiefile_path else []

        # If yt-dlp already failed on this task (retry), skip straight to native/browser
        if task.skip_ytdlp:
            print(f"[DL] #{task.id} yt-dlp atlaniyor (once basarisiz), browser/native deneniyor", flush=True)
            direct_download_url = task.custom_video_url if (task.is_browser_url and task.custom_video_url) else task.url

            # Try browser extraction first for page URLs
            if task.is_browser_url and task.page_url:
                print(f"[BROWSER] #{task.id} (retry) tarayici ile stream URL araniyor...", flush=True)
                browser_result = self._browser_extract(task)
                if not browser_result.get("error") and browser_result.get("video_url"):
                    stream_url = browser_result["video_url"]
                    print(f"[BROWSER] #{task.id} (retry) stream URL bulundu: {stream_url[:120]}", flush=True)
                    success, native_error = self._run_native_download_with_url(task, category_folder, stream_url)
                    if success:
                        self._finalize_success(task, category_folder)
                        return
                    print(f"[HATA] #{task.id} (retry) browser+native başarısız: {native_error}", flush=True)
                else:
                    print(f"[BROWSER] #{task.id} (retry) browser bos: {browser_result.get('error', '')[:100]}", flush=True)

            # Fall back to running native on the download URL directly
            success, native_error = self._run_native_download_with_url(task, category_folder, direct_download_url)
            if success:
                self._finalize_success(task, category_folder)
                return
            task.status = "failed"
            task.error = native_error
            update_history(task.id, status="failed", error_message=task.error)
            self._send_ws(task)
            with self._lock:
                if task in self._active:
                    self._active.remove(task)
            self._process_queue()
            return

        cookiefile_path = None
        cookie_args = ["--cookies", cookiefile_path] if cookiefile_path else []

        if task.format_id.startswith("audio"):
            output_template = os.path.join(category_folder, "%(title).200s.%(ext)s")
            fmt = task.format_id.replace("audio_", "") if "_" in task.format_id else "bestaudio/best"
            cmd = [
                sys.executable, "-m", "yt_dlp", *YTDLP_JS_ARGS, *cookie_args,
                "-f", fmt,
                "--extract-audio",
                "--audio-format", "mp3",
                "-o", output_template,
                "--no-playlist",
                download_url,
            ]
        else:
            custom_title = (task.custom_title or "") if task.is_browser_url else ""
            if custom_title:
                clean_title = re.sub(r'[\\/*?:"<>|]', "", custom_title).strip()[:150]
                filename = os.path.join(category_folder, f"{clean_title}.%(ext)s")
            else:
                filename = os.path.join(category_folder, "%(title).200s.%(ext)s")

            if task.format_id == "best":
                fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
            else:
                fmt = f"{task.format_id}+bestaudio/best"

            cmd = [
                sys.executable, "-m", "yt_dlp", *YTDLP_JS_ARGS, *cookie_args,
                "-f", fmt,
                "-o", filename,
                "--merge-output-format", "mp4",
                "--write-thumbnail",
                "--convert-thumbnails", "jpg",
                "--no-playlist",
                "--concurrent-fragments", "4",
                download_url,
            ]

        si = None
        if platform.system() == "Windows":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE

        try:
            task.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=si,
            )

            last_line = ""
            log_lines = []
            print(f"[YTDL] #{task.id} yt-dlp başladı", flush=True)
            for line in task.process.stdout:
                line = _strip_ansi(line.strip())
                if not line:
                    continue
                last_line = line
                log_lines.append(line)

                pct = _extract_progress(line)
                if pct is not None:
                    task.progress = pct
                    speed_str = _extract_speed(line)
                    eta_str = _extract_eta(line)
                    if speed_str:
                        task.speed = speed_str
                        self.add_batch_speed_sample(_parse_speed(speed_str))
                    if eta_str:
                        task.eta = eta_str

                    self._send_ws(task)

            task.process.wait()

            if task.process.returncode == 0:
                file_path = self._find_downloaded_file(task, log_lines, category_folder)
                file_size = os.path.getsize(file_path) if file_path and os.path.isfile(file_path) else 0
                print(f"[TAMAMLANDI] #{task.id} yt-dlp: {file_path or 'indirildi'} ({file_size // 1024 // 1024}MB)", flush=True)
                task.status = "completed"
                task.progress = 100

                thumb_path = self._find_thumbnail(file_path, category_folder)

                update_history(
                    task.id,
                    status="completed",
                    file_path=file_path or "",
                    file_size=file_size,
                    thumb_path=thumb_path,
                    completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            else:
                # yt-dlp failed — print last meaningful line, preferring a real
                # ERROR over a WARNING (a prior operator-precedence bug here
                # meant `l and "WARNING" in l or "ERROR" in l` matched almost
                # every WARNING line, so the true error was rarely shown)
                err_lines = [l for l in log_lines if l and ("ERROR" in l or "error" in l.lower())]
                warn_lines = [l for l in log_lines if l and "WARNING" in l]
                if err_lines:
                    print(f"[YTDL] #{task.id} hata: {err_lines[-1][:200]}", flush=True)
                elif warn_lines:
                    print(f"[YTDL] #{task.id} uyari: {warn_lines[-1][:200]}", flush=True)
                else:
                    print(f"[YTDL] #{task.id} başarısız (kod {task.process.returncode}): {last_line[:200]}", flush=True)

                direct_download_url = task.custom_video_url if (task.is_browser_url and task.custom_video_url) else task.url
                looks_like_media = direct_download_url.lower().split("?")[0].endswith(
                    (".mp4", ".mkv", ".webm", ".m3u8", ".mpd", ".ts", ".flv", ".mov")
                )

                # If yt-dlp failed with 403/Forbidden, try browser extraction to find real stream URL
                error_text = (err_lines[-1] if err_lines else last_line) or ""
                is_forbidden = any(kw in error_text for kw in ["403", "Forbidden", "Unable to download webpage"])

                if is_forbidden and task.is_browser_url and task.page_url:
                    print(f"[BROWSER] #{task.id} yt-dlp 403, tarayici ile stream URL araniyor...", flush=True)
                    browser_result = self._browser_extract(task)
                    if not browser_result.get("error") and browser_result.get("video_url"):
                        stream_url = browser_result["video_url"]
                        print(f"[BROWSER] #{task.id} stream URL bulundu: {stream_url[:120]}", flush=True)
                        success, native_error = self._run_native_download_with_url(task, category_folder, stream_url)
                        if success:
                            file_path = task.native_output_path if hasattr(task, 'native_output_path') else ""
                            file_size = os.path.getsize(file_path) if file_path and os.path.isfile(file_path) else 0
                            print(f"[TAMAMLANDI] #{task.id} browser+native: {file_path or 'indirildi'} ({file_size // 1024 // 1024}MB)", flush=True)
                            task.status = "completed"
                            task.progress = 100
                            update_history(task.id, status="completed", file_path=file_path or "",
                                           file_size=file_size, completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        else:
                            print(f"[HATA] #{task.id} browser+native başarısız: {native_error}", flush=True)
                            task.status = "failed"
                            task.error = native_error
                            update_history(task.id, status="failed", error_message=task.error)
                    else:
                        print(f"[BROWSER] #{task.id} browser analizi başarısız: {browser_result.get('error', 'bilinmeyen')}", flush=True)
                        # Fall through to native-only attempt below

                if task.status == "completed":
                    # Browser+native ile zaten basariyla indirildi; alttaki
                    # native/hata dallarina DUSME (yoksa status tekrar "failed"
                    # olup gereksiz RETRY tetikleniyor ve ayni film 2 kez iniyordu).
                    pass
                elif direct_download_url.startswith(("http://", "https://")) and looks_like_media:
                    print(f"[NATIVE] #{task.id} yt-dlp başarısız, native deneniyor: {direct_download_url[:100]}", flush=True)
                    success, native_error = self._run_native_download(task, category_folder)
                    if success:
                        file_path = task.native_output_path if hasattr(task, 'native_output_path') else ""
                        file_size = os.path.getsize(file_path) if file_path and os.path.isfile(file_path) else 0
                        print(f"[TAMAMLANDI] #{task.id} native: {file_path or 'indirildi'} ({file_size // 1024 // 1024}MB)", flush=True)
                        task.status = "completed"
                        task.progress = 100
                        update_history(
                            task.id,
                            status="completed",
                            file_path=file_path or "",
                            file_size=file_size,
                            completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        )
                    else:
                        print(f"[HATA] #{task.id} native başarısız: {native_error}", flush=True)
                        task.status = "failed"
                        task.error = native_error
                        update_history(task.id, status="failed", error_message=task.error)
                elif task.status == "failed":
                    # Already marked failed by browser+ytdlp attempt above
                    pass
                else:
                    error_summary = err_lines[-1] if err_lines else (warn_lines[-1] if warn_lines else last_line)
                    print(f"[HATA] #{task.id} yt-dlp başarısız: {error_summary[:200]}", flush=True)
                    task.status = "failed"
                    task.error = error_summary[:500]
                    update_history(task.id, status="failed", error_message=task.error)
        except Exception as e:
            print(f"[HATA] #{task.id} istisna: {e}", flush=True)
            task.status = "failed"
            task.error = str(e)
            update_history(task.id, status="failed", error_message=str(e))
        finally:
            if cookiefile_path and os.path.isfile(cookiefile_path):
                try:
                    os.remove(cookiefile_path)
                except Exception:
                    pass

        self._send_ws(task)

        # Auto-retry failed tasks (skip yt-dlp on retry, go straight to browser/native)
        if task.status == "failed" and task.retry_count < task.max_retries:
            task.retry_count += 1
            task.skip_ytdlp = True
            print(f"[RETRY] #{task.id} yeniden deneniyor ({task.retry_count}/{task.max_retries}) - browser/native ile: {task.url[:80]}", flush=True)
            task.status = "pending"
            task.error = None
            task.progress = 0
            task.speed = ""
            task.eta = ""
            update_history(task.id, status="pending")
            with self._lock:
                self._queue.append(task)

        with self._lock:
            if task in self._active:
                self._active.remove(task)
        self._process_queue()

    def _run_native_download_with_url(self, task, category_folder, download_url):
        """Native download with explicit URL override (used after browser extraction)."""
        custom_title = (task.custom_title or "video").strip()
        clean_title = re.sub(r'[\\/*?:"<>|]', "", custom_title)[:150]
        output_path = os.path.join(category_folder, f"{clean_title}.mp4")

        def on_progress(pct, speed_str="", eta_str=""):
            task.progress = round(pct, 1)
            task.status = "downloading"
            if speed_str:
                task.speed = speed_str
                self.add_batch_speed_sample(_parse_speed(speed_str))
            if eta_str:
                task.eta = eta_str
            self._send_ws(task)

        referer = task.page_url if task.is_browser_url else ""
        cookies = task.cookies if task.is_browser_url else ""

        try:
            if task.cancel_event.is_set():
                return False, "Iptal edildi"

            from backend.native_downloader import download_auto
            success, err = download_auto(
                download_url, output_path, progress_callback=on_progress,
                referer=referer, cookies=cookies, cancel_event=task.cancel_event,
                format_id=task.format_id,
            )
            if success and os.path.isfile(output_path):
                task.native_output_path = output_path
                self._save_poster(task, output_path)
                self._maybe_upscale(task, output_path, on_progress)
                return True, ""
            self._cleanup_junk(output_path)
            return False, err or "Native indirme basarisiz"
        except Exception as e:
            self._cleanup_junk(output_path)
            return False, f"Native indirme hatasi: {str(e)[:200]}"
        """Try native downloader (ffmpeg for HLS, multi-threaded for MP4) as yt-dlp fallback."""
        download_url = task.custom_video_url if (task.is_browser_url and task.custom_video_url) else task.url
        if not download_url:
            return False, "Indirme URL'si yok"

        custom_title = (task.custom_title or "video").strip()
        clean_title = re.sub(r'[\\/*?:"<>|]', "", custom_title)[:150]
        output_path = os.path.join(category_folder, f"{clean_title}.mp4")

        def on_progress(pct, speed_str="", eta_str=""):
            task.progress = round(pct, 1)
            task.status = "downloading"
            if speed_str:
                task.speed = speed_str
                self.add_batch_speed_sample(_parse_speed(speed_str))
            if eta_str:
                task.eta = eta_str
            self._send_ws(task)

        def on_process(proc):
            task.process = proc

        url_lower = download_url.lower()
        referer = task.page_url if task.is_browser_url else ""
        cookies = task.cookies if task.is_browser_url else ""
        try:
            if task.cancel_event.is_set():
                return False, "Iptal edildi"

            if ".mpd" in url_lower:
                print(f"[NATIVE] #{task.id} DASH segment: {download_url[:100]}", flush=True)
                from backend.native_downloader import dash_download
                success, err = dash_download(
                    download_url, output_path, progress_callback=on_progress, referer=referer,
                    cookies=cookies, cancel_event=task.cancel_event,
                )
            elif ".m3u8" in url_lower:
                print(f"[NATIVE] #{task.id} HLS (decrypt-aware): {download_url[:100]}", flush=True)
                from backend.native_downloader import hls_download_with_decrypt
                success, err = hls_download_with_decrypt(
                    download_url, output_path, progress_callback=on_progress, referer=referer,
                    cookies=cookies, cancel_event=task.cancel_event, on_process=on_process,
                )
            elif any(ext in url_lower for ext in [".mp4", ".webm", ".mkv"]):
                print(f"[NATIVE] #{task.id} multi-thread MP4: {download_url[:100]}", flush=True)
                from backend.native_downloader import multithreaded_download
                success, err = multithreaded_download(
                    download_url, output_path, progress_callback=on_progress, referer=referer,
                    cookies=cookies, cancel_event=task.cancel_event,
                )
            else:
                print(f"[NATIVE] #{task.id} single-thread: {download_url[:100]}", flush=True)
                from backend.native_downloader import _single_threaded_download
                success, err = _single_threaded_download(
                    download_url, output_path, progress_callback=on_progress, referer=referer,
                    cookies=cookies, cancel_event=task.cancel_event,
                )

            if success and os.path.isfile(output_path):
                task.native_output_path = output_path
                return True, ""
            return False, err or "Native indirme basarisiz"
        except Exception as e:
            return False, f"Native indirme hatasi: {str(e)[:200]}"

    def _find_downloaded_file(self, task, log_lines, folder):
        candidates = []
        for line in log_lines:
            if "Destination:" in line and ("[download]" in line or "[ExtractAudio]" in line):
                candidates.append(line.split("Destination:", 1)[1].strip())
            elif line.startswith("[Merger]") or line.startswith("[FixupM3u8]") or line.startswith("[Fixup"):
                m = re.search(r'"([^"]+)"', line)
                if m:
                    candidates.append(m.group(1))
            elif "[download]" in line and "has already been downloaded" in line:
                m = re.match(r"\[download\]\s+(.+?)\s+has already been downloaded", line)
                if m:
                    candidates.append(m.group(1).strip())

        for fp in reversed(candidates):
            if os.path.isfile(fp):
                return fp

        try:
            found = []
            for root, _dirs, files in os.walk(folder):
                for fname in files:
                    if fname.lower().endswith((".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".avi", ".flv", ".ts", ".mov", ".opus", ".ogg", ".wav", ".weba")):
                        fp = os.path.join(root, fname)
                        found.append((os.path.getmtime(fp), fp))
            found.sort(reverse=True)
            for mtime, fp in found:
                if time.time() - mtime < 300:
                    return fp
        except Exception:
            pass
        return ""

    def _cleanup_junk(self, output_path):
        """Basarisiz indirmede diske yazilmis kucuk/bozuk dosyayi sil; yoksa
        kutuphaneye 2 KB'lik HTML hata sayfalari 'video.mp4' olarak dusuyor."""
        try:
            if output_path and os.path.isfile(output_path) and os.path.getsize(output_path) < 1_000_000:
                os.remove(output_path)
        except Exception:
            pass

    def _save_poster(self, task, video_path):
        """Tarayicidan cikarilan kapak gorselini indirip video ile ayni ada
        <video>.jpg olarak kaydet. Basarisiz olursa sessizce gecer."""
        thumb_url = getattr(task, "thumb_url", "") or ""
        if not thumb_url or not thumb_url.startswith("http") or not video_path:
            return
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
            if task.page_url:
                headers["Referer"] = task.page_url
            r = requests.get(thumb_url, headers=headers, timeout=15)
            if r.status_code == 200 and len(r.content) > 1024:
                thumb_path = os.path.splitext(video_path)[0] + ".jpg"
                with open(thumb_path, "wb") as f:
                    f.write(r.content)
        except Exception:
            pass

    def _maybe_upscale(self, task, video_path, progress_callback=None):
        """Kullanici 4K istediyse (format_id 2160/4k) inen videoyu GPU ile 4K'ya
        buyut. Kaynak zaten >= 4K ise dokunmaz. Basarisiz olursa orijinal 1080p
        dosya korunur (indirme yine de basarili sayilir)."""
        from backend.native_downloader import _format_id_to_height, upscale_video
        if _format_id_to_height(task.format_id) != 2160:
            return
        try:
            task.status = "upscaling"
            self._send_ws(task)
            ok, err = upscale_video(video_path, progress_callback=progress_callback,
                                    cancel_event=task.cancel_event)
            if not ok:
                print(f"[UPSCALE] #{task.id} 4K'ya buyutulemedi ({err}); 1080p korundu", flush=True)
        except Exception as e:
            print(f"[UPSCALE] #{task.id} istisna: {str(e)[:150]}; orijinal korundu", flush=True)

    def _find_thumbnail(self, video_path, folder):
        if not video_path:
            return ""
        base = os.path.splitext(video_path)[0]
        for ext in (".jpg", ".webp", ".png", ".jpeg"):
            candidate = base + ext
            if os.path.isfile(candidate):
                return candidate
        try:
            for fname in os.listdir(folder):
                if fname.lower().endswith((".jpg", ".webp", ".png", ".jpeg")):
                    return os.path.join(folder, fname)
        except Exception:
            pass
        return ""

    def _send_ws(self, task):
        if task.ws_callback and self._event_loop:
            data = {
                "id": task.id,
                "status": task.status,
                "progress": task.progress,
                "speed": task.speed,
                "eta": task.eta,
                "error": task.error,
            }
            try:
                asyncio.run_coroutine_threadsafe(task.ws_callback(data), self._event_loop)
            except Exception:
                pass

    def get_queue_state(self):
        with self._lock:
            active = [
                {"id": t.id, "url": t.url, "category": t.category, "status": t.status,
                 "progress": t.progress, "speed": t.speed, "eta": t.eta}
                for t in self._active
            ]
            pending = [
                {"id": t.id, "url": t.url, "category": t.category, "status": t.status}
                for t in self._queue
            ]
        return {"active": active, "pending": pending, "max_concurrent": self.get_adaptive_max()}

    def cancel(self, task_id):
        with self._lock:
            for t in self._active:
                if t.id == task_id:
                    # Signal native-downloader threads/chunks to stop; t.process
                    # only exists for the yt-dlp subprocess (or an ffmpeg HLS
                    # process once running) - multithreaded chunk downloads have
                    # no single process to kill and rely entirely on this event.
                    t.cancel_event.set()
                    if t.process:
                        try:
                            t.process.terminate()
                            try:
                                t.process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                t.process.kill()
                                try:
                                    t.process.wait(timeout=3)
                                except subprocess.TimeoutExpired:
                                    pass
                        except Exception:
                            try:
                                t.process.kill()
                            except Exception:
                                pass
                    t.status = "cancelled"
                    update_history(t.id, status="cancelled")
                    self._active.remove(t)
                    return True
            for t in self._queue:
                if t.id == task_id:
                    self._queue.remove(t)
                    update_history(t.id, status="cancelled")
                    return True
        return False

    def get_active_count(self):
        with self._lock:
            return len(self._active)


manager = DownloadManager()


def _strip_ansi(line):
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line)


def _extract_progress(line):
    line = _strip_ansi(line)
    if "%" in line and "[download]" in line:
        try:
            pct_str = line.split("%")[0].split()[-1]
            return float(pct_str)
        except (ValueError, IndexError):
            pass
    elif "[download]" in line and "has already been downloaded" in line:
        return 100.0
    return None


def _extract_speed(line):
    if "MiB/s" in line or "KiB/s" in line or "GiB/s" in line:
        for unit in ["GiB/s", "MiB/s", "KiB/s"]:
            if unit in line:
                try:
                    val = line.split(unit)[0].split()[-1]
                    return f"{val} {unit}"
                except (ValueError, IndexError):
                    pass
    return ""


def _extract_eta(line):
    if "[download]" in line and "ETA" in line:
        try:
            return line.split("ETA", 1)[1].strip().split()[0]
        except (ValueError, IndexError):
            pass
    return ""


def _parse_speed(speed_str):
    if not speed_str:
        return 0
    try:
        parts = speed_str.split()
        val = float(parts[0])
        unit = parts[1] if len(parts) > 1 else "KiB/s"
        multipliers = {"KiB/s": 1024, "MiB/s": 1024**2, "GiB/s": 1024**3}
        return int(val * multipliers.get(unit, 1024))
    except Exception:
        return 0


def _parse_formats(text):
    formats = []
    lines = text.split("\n")
    header_found = False
    re_fmt = re.compile(
        r"^\s*(\S+)\s+(\S+)\s+"
    )
    for line in lines:
        if "ID" in line and "EXT" in line and "RESOLUTION" in line:
            header_found = True
            continue
        if not header_found or not line.strip() or line.startswith("-"):
            continue

        m = re_fmt.match(line)
        if not m:
            continue
        fid = m.group(1)
        ext = m.group(2)

        if not fid.isdigit():
            continue
        if ext.lower() in NON_VIDEO_FORMAT_EXTS:
            continue

        lower_line = line.lower()
        after_ext = line[m.end(2):].strip()

        if "video only" in lower_line:
            res = after_ext.split()[0] if after_ext.split() else ""
            label = f"{res} (video only)"
        elif "audio only" in lower_line:
            label = "Audio only"
        else:
            parts = after_ext.split()
            res = parts[0] if parts else ""
            if "x" in res:
                label = res
            else:
                label = res

        formats.append({"id": fid, "label": label, "detail": after_ext[:80]})
    return formats


def analyze_url_sync(url):
    return manager.analyze_url(url)
