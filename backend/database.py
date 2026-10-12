import sqlite3
import os
import json
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "aniframe.db")

_ALLOWED_COLUMNS = {
    "title", "category", "format_label", "file_path", "file_size",
    "status", "error_message", "completed_at", "thumb_path",
}


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS download_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                category TEXT DEFAULT 'uncategorized',
                format_label TEXT,
                file_path TEXT,
                file_size INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS external_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_name TEXT NOT NULL,
                category TEXT DEFAULT 'uncategorized',
                folder_id INTEGER,
                file_size INTEGER DEFAULT 0,
                duration REAL DEFAULT 0,
                season INTEGER DEFAULT 0,
                episode INTEGER DEFAULT 0,
                year INTEGER DEFAULT 0,
                quality TEXT DEFAULT '',
                added_at TEXT DEFAULT (datetime('now', 'localtime')),
                thumb_path TEXT DEFAULT '',
                FOREIGN KEY (folder_id) REFERENCES external_folders(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS external_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_path TEXT UNIQUE NOT NULL,
                category TEXT DEFAULT 'uncategorized',
                label TEXT DEFAULT '',
                last_scanned TEXT,
                added_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_history_status ON download_history(status);
            CREATE INDEX IF NOT EXISTS idx_history_category ON download_history(category);
            CREATE INDEX IF NOT EXISTS idx_ext_files_category ON external_files(category);
            CREATE INDEX IF NOT EXISTS idx_ext_files_folder ON external_files(folder_id);
        """)
        conn.commit()

        cols = [r[1] for r in conn.execute("PRAGMA table_info(download_history)").fetchall()]
        if "thumb_path" not in cols:
            conn.execute("ALTER TABLE download_history ADD COLUMN thumb_path TEXT DEFAULT ''")
            conn.commit()
        _init_defaults(conn)
    finally:
        conn.close()


def _init_defaults(conn):
    defaults = {
        "download_path": os.path.join(BASE_DIR, "downloads"),
        "max_concurrent": "auto",
        "language": "tr",
        "scan_on_startup": "true",
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )
    os.makedirs(defaults["download_path"], exist_ok=True)
    conn.commit()


def _stale_entries(conn):
    conn.execute(
        "DELETE FROM download_history WHERE file_path IS NOT NULL AND file_path != '' AND status='completed'"
    )
    conn.execute(
        "DELETE FROM external_files WHERE file_path IS NOT NULL AND file_path != ''"
    )
    conn.commit()


def get_setting(key, default=None):
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    finally:
        conn.close()


def get_all_settings():
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def add_history(url, title=None, category="uncategorized"):
    conn = get_db()
    try:
        c = conn.execute(
            "INSERT INTO download_history (url, title, category, status) VALUES (?, ?, ?, 'pending')",
            (url, title, category),
        )
        hid = c.lastrowid
        conn.commit()
        return hid
    finally:
        conn.close()


def update_history(hid, **kwargs):
    filtered = {k: v for k, v in kwargs.items() if k in _ALLOWED_COLUMNS}
    if not filtered:
        return
    conn = get_db()
    try:
        sets = ", ".join(f"{k}=?" for k in filtered)
        vals = list(filtered.values())
        conn.execute(f"UPDATE download_history SET {sets} WHERE id=?", vals + [hid])
        conn.commit()
    finally:
        conn.close()


def reset_stale_downloads():
    """Sunucu baslatilirken, onceki oturumdan kalan 'downloading'/'pending'
    kayitlari 'failed' yap. Aksi halde sunucu indirme sirasinda kapaninca o
    satirlar sonsuza dek 'Indiriliyor' olarak takili kaliyor (bellekteki gorev
    ise restart'ta kayboluyor)."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE download_history SET status='failed', "
            "error_message='Sunucu yeniden baslatildi (indirme yarim kaldi)' "
            "WHERE status IN ('downloading', 'pending')"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def cleanup_missing_files():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, file_path FROM download_history WHERE file_path IS NOT NULL AND file_path != ''"
        ).fetchall()
        for r in rows:
            if not os.path.isfile(r["file_path"]):
                conn.execute("DELETE FROM download_history WHERE id=?", (r["id"],))
        rows = conn.execute(
            "SELECT id, file_path, thumb_path FROM external_files"
        ).fetchall()
        for r in rows:
            if not os.path.isfile(r["file_path"]):
                if r["thumb_path"] and os.path.isfile(r["thumb_path"]):
                    try:
                        os.remove(r["thumb_path"])
                    except OSError:
                        pass
                conn.execute("DELETE FROM external_files WHERE id=?", (r["id"],))
        conn.commit()
        return True
    finally:
        conn.close()


def get_history(limit=50, category=None):
    conn = get_db()
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM download_history WHERE category=? ORDER BY created_at DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM download_history ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def clear_history():
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM download_history
               WHERE status='completed' AND file_path IS NOT NULL AND file_path != ''"""
        ).fetchall()
        for r in rows:
            if os.path.isfile(r["file_path"]):
                existing = conn.execute(
                    "SELECT id FROM external_files WHERE file_path=?", (r["file_path"],)
                ).fetchone()
                if not existing:
                    conn.execute(
                        """INSERT OR IGNORE INTO external_files
                           (file_path, file_name, category, folder_id, file_size, thumb_path)
                           VALUES (?, ?, ?, NULL, ?, ?)""",
                        (r["file_path"], r["title"] or "", r["category"],
                         r["file_size"] or 0, r["thumb_path"] or ""),
                    )
        # Aktif/kuyruktaki indirmelerin satirini SILME - yoksa indirme bitince
        # update_history() var olmayan bir id'yi guncellemeye calisir (sessiz
        # no-op olur) ve tamamlanan dosya artik hicbir yerde kayitli olmaz.
        conn.execute("DELETE FROM download_history WHERE status NOT IN ('pending', 'downloading')")
        conn.commit()
        return True
    finally:
        conn.close()


def add_folder(folder_path, category="uncategorized", label=""):
    conn = get_db()
    try:
        c = conn.execute(
            "INSERT OR IGNORE INTO external_folders (folder_path, category, label) VALUES (?, ?, ?)",
            (folder_path, category, label),
        )
        fid = c.lastrowid
        conn.commit()
        return fid
    finally:
        conn.close()


def remove_folder(folder_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT thumb_path FROM external_files WHERE folder_id=?", (folder_id,)
        ).fetchall()
        for r in rows:
            if r["thumb_path"] and os.path.isfile(r["thumb_path"]):
                try:
                    os.remove(r["thumb_path"])
                except OSError:
                    pass
        conn.execute("DELETE FROM external_files WHERE folder_id=?", (folder_id,))
        conn.execute("DELETE FROM external_folders WHERE id=?", (folder_id,))
        conn.commit()
    finally:
        conn.close()


def get_folders():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM external_folders ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_folder_scan(folder_id):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE external_folders SET last_scanned=datetime('now','localtime') WHERE id=?",
            (folder_id,),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_external_file(file_path, file_name, category, folder_id, file_size=0, duration=0, season=0, episode=0, year=0, quality="", thumb_path=""):
    conn = get_db()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO external_files
               (file_path, file_name, category, folder_id, file_size, duration, season, episode, year, quality, thumb_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_path, file_name, category, folder_id, file_size, duration, season, episode, year, quality, thumb_path),
        )
        conn.commit()
    finally:
        conn.close()


def clear_folder_files(folder_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT thumb_path FROM external_files WHERE folder_id=?", (folder_id,)
        ).fetchall()
        for r in rows:
            if r["thumb_path"] and os.path.isfile(r["thumb_path"]):
                try:
                    os.remove(r["thumb_path"])
                except OSError:
                    pass
        conn.execute("DELETE FROM external_files WHERE folder_id=?", (folder_id,))
        conn.commit()
    finally:
        conn.close()


def _filter_existing(videos):
    return [v for v in videos if os.path.isfile(v.get("file_path", ""))]


def get_category_videos(category, limit=200):
    conn = get_db()
    try:
        ext = conn.execute(
            """SELECT id, file_path, file_name, category, file_size, season, episode, year,
                      quality, added_at, thumb_path, folder_id, duration, 'external' as source
               FROM external_files WHERE category=? ORDER BY added_at DESC LIMIT ?""",
            (category, limit),
        ).fetchall()

        dl = conn.execute(
            """SELECT id, file_path, title as file_name, category, file_size, NULL as season,
                      NULL as episode, NULL as year, format_label as quality, completed_at as added_at,
                      COALESCE(thumb_path, '') as thumb_path, '' as folder_id, 0 as duration, 'download' as source
               FROM download_history WHERE category=? AND status='completed' AND file_path IS NOT NULL AND file_path != ''
               ORDER BY completed_at DESC LIMIT ?""",
            (category, limit),
        ).fetchall()
    finally:
        conn.close()

    result = [dict(r) for r in ext] + [dict(r) for r in dl]
    result = _filter_existing(result)
    result.sort(key=lambda x: x.get("added_at", ""), reverse=True)
    return result


def get_all_videos(limit=200):
    conn = get_db()
    try:
        ext = conn.execute(
            """SELECT id, file_path, file_name, category, file_size, season, episode, year,
                      quality, added_at, thumb_path, folder_id, duration, 'external' as source
               FROM external_files ORDER BY added_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        dl = conn.execute(
            """SELECT id, file_path, title as file_name, category, file_size, NULL as season,
                      NULL as episode, NULL as year, format_label as quality, completed_at as added_at,
                      COALESCE(thumb_path, '') as thumb_path, '' as folder_id, 0 as duration, 'download' as source
               FROM download_history WHERE status='completed' AND file_path IS NOT NULL AND file_path != ''
               ORDER BY completed_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    result = [dict(r) for r in ext] + [dict(r) for r in dl]
    result = _filter_existing(result)
    result.sort(key=lambda x: x.get("added_at", ""), reverse=True)
    return result


def get_stats():
    conn = get_db()
    try:
        ext_rows = conn.execute(
            "SELECT id, file_path, file_size FROM external_files"
        ).fetchall()
        dl_rows = conn.execute(
            "SELECT id, file_path, file_size FROM download_history WHERE status='completed' AND file_path IS NOT NULL AND file_path != ''"
        ).fetchall()

        ext_count = 0
        total_size = 0
        ext_cats = {}
        for r in ext_rows:
            if os.path.isfile(r["file_path"]):
                ext_count += 1
                total_size += r["file_size"] or 0
                cat = conn.execute(
                    "SELECT category FROM external_files WHERE id=?", (r["id"],)
                ).fetchone()
                if cat:
                    ext_cats[cat["category"]] = ext_cats.get(cat["category"], 0) + 1

        dl_count = 0
        dl_cats = {}
        for r in dl_rows:
            fp = r["file_path"] or ""
            if fp and os.path.isfile(fp):
                dl_count += 1
                total_size += r["file_size"] or 0
                cat = conn.execute(
                    "SELECT category FROM download_history WHERE id=?", (r["id"],)
                ).fetchone()
                if cat:
                    dl_cats[cat["category"]] = dl_cats.get(cat["category"], 0) + 1

        recent = conn.execute(
            "SELECT COUNT(*) as c FROM download_history WHERE status='completed'"
            " AND file_path IS NOT NULL AND file_path != ''"
            " AND completed_at > datetime('now','-7 days')"
        ).fetchone()["c"]
    finally:
        conn.close()

    return {
        "total_videos": ext_count + dl_count,
        "total_size": total_size,
        "total_size_gb": round(total_size / (1024**3), 2),
        "recent_downloads": recent,
        "categories": {
            "external": ext_cats,
            "downloaded": dl_cats,
        },
    }


init_db()
