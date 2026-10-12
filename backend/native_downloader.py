import os
import re
import time
import hashlib
import subprocess
import platform
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

CHUNK_SIZE = 5 * 1024 * 1024
MAX_THREADS = 4
CHUNK_RETRIES = 3
CHUNK_RETRY_BACKOFF = (1, 2, 4)

VIDEO_SIGNATURES = {
    b"\x00\x00\x00": "mp4 ftyp",      # ftyp box at offset 4+
    b"ftyp": "mp4/mov",
    b"\x1a\x45\xdf\xa3": "webm/mkv",
    b"RIFF": "webm/avi",
    b"\x47": "ts (MPEG-TS)",
    b"G@": "flv",
}


def _build_headers(referer=None, cookies=None):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "identity;q=1, *;q=0",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if referer:
        headers["Referer"] = referer
        try:
            if referer.count("/") >= 3:
                origin = referer[:referer.index("/", 10)]
                headers["Origin"] = origin
        except Exception:
            pass
    if cookies:
        headers["Cookie"] = cookies
    return headers


def _is_video_file(filepath):
    """Check if downloaded file is actually a video by reading magic bytes."""
    if not os.path.isfile(filepath) or os.path.getsize(filepath) < 1024:
        return False
    try:
        with open(filepath, "rb") as f:
            header = f.read(16)
        for sig in VIDEO_SIGNATURES:
            if header.startswith(sig):
                return True
        if b"ftyp" in header[:12]:
            return True
        if header[:3] == b"ID3":
            return True
        # HLS .ts segments and other binary media don't have a fixed magic
        # byte, but HTML error pages do - reject obvious text/markup.
        if header.lstrip()[:1] in (b"<", b"{"):
            return False
        return True
    except Exception:
        return False


def _check_ffmpeg():
    try:
        p = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        return p.returncode == 0
    except Exception:
        return False


def _format_speed(bps):
    if bps <= 0:
        return ""
    if bps >= 1024 ** 3:
        return f"{bps / 1024 ** 3:.1f} GiB/s"
    if bps >= 1024 ** 2:
        return f"{bps / 1024 ** 2:.1f} MiB/s"
    return f"{bps / 1024:.1f} KiB/s"


def _format_eta(seconds):
    if seconds is None or seconds <= 0 or seconds == float("inf"):
        return ""
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class _SpeedTracker:
    """Derives a smoothed speed/ETA estimate from cumulative byte counts."""

    def __init__(self, total_bytes=0):
        self.total_bytes = total_bytes
        self._last_time = time.time()
        self._last_bytes = 0

    def update(self, downloaded_bytes):
        now = time.time()
        dt = now - self._last_time
        speed = 0.0
        if dt >= 0.4:
            speed = max(0.0, (downloaded_bytes - self._last_bytes) / dt)
            self._last_time = now
            self._last_bytes = downloaded_bytes
        eta = None
        if speed > 0 and self.total_bytes > downloaded_bytes:
            eta = (self.total_bytes - downloaded_bytes) / speed
        return speed, eta


def parse_hls_variants(master_url, content):
    """Master playlist METNINI ayristir. Doner: (variants, audio_by_group).
    variants: [{height, width, bandwidth, url, audio_group}] (yuksekten alcaga sirali)
    audio_by_group: {group_id: audio_playlist_url}. AG ISTEGI YAPMAZ.
    Master degilse variants bos doner."""
    variants = []
    audio_by_group = {}
    if not content or "#EXT-X-STREAM-INF" not in content:
        return variants, audio_by_group
    lines = content.splitlines()

    for l in lines:
        ls = l.strip()
        if ls.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in ls:
            gid = re.search(r'GROUP-ID="([^"]+)"', ls)
            uri = re.search(r'URI="([^"]+)"', ls)
            if gid and uri:
                is_default = "DEFAULT=YES" in ls
                if gid.group(1) not in audio_by_group or is_default:
                    au = uri.group(1)
                    audio_by_group[gid.group(1)] = au if au.startswith("http") else _resolve_url(master_url, au)

    for i, l in enumerate(lines):
        ls = l.strip()
        if ls.startswith("#EXT-X-STREAM-INF"):
            bw_m = re.search(r"BANDWIDTH=(\d+)", ls)
            bw = int(bw_m.group(1)) if bw_m else 0
            res_m = re.search(r"RESOLUTION=(\d+)x(\d+)", ls)
            width = int(res_m.group(1)) if res_m else 0
            height = int(res_m.group(2)) if res_m else 0
            grp_m = re.search(r'AUDIO="([^"]+)"', ls)
            grp = grp_m.group(1) if grp_m else None
            for j in range(i + 1, len(lines)):
                cand = lines[j].strip()
                if cand and not cand.startswith("#"):
                    url = cand if cand.startswith("http") else _resolve_url(master_url, cand)
                    variants.append({
                        "height": height, "width": width, "bandwidth": bw,
                        "url": url, "audio_group": grp,
                    })
                    break
    # Yuksek cozunurluk once; esitlikte yuksek bant genisligi once
    variants.sort(key=lambda v: (v["height"], v["bandwidth"]), reverse=True)
    return variants, audio_by_group


