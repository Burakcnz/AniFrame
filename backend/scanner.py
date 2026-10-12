import os
import time
from backend.database import (
    get_folders,
    clear_folder_files,
    upsert_external_file,
    update_folder_scan,
)
from backend.metadata import extract_metadata

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".mpg", ".mpeg", ".vob", ".ogv", ".3gp", ".m2ts", ".mts"}
THUMB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "thumbnails")


def _ensure_thumb_dir():
    os.makedirs(THUMB_DIR, exist_ok=True)


def scan_folder(folder_id, folder_path, category):
    found = 0
    clear_folder_files(folder_id)

    for root, dirs, files in os.walk(folder_path):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue

            file_path = os.path.join(root, fname)
            if not os.path.isfile(file_path):
                continue
            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                continue
            meta = extract_metadata(fname, root)

            _ensure_thumb_dir()
            thumb_name = f"{folder_id}_{found}_{os.path.splitext(fname)[0]}.jpg"
            thumb_path = os.path.join(THUMB_DIR, thumb_name)

            if not os.path.exists(thumb_path):
                try:
                    _generate_thumbnail(file_path, thumb_path)
                except Exception:
                    thumb_path = ""

            upsert_external_file(
                file_path=file_path,
                file_name=fname,
                category=category,
                folder_id=folder_id,
                file_size=file_size,
                season=meta["season"],
                episode=meta["episode"],
                year=meta["year"],
                quality=meta["quality"],
                thumb_path=thumb_path,
            )
            found += 1

    update_folder_scan(folder_id)
    return found


def scan_all():
    folders = get_folders()
    total = 0
    for f in folders:
        if os.path.isdir(f["folder_path"]):
            total += scan_folder(f["id"], f["folder_path"], f["category"])
    return total


def _generate_thumbnail(video_path, thumb_path):
    try:
        import subprocess
        import platform

        system = platform.system()
        if system == "Windows":
            ffmpeg = "ffmpeg.exe"
        else:
            ffmpeg = "ffmpeg"

        cmd = [
            ffmpeg, "-y", "-ss", "00:00:05", "-i", video_path,
            "-vframes", "1", "-q:v", "3", "-s", "320x180", thumb_path,
        ]
        subprocess.run(
            cmd,
            capture_output=True,
            timeout=15,
            startupinfo=_hide_console() if system == "Windows" else None,
        )
    except Exception:
        pass


def _hide_console():
    import subprocess as sp
    si = sp.STARTUPINFO()
    si.dwFlags |= sp.STARTF_USESHOWWINDOW
    si.wShowWindow = sp.SW_HIDE
    return si