def _pick_hls_streams(master_url, content, target_height=None):
    """Master playlist metninden VIDEO varyantini ve (varsa) ona bagli AYRI AUDIO
    akisini sec. Doner: (video_url, audio_url_or_None). Master degilse
    (master_url, None). AG ISTEGI YAPMAZ (token tuketmemek icin).

    Secim COZUNURLUGE gore (en yuksek yukseklik); esitlikte bant genisligi. Boylece
    1080p AV1 (dusuk bitrate) yerine yanlislikla 720p H.264 (yuksek bitrate)
    secilmesi onlenir. target_height verilirse o cozunurluk (yoksa <= hedefe en
    yakin, o da yoksa en dusuk) secilir.

    Neden ayri ses: bu CDN'lerde ses ayri #EXT-X-MEDIA:TYPE=AUDIO akisidir; ffmpeg'e
    yalniz video verilirse SES OLMAZ. Ayrica tek varyant secmek, token'li CDN'lerde
    ffmpeg'in tum varyantlari yoklayip basarisiz olmasini onler."""
    variants, audio_by_group = parse_hls_variants(master_url, content)
    if not variants:
        return master_url, None

    chosen = variants[0]  # en yuksek cozunurluk (varsayilan)
    if target_height:
        try:
            th = int(target_height)
            exact = [v for v in variants if v["height"] == th]
            if exact:
                chosen = exact[0]
            else:
                le = [v for v in variants if v["height"] and v["height"] <= th]
                chosen = le[0] if le else variants[-1]
        except (ValueError, TypeError):
            pass

    audio_url = audio_by_group.get(chosen.get("audio_group")) if chosen.get("audio_group") else None
    return chosen["url"], audio_url


def hls_download(url, output_path, progress_callback=None, referer=None, cookies=None,
                  cancel_event=None, on_process=None, audio_url=None):
    """Download HLS/DASH stream using ffmpeg. Returns (success, error_msg).
    audio_url verilirse ayri ses akisi ikinci girdi olarak eklenip mux'lanir."""
    if not _check_ffmpeg():
        return False, "ffmpeg sistemde bulunamadi"

    si = None
    if platform.system() == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    def _input_opts():
        # ffmpeg'de bunlar GIRDI secenekleridir; kendinden sonraki -i'ye uygulanir.
        # Iki girdi (video + ses) icin her -i'den once tekrar edilmeli.
        opts = [
            "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_delay_max", "10",
            "-rw_timeout", "30000000",
            # Bazi CDN'ler .ts segmentlerini .jpg/.png gibi uzantilarla gizler;
            # ffmpeg varsayilan reddeder. ALL ile tum uzantilara izin ver.
            "-allowed_extensions", "ALL",
            "-extension_picky", "0",
        ]
        if referer:
            origin = referer[:referer.index("/", 10)] if referer.count("/") >= 3 else referer
            header_block = f"Referer: {referer}\r\nOrigin: {origin}"
            if cookies:
                header_block += f"\r\nCookie: {cookies}"
            opts += ["-headers", header_block]
        return opts

    cmd = ["ffmpeg", "-y"] + _input_opts() + ["-i", url]
    if audio_url:
        cmd += _input_opts() + ["-i", audio_url]
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += [
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-progress", "pipe:2",
        output_path,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=si,
        )
        if on_process:
            try:
                on_process(proc)
            except Exception:
                pass

        duration = None
        last_progress = 0
        cancelled = False

        for line in proc.stderr:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break

            line = line.strip()
            if "Duration:" in line:
                m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", line)
                if m:
                    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            elif "out_time_us=" in line:
                try:
                    us = int(line.split("=")[1].strip())
                    sec = us / 1_000_000
                    if duration and duration > 0:
                        pct = min(99, (sec / duration) * 100)
                        if pct - last_progress >= 1:
                            last_progress = pct
                            if progress_callback:
                                progress_callback(pct)
                except (ValueError, IndexError):
                    pass

        if cancelled:
            return False, "Iptal edildi"

        proc.wait()

        if proc.returncode == 0:
            if progress_callback:
                progress_callback(100)
            if os.path.isfile(output_path) and os.path.getsize(output_path) < 1024:
                return False, "Indirilen dosya cok kucuk (bozuk?)"
            return True, ""
        return False, f"ffmpeg cikis kodu: {proc.returncode}"
    except FileNotFoundError:
        return False, "ffmpeg bulunamadi (PATH'te degil veya kurulu degil)"
    except Exception as e:
        return False, str(e)


def get_file_size(url, timeout=10, referer=None, cookies=None):
    """Get file size via HEAD request."""
    headers = _build_headers(referer=referer, cookies=cookies)
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        size = int(resp.headers.get("Content-Length", 0))
        if size > 0:
            return size
        resp = requests.get(url, timeout=timeout, stream=True, headers=headers)
        size = int(resp.headers.get("Content-Length", 0))
        resp.close()
        return size
    except Exception:
        return 0


def _supports_range(url, timeout=10, referer=None, cookies=None):
    """Probe whether the server honors byte-range requests. If it doesn't,
    splitting into concurrent Range chunks silently produces N copies of the
    whole file glued together instead of N/th of it - so this must be
    verified before attempting a multi-connection download."""
    headers = _build_headers(referer=referer, cookies=cookies)
    headers["Range"] = "bytes=0-0"
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
        resp.close()
        if resp.status_code == 206:
            return True
        if resp.status_code == 200 and resp.headers.get("Accept-Ranges", "").lower() == "bytes":
            return True
        return False
    except Exception:
        return False


def _download_chunk(url, start, end, chunk_idx, temp_dir, referer=None, cookies=None, cancel_event=None):
    """Download a single chunk using HTTP Range header, with retries.
    Rejects anything but a real 206 partial response and verifies the
    downloaded size matches what was requested, so a server that ignores
    Range can never silently produce a corrupt merged file."""
    expected_size = end - start + 1
    headers = _build_headers(referer=referer, cookies=cookies)
    headers["Range"] = f"bytes={start}-{end}"
    chunk_path = os.path.join(temp_dir, f"chunk_{chunk_idx:06d}")

    if os.path.isfile(chunk_path) and os.path.getsize(chunk_path) == expected_size:
        return True, chunk_idx

    last_err = None
    for attempt in range(CHUNK_RETRIES):
        if cancel_event is not None and cancel_event.is_set():
            return False, chunk_idx
        try:
            resp = requests.get(url, headers=headers, timeout=30, stream=True)
            if resp.status_code != 206:
                last_err = f"HTTP {resp.status_code} (206 bekleniyor)"
                resp.close()
            else:
                written = 0
                with open(chunk_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if cancel_event is not None and cancel_event.is_set():
                            return False, chunk_idx
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)
                if written != expected_size:
                    last_err = f"boyut uyusmadi ({written}/{expected_size})"
                    try:
                        os.remove(chunk_path)
                    except Exception:
                        pass
                else:
                    return True, chunk_idx
        except Exception as e:
            last_err = str(e)

        if attempt < CHUNK_RETRIES - 1:
            time.sleep(CHUNK_RETRY_BACKOFF[min(attempt, len(CHUNK_RETRY_BACKOFF) - 1)])

    print(f"[NATIVE] Parca {chunk_idx} basarisiz: {last_err}", flush=True)
    return False, chunk_idx


def _temp_dir_for(url, output_path):
    """Deterministic (not timestamp-based) temp dir name so an interrupted
    download can be resumed by re-running with the same url/output_path."""
    key = hashlib.sha1(f"{url}|{output_path}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    return os.path.join(os.path.dirname(output_path) or ".", f".af_dl_{key}")


def multithreaded_download(url, output_path, progress_callback=None, max_threads=MAX_THREADS,
                            referer=None, cookies=None, cancel_event=None):
    """Download a file using multi-threaded HTTP Range requests, resumable
    across restarts and safe against servers that don't actually support
    Range (falls back to single-threaded in that case)."""
    file_size = get_file_size(url, referer=referer, cookies=cookies)
    if file_size <= 0:
        return _single_threaded_download(url, output_path, progress_callback, referer=referer,
                                           cookies=cookies, cancel_event=cancel_event)

    if not _supports_range(url, referer=referer, cookies=cookies):
        print("[NATIVE] Sunucu Range desteklemiyor, tek baglantiya dusuluyor", flush=True)
        return _single_threaded_download(url, output_path, progress_callback, referer=referer,
                                          cookies=cookies, cancel_event=cancel_event)

    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_dir = _temp_dir_for(url, output_path)
    os.makedirs(temp_dir, exist_ok=True)

    chunk_size = max(CHUNK_SIZE, file_size // (max_threads * 4))
    chunks = []
    start = 0
    chunk_idx = 0
    while start < file_size:
        end = min(start + chunk_size - 1, file_size - 1)
        chunks.append((start, end, chunk_idx))
        start = end + 1
        chunk_idx += 1

    total_chunks = len(chunks)
    completed_chunks = 0
    completed_bytes = 0
    lock = Lock()
    tracker = _SpeedTracker(total_bytes=file_size)

    # Pre-count already-resumed chunks (from a prior interrupted run) so the
    # progress bar doesn't jump backwards to 0% on resume.
    for s, e, i in chunks:
        cp = os.path.join(temp_dir, f"chunk_{i:06d}")
        if os.path.isfile(cp) and os.path.getsize(cp) == (e - s + 1):
            completed_bytes += (e - s + 1)

    def _tracked_chunk(start, end, idx):
        nonlocal completed_chunks, completed_bytes
        if cancel_event is not None and cancel_event.is_set():
            return False
        ok, _ = _download_chunk(url, start, end, idx, temp_dir, referer, cookies, cancel_event)
        with lock:
            completed_chunks += 1
            if ok:
                completed_bytes += (end - start + 1)
            if progress_callback:
                pct = (completed_chunks / total_chunks) * 100
                speed, eta = tracker.update(completed_bytes)
                progress_callback(min(99, pct), _format_speed(speed), _format_eta(eta))
        return ok

    success_count = 0
    with ThreadPoolExecutor(max_workers=min(max_threads, total_chunks)) as executor:
        futures = [executor.submit(_tracked_chunk, s, e, i) for s, e, i in chunks]
        for future in as_completed(futures):
            if future.result():
                success_count += 1

    if cancel_event is not None and cancel_event.is_set():
        return False, "Iptal edildi"

    if success_count != total_chunks:
        return False, f"Indirildi {success_count}/{total_chunks} parca (bazisi basarisiz, tekrar denemek icin ayni indirmeyi baslat)"

    try:
        with open(output_path, "wb") as out:
            for i in range(total_chunks):
                chunk_path = os.path.join(temp_dir, f"chunk_{i:06d}")
                if os.path.isfile(chunk_path):
                    with open(chunk_path, "rb") as cf:
                        while True:
                            buf = cf.read(65536)
                            if not buf:
                                break
                            out.write(buf)
    except Exception as e:
        return False, f"Birlestirme hatasi: {e}"

    _cleanup_temp(temp_dir)

    if progress_callback:
        progress_callback(100, "", "")
    return True, ""


def _single_threaded_download(url, output_path, progress_callback=None, referer=None, cookies=None,
                               cancel_event=None):
    """Single-threaded streaming download (fallback)."""
    try:
        headers = _build_headers(referer=referer, cookies=cookies)
        resp = requests.get(url, stream=True, timeout=30, headers=headers)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"

        content_type = resp.headers.get("Content-Type", "").lower()
        if "text/html" in content_type and not url.lower().endswith((".ts", ".m3u8", ".mpd")):
            return False, "Sunucu video yerine HTML sayfasi dondurdu"

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        last_reported = 0
        tracker = _SpeedTracker(total_bytes=total)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if cancel_event is not None and cancel_event.is_set():
                    return False, "Iptal edildi"
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = (downloaded / total) * 100
                        if pct - last_reported >= 1:
                            last_reported = pct
                            if progress_callback:
                                speed, eta = tracker.update(downloaded)
                                progress_callback(min(99, pct), _format_speed(speed), _format_eta(eta))

        if progress_callback:
            progress_callback(100, "", "")

        if os.path.isfile(output_path):
            fsize = os.path.getsize(output_path)
            if fsize < 4096:
                return False, f"Dosya cok kucuk ({fsize} byte) - indirme basarisiz olabilir"
            if not _is_video_file(output_path) and not url.lower().endswith((".ts", ".m3u8", ".mpd")):
                return False, "Indirilen dosya video degil (HTML sayfasi?)"
        return True, ""
    except Exception as e:
        return False, str(e)


def _cleanup_temp(temp_dir):
    """Remove temporary directory."""
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


def rtmp_download(url, output_path, duration_limit_sec=3600, progress_callback=None, cancel_event=None):
    """Download RTMP/RTSP stream using ffmpeg."""
    if not _check_ffmpeg():
        return False, "ffmpeg bulunamadi"

    si = None
    if platform.system() == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-rtmp_live", "live",
        "-timeout", "30",
        "-i", url,
        "-c", "copy",
        "-t", str(duration_limit_sec),
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace", startupinfo=si)
        cancelled = False
        for line in proc.stderr:
            if cancel_event and cancel_event.is_set():
                cancelled = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break

        if cancelled:
            return False, "Iptal edildi"

        proc.wait()
        if proc.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 4096:
            if progress_callback:
                progress_callback(100, "", "")
            return True, ""
        return False, f"RTMP indirme hatasi: {proc.returncode}"
    except Exception as e:
        return False, str(e)


def _sniff_stream_type(url, referer=None, cookies=None):
    """Uzantisiz URL'ler icin ( or. https://cdn/mf/HASH) icerigi koklayarak
    HLS/DASH tespit et. Embed CDN'leri stream'i uzantisiz servis edebilir; bu
    durumda m3u8 metni yanlislikla dogrudan dosya olarak kaydediliyordu."""
    try:
        headers = _build_headers(referer=referer, cookies=cookies)
        resp = requests.get(url, stream=True, timeout=15, headers=headers)
        ctype = resp.headers.get("Content-Type", "").lower()
        chunk = b""
        try:
            for c in resp.iter_content(chunk_size=2048):
                chunk = c or b""
                break
        finally:
            resp.close()
        head = chunk.decode("utf-8", "ignore").lstrip()
        if "mpegurl" in ctype or head.startswith("#EXTM3U"):
            return "hls"
        if "dash+xml" in ctype or (head.startswith("<?xml") and "MPD" in head):
            return "dash"
    except Exception:
        pass
    return "direct"


def _format_id_to_height(format_id):
    """'1080' -> 1080, '720p' -> 720, '4k' -> 2160, 'best'/'native'/None -> None."""
    if not format_id:
        return None
    fid = str(format_id).lower()
    if "4k" in fid or "2160" in fid:
        return 2160
    m = re.search(r"(\d{3,4})", fid)
    return int(m.group(1)) if m else None


def _probe_height(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return int(re.search(r"\d+", out).group(0)) if out else 0
    except Exception:
        return 0


def upscale_video(input_path, target_w=3840, target_h=2160, progress_callback=None,
                  cancel_event=None, on_process=None):
    """Videoyu GPU (NVENC) ile hedef cozunurluge buyut. Kaynak zaten >= hedefse
    dokunmaz. GPU basarisiz olursa CPU (libx265/lanczos) ile dener. Basarida
    orijinal dosyanin uzerine yazar. Donen: (success, error_msg).

    NOT: Bu GERCEK 4K degil - interpolasyon (yukari olcekleme); detay eklemez."""
    if not _check_ffmpeg():
        return False, "ffmpeg yok"
    if not os.path.isfile(input_path):
        return False, "girdi dosyasi yok"

    src_h = _probe_height(input_path)
    if src_h and src_h >= target_h:
        return True, ""  # zaten yeterli cozunurlukte

    si = None
    if platform.system() == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    base, ext = os.path.splitext(input_path)
    tmp_out = f"{base}.4k{ext or '.mp4'}"
    scale_expr = f"{target_w}:{target_h}"

    gpu_cmd = [
        "ffmpeg", "-y", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
        "-i", input_path,
        "-vf", f"scale_cuda={scale_expr}:interp_algo=lanczos",
        "-c:v", "hevc_nvenc", "-preset", "p5", "-cq", "24",
        "-c:a", "copy", "-movflags", "+faststart",
        "-progress", "pipe:2", tmp_out,
    ]
    cpu_cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"scale={scale_expr}:flags=lanczos",
        "-c:v", "libx265", "-crf", "24", "-preset", "medium",
        "-c:a", "copy", "-movflags", "+faststart",
        "-progress", "pipe:2", tmp_out,
    ]

    for cmd, label in ((gpu_cmd, "GPU/NVENC"), (cpu_cmd, "CPU/x265")):
        try:
            if cancel_event is not None and cancel_event.is_set():
                return False, "Iptal edildi"
            if os.path.isfile(tmp_out):
                try: os.remove(tmp_out)
                except Exception: pass
            print(f"[UPSCALE] {label} ile 4K'ya buyutuluyor: {os.path.basename(input_path)}", flush=True)
            proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace", startupinfo=si)
            if on_process:
                try: on_process(proc)
                except Exception: pass
            duration = None
            for line in proc.stderr:
                if cancel_event is not None and cancel_event.is_set():
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: proc.kill()
                    return False, "Iptal edildi"
                line = line.strip()
                if "Duration:" in line:
                    m = re.search(r"Duration: (\d+):(\d+):(\d+)", line)
                    if m:
                        duration = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3))
                elif "out_time_us=" in line and duration and progress_callback:
                    try:
                        sec = int(line.split("=")[1].strip()) / 1_000_000
                        progress_callback(min(99, (sec/duration)*100) if duration else 0)
                    except (ValueError, IndexError):
                        pass
            proc.wait()
            if (proc.returncode == 0 and os.path.isfile(tmp_out)
                    and os.path.getsize(tmp_out) > 50_000
                    and _probe_height(tmp_out) >= target_h):
                try:
                    os.replace(tmp_out, input_path)  # orijinalin uzerine yaz
                    if progress_callback: progress_callback(100)
                    print(f"[UPSCALE] {label} tamamlandi ({src_h}p -> {target_h}p)", flush=True)
                    return True, ""
                except Exception as e:
                    return False, f"dosya degistirilemedi: {e}"
            print(f"[UPSCALE] {label} basarisiz (kod {proc.returncode}), sonraki yontem deneniyor", flush=True)
        except FileNotFoundError:
            return False, "ffmpeg bulunamadi"
        except Exception as e:
            print(f"[UPSCALE] {label} hata: {str(e)[:150]}", flush=True)
    if os.path.isfile(tmp_out):
        try: os.remove(tmp_out)
        except Exception: pass
    return False, "Upscale basarisiz (GPU ve CPU)"


def download_auto(url, output_path, progress_callback=None, referer=None, cookies=None,
                   cancel_event=None, on_process=None, format_id=None):
    """Smart auto-downloader: detects type and routes to correct downloader."""
    url_lower = url.lower()
    target_height = _format_id_to_height(format_id)

    if url.startswith(("rtmp://", "rtmps://", "rtsp://", "rtsps://")):
        return rtmp_download(url, output_path, progress_callback=progress_callback,
                              cancel_event=cancel_event)

    if ".mpd" in url_lower:
        return dash_download(url, output_path, progress_callback=progress_callback,
                              referer=referer, cookies=cookies, cancel_event=cancel_event)

    if ".m3u8" in url_lower:
        return hls_download_with_decrypt(url, output_path, progress_callback=progress_callback,
                                          referer=referer, cookies=cookies, cancel_event=cancel_event,
                                          on_process=on_process, target_height=target_height)

    if any(ext in url_lower for ext in [".mp4", ".webm", ".mkv", ".flv", ".avi", ".mov"]):
        return multithreaded_download(url, output_path, progress_callback=progress_callback,
                                       referer=referer, cookies=cookies, cancel_event=cancel_event)

    # Uzanti taninmadi: icerigi koklaya (m3u8/mpd metni olabilir)
    sniffed = _sniff_stream_type(url, referer=referer, cookies=cookies)
    if sniffed == "hls":
        print(f"[NATIVE] sniff: HLS tespit edildi (uzantisiz URL)", flush=True)
        return hls_download_with_decrypt(url, output_path, progress_callback=progress_callback,
                                          referer=referer, cookies=cookies, cancel_event=cancel_event,
                                          on_process=on_process, target_height=target_height)
    if sniffed == "dash":
        print(f"[NATIVE] sniff: DASH tespit edildi (uzantisiz URL)", flush=True)
        return dash_download(url, output_path, progress_callback=progress_callback,
                              referer=referer, cookies=cookies, cancel_event=cancel_event)

    return _single_threaded_download(url, output_path, progress_callback=progress_callback,
                                      referer=referer, cookies=cookies, cancel_event=cancel_event)


# ═══════════════════════════════════════════════════════════════
# DUAL SOURCE HTTP DOWNLOAD (ayri video + audio URL'leri)
# YouTube/streaming platformlarinda video ve audio ayri sunulur
# ═══════════════════════════════════════════════════════════════

def dual_source_download(video_url, audio_url, output_path, progress_callback=None,
                          referer=None, cookies=None, cancel_event=None):
    """Download video and audio from separate URLs, then merge with ffmpeg."""
    if not _check_ffmpeg():
        return False, "ffmpeg sistemde bulunamadi"

    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)

    video_key = hashlib.sha1(f"video:{video_url}".encode()).hexdigest()[:12]
    audio_key = hashlib.sha1(f"audio:{audio_url}".encode()).hexdigest()[:12]
    video_tmp = os.path.join(output_dir, f".af_{video_key}_video.tmp")
    audio_tmp = os.path.join(output_dir, f".af_{audio_key}_audio.tmp")

    total_weight = 0.85
    audio_weight = 0.15

    video_progress = [0.0]
    audio_progress = [0.0]
    lock = Lock()

    def video_cb(pct, speed="", eta=""):
        with lock:
            video_progress[0] = pct
            combined = video_progress[0] * total_weight + audio_progress[0] * audio_weight
            if progress_callback:
                progress_callback(min(99, combined), speed, eta)

    def audio_cb(pct, speed="", eta=""):
        with lock:
            audio_progress[0] = pct
            combined = video_progress[0] * total_weight + audio_progress[0] * audio_weight
            if progress_callback:
                progress_callback(min(99, combined), speed, eta)

    video_ok, video_err = multithreaded_download(
        video_url, video_tmp, progress_callback=video_cb,
        referer=referer, cookies=cookies, cancel_event=cancel_event
    )

    if cancel_event and cancel_event.is_set():
        _cleanup_temp_file(video_tmp)
        _cleanup_temp_file(audio_tmp)
        return False, "Iptal edildi"

    if not video_ok:
        _cleanup_temp_file(video_tmp)
        _cleanup_temp_file(audio_tmp)
        return False, f"Video indirilemedi: {video_err}"

    audio_ok, audio_err = multithreaded_download(
        audio_url, audio_tmp, progress_callback=audio_cb,
        referer=referer, cookies=cookies, cancel_event=cancel_event
    )

    if cancel_event and cancel_event.is_set():
        _cleanup_temp_file(video_tmp)
        _cleanup_temp_file(audio_tmp)
        return False, "Iptal edildi"

    if not audio_ok:
        _cleanup_temp_file(video_tmp)
        _cleanup_temp_file(audio_tmp)
        return False, f"Ses indirilemedi: {audio_err}"

    # Merge with ffmpeg
    si = None
    if platform.system() == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    merge_cmd = [
        "ffmpeg", "-y",
        "-i", video_tmp,
        "-i", audio_tmp,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        proc = subprocess.run(merge_cmd, capture_output=True, text=True, timeout=300, startupinfo=si)
        _cleanup_temp_file(video_tmp)
        _cleanup_temp_file(audio_tmp)

        if proc.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 4096:
            if progress_callback:
                progress_callback(100, "", "")
            return True, ""
        return False, f"ffmpeg merge hatasi: {proc.stderr[:200] if proc.stderr else 'bilinmeyen'}"
    except Exception as e:
        _cleanup_temp_file(video_tmp)
        _cleanup_temp_file(audio_tmp)
        return False, f"Merge hatasi: {str(e)[:200]}"


def _cleanup_temp_file(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# DASH (MPEG-DASH) SEGMENT DOWNLOAD
# MPD manifest parse -> segment URL'leri olustur -> paralel indir -> ffmpeg merge
# ═══════════════════════════════════════════════════════════════

def dash_download(mpd_url, output_path, progress_callback=None, referer=None, cookies=None,
                   cancel_event=None, on_process=None):
    """Download DASH stream by parsing MPD manifest, downloading segments, merging."""
    if not _check_ffmpeg():
        return False, "ffmpeg sistemde bulunamadi"

    try:
        from xml.dom import minidom
        headers = _build_headers(referer=referer, cookies=cookies)
        resp = requests.get(mpd_url, timeout=30, headers=headers)
        if resp.status_code != 200:
            return False, f"MPD indirilemedi: HTTP {resp.status_code}"
        mpd_text = resp.text
        dom = minidom.parseString(mpd_text)
    except Exception as e:
        return False, f"MPD parse hatasi: {str(e)[:200]}"

    base_url = _extract_mpd_base_url(dom, mpd_url)
    timescale, duration, segments = _parse_mpd_segments(dom)

    if not segments:
        return False, "MPD'de segment bulunamadi"

    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_dir = _temp_dir_for(mpd_url, output_path)
    os.makedirs(temp_dir, exist_ok=True)

    total_segments = len(segments)
    completed = [0]
    lock = Lock()

    def _dl_segment(idx, seg_url):
        if cancel_event and cancel_event.is_set():
            return False
        seg_path = os.path.join(temp_dir, f"seg_{idx:06d}.m4s")
        if os.path.isfile(seg_path) and os.path.getsize(seg_path) > 0:
            with lock:
                completed[0] += 1
                if progress_callback:
                    progress_callback(min(99, (completed[0] / total_segments) * 100))
            return True

        for attempt in range(3):
            if cancel_event and cancel_event.is_set():
                return False
            try:
                r = requests.get(seg_url, headers=headers, timeout=30)
                if r.status_code == 200:
                    with open(seg_path, "wb") as f:
                        f.write(r.content)
                    if os.path.getsize(seg_path) > 0:
                        with lock:
                            completed[0] += 1
                            if progress_callback:
                                progress_callback(min(99, (completed[0] / total_segments) * 100))
                        return True
            except Exception:
                pass
            time.sleep(1 if attempt == 0 else 2)
        return False

    with ThreadPoolExecutor(max_workers=min(8, total_segments)) as executor:
        futures = {executor.submit(_dl_segment, i, u): i for i, u in enumerate(segments)}
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                break
            future.result()

    if cancel_event and cancel_event.is_set():
        return False, "Iptal edildi"

    # ffmpeg concat
    concat_path = os.path.join(temp_dir, "concat.txt")
    try:
        with open(concat_path, "w") as f:
            for i in range(total_segments):
                seg_path = os.path.join(temp_dir, f"seg_{i:06d}.m4s")
                if os.path.isfile(seg_path):
                    f.write(f"file '{seg_path.replace(os.sep, '/')}'\n")
    except Exception as e:
        return False, f"Concat dosyasi olusturulamadi: {e}"

    si = None
    if platform.system() == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    merge_cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_path,
        "-c", "copy", "-movflags", "+faststart",
        output_path,
    ]

    try:
        proc = subprocess.run(merge_cmd, capture_output=True, text=True, timeout=600, startupinfo=si)
        _cleanup_temp(temp_dir)
        if proc.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 4096:
            if progress_callback:
                progress_callback(100, "", "")
            return True, ""
        return False, f"ffmpeg concat hatasi: {proc.returncode}"
    except Exception as e:
        _cleanup_temp(temp_dir)
        return False, f"Merge hatasi: {str(e)[:200]}"


def _extract_mpd_base_url(dom, mpd_url):
    base_els = dom.getElementsByTagName("BaseURL")
    if base_els and base_els[0].firstChild:
        base = base_els[0].firstChild.nodeValue.strip()
        if base.startswith("http"):
            return base
        return _resolve_url(mpd_url, base)
    return mpd_url.rsplit("/", 1)[0] + "/"


def _parse_mpd_segments(dom):
    """Parse MPD and return (timescale, duration, list_of_segment_urls)."""
    periods = dom.getElementsByTagName("Period")
    if not periods:
        return 1, 0, []

    period = periods[0]
    duration_str = period.getAttribute("duration") or ""
    duration = _parse_iso_duration(duration_str)

    adaptation_sets = period.getElementsByTagName("AdaptationSet")
    video_set = None
    for ads in adaptation_sets:
        mime = ads.getAttribute("mimeType") or ads.getAttribute("contentType") or ""
        if "video" in mime.lower():
            video_set = ads
            break
    if not video_set and adaptation_sets:
        video_set = adaptation_sets[0]
    if not video_set:
        return 1, 0, []

    reps = video_set.getElementsByTagName("Representation")
    if not reps:
        return 1, 0, []

    best_rep = reps[-1]
    for rep in reps:
        old_bw = int(best_rep.getAttribute("bandwidth") or "0")
        new_bw = int(rep.getAttribute("bandwidth") or "0")
        if new_bw > old_bw:
            best_rep = rep

    seg_template = (period.getElementsByTagName("SegmentTemplate") +
                     video_set.getElementsByTagName("SegmentTemplate") +
                     best_rep.getElementsByTagName("SegmentTemplate"))

    if seg_template:
        return _resolve_segment_template(seg_template[0], best_rep, duration)

    seg_list = (period.getElementsByTagName("SegmentList") +
                 video_set.getElementsByTagName("SegmentList"))
    if seg_list:
        return _resolve_segment_list(seg_list[0], best_rep)

    return 1, 0, []


def _resolve_segment_template(template, representation, duration):
    timescale = int(template.getAttribute("timescale") or "1")
    duration_attr = int(template.getAttribute("duration") or "0")
    start_number = int(template.getAttribute("startNumber") or "1")
    initialization = template.getAttribute("initialization") or ""
    media = template.getAttribute("media") or ""

    if not media or duration_attr <= 0:
        dur_str = representation.getAttribute("duration") or ""
        if dur_str:
            duration_attr = int(dur_str) // timescale if timescale else int(dur_str)

    if not media and not initialization:
        return timescale, duration_attr, []

    rep_id = representation.getAttribute("id") or ""
    bandwidth = representation.getAttribute("bandwidth") or ""

    total_dur = duration or (duration_attr * 10)
    segment_count = max(1, total_dur // max(1, duration_attr)) if total_dur > 0 else 10

    segments = []
    base_pattern = media.replace("$RepresentationID$", rep_id).replace("$Bandwidth$", bandwidth)

    for i in range(segment_count):
        number = start_number + i
        time_val = i * duration_attr
        seg_url = base_pattern.replace("$Number%06d$", f"{number:06d}")
        seg_url = seg_url.replace("$Number%05d$", f"{number:05d}")
        seg_url = seg_url.replace("$Number%04d$", f"{number:04d}")
        seg_url = seg_url.replace("$Number%03d$", f"{number:03d}")
        seg_url = seg_url.replace("$Number%02d$", f"{number:02d}")
        seg_url = seg_url.replace("$Number$", str(number))
        seg_url = seg_url.replace("$Time$", str(time_val))
        segments.append(seg_url)

    return timescale, total_dur, segments


def _resolve_segment_list(seg_list, representation):
    urls = []
    for surl in seg_list.getElementsByTagName("SegmentURL"):
        media_attr = surl.getAttribute("media") or ""
        if media_attr:
            urls.append(media_attr)
    return 1, 0, urls


def _parse_iso_duration(dur_str):
    if not dur_str:
        return 0
    dur_str = dur_str.replace("PT", "").replace("S", "")
    total = 0
    if "H" in dur_str:
        parts = dur_str.split("H")
        total += int(parts[0]) * 3600
        dur_str = parts[1] if len(parts) > 1 else ""
    if "M" in dur_str:
        parts = dur_str.split("M")
        total += int(parts[0]) * 60
        dur_str = parts[1] if len(parts) > 1 else ""
    if dur_str:
        try:
            total += float(dur_str)
        except ValueError:
            pass
    return int(total)


def _resolve_url(base, relative):
    if not relative:
        return base
    if relative.startswith(("http://", "https://")):
        return relative
    if relative.startswith("//"):
        return "https:" + relative
    if relative.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(base)
        return f"{parsed.scheme}://{parsed.netloc}{relative}"
    return base.rsplit("/", 1)[0] + "/" + relative


# ═══════════════════════════════════════════════════════════════
# HLS AES-128 DECRYPTION
# EXT-X-KEY: METHOD=AES-128, URI="key_url", IV=0x...
# ═══════════════════════════════════════════════════════════════

def _download_hls_key(key_uri, headers=None, timeout=15):
    """Download AES-128 key from EXT-X-KEY URI."""
    try:
        r = requests.get(key_uri, headers=headers or {}, timeout=timeout)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


def _aes_decrypt_segment(data, key, iv):
    """Decrypt AES-128-CBC encrypted segment data."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        return decryptor.update(data) + decryptor.finalize()
    except ImportError:
        pass

    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return cipher.decrypt(data)
    except ImportError:
        pass

    return None


def hls_download_with_decrypt(url, output_path, progress_callback=None, referer=None, cookies=None,
                               cancel_event=None, on_process=None, target_height=None):
    """Download HLS stream with AES-128 decryption support. Falls back to ffmpeg if no key found."""
    headers = _build_headers(referer=referer, cookies=cookies)

    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return hls_download(url, output_path, progress_callback, referer, cookies, cancel_event, on_process)
        content = r.text
    except Exception:
        return hls_download(url, output_path, progress_callback, referer, cookies, cancel_event, on_process)

    if not content.strip().startswith("#EXTM3U"):
        return hls_download(url, output_path, progress_callback, referer, cookies, cancel_event, on_process)

    # Check for encryption
    has_encryption = "#EXT-X-KEY" in content and "AES-128" in content

    if not has_encryption:
        # Master ise onceden alinmis metinden tek VIDEO varyanti ve (varsa) ayri
        # SES akisini sec; DOGRUDAN ffmpeg'e ver. Burada yeniden indirmiyoruz;
        # token'li CDN'lerde ffmpeg taze cekmeli. ffmpeg AES-128 HLS'i de coz.
        video_url, audio_url = _pick_hls_streams(url, content, target_height=target_height)
        return hls_download(video_url, output_path, progress_callback, referer, cookies,
                            cancel_event, on_process, audio_url=audio_url)

    # Parse playlist and decrypt
    key_uri = None
    key_iv = None
    segments = []
    base_url = url.rsplit("/", 1)[0] + "/"

    lines = content.strip().split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("#EXT-X-KEY:"):
            attrs = {}
            raw = line[len("#EXT-X-KEY:"):]
            for part in raw.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    attrs[k.strip()] = v.strip().strip('"')
            if attrs.get("METHOD") == "AES-128":
                key_uri = attrs.get("URI", "")
                iv_str = attrs.get("IV", "")
                if iv_str and iv_str.startswith("0x"):
                    key_iv = bytes.fromhex(iv_str[2:].zfill(32))
        elif line.startswith("#EXTINF:") and i + 1 < len(lines):
            seg_url = lines[i + 1].strip()
            if seg_url and not seg_url.startswith("#"):
                if not seg_url.startswith("http"):
                    seg_url = _resolve_url(url, seg_url)
                segments.append(seg_url)

    if not segments:
        return hls_download(url, output_path, progress_callback, referer, cookies, cancel_event, on_process)

    if not key_uri:
        return hls_download(url, output_path, progress_callback, referer, cookies, cancel_event, on_process)

    if not key_uri.startswith("http"):
        key_uri = _resolve_url(url, key_uri)

    aes_key = _download_hls_key(key_uri, headers)
    if not aes_key or len(aes_key) != 16:
        return hls_download(url, output_path, progress_callback, referer, cookies, cancel_event, on_process)

    if key_iv is None:
        key_iv = bytes(16)

    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_dir = _temp_dir_for(url, output_path)
    os.makedirs(temp_dir, exist_ok=True)

    total = len(segments)
    completed = [0]
    lock = Lock()

    def _dl_decrypt(idx, seg_url, seg_iv):
        if cancel_event and cancel_event.is_set():
            return False
        dec_path = os.path.join(temp_dir, f"dec_{idx:06d}.ts")
        if os.path.isfile(dec_path) and os.path.getsize(dec_path) > 0:
            with lock:
                completed[0] += 1
                if progress_callback:
                    progress_callback(min(99, (completed[0] / total) * 100))
            return True

        for attempt in range(3):
            if cancel_event and cancel_event.is_set():
                return False
            try:
                r = requests.get(seg_url, headers=headers, timeout=30)
                if r.status_code == 200:
                    decrypted = _aes_decrypt_segment(r.content, aes_key, seg_iv)
                    if decrypted is None:
                        return False
                    with open(dec_path, "wb") as f:
                        f.write(decrypted)
                    with lock:
                        completed[0] += 1
                        if progress_callback:
                            progress_callback(min(99, (completed[0] / total) * 100))
                    return True
            except Exception:
                pass
            time.sleep(1 if attempt == 0 else 2)
        return False

    with ThreadPoolExecutor(max_workers=min(8, total)) as executor:
        futures = []
        for i, seg_url in enumerate(segments):
            seg_iv = bytearray(key_iv)
            seq = i
            for byte_idx in range(15, 11, -1):
                seg_iv[byte_idx] = seq & 0xFF
                seq >>= 8
            futures.append(executor.submit(_dl_decrypt, i, seg_url, bytes(seg_iv)))
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                break
            future.result()

    if cancel_event and cancel_event.is_set():
        _cleanup_temp(temp_dir)
        return False, "Iptal edildi"

    concat_path = os.path.join(temp_dir, "concat.txt")
    with open(concat_path, "w") as f:
        for i in range(total):
            dec_path = os.path.join(temp_dir, f"dec_{i:06d}.ts")
            if os.path.isfile(dec_path):
                f.write(f"file '{dec_path.replace(os.sep, '/')}'\n")

    si = None
    if platform.system() == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    merge_cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path, "-c", "copy", output_path]
    try:
        proc = subprocess.run(merge_cmd, capture_output=True, text=True, timeout=600, startupinfo=si)
        _cleanup_temp(temp_dir)
        if proc.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 4096:
            if progress_callback:
                progress_callback(100, "", "")
            return True, ""
        return False, f"ffmpeg concat hatasi: {proc.returncode}"
    except Exception as e:
        _cleanup_temp(temp_dir)
        return False, f"Merge hatasi: {str(e)[:200]}"
