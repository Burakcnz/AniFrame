import os
import sys
import json
import time
import signal
import threading
import subprocess
import webbrowser
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext
except ImportError:
    print("tkinter bulunamadi. Lutfen: pip install tk")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "server_panel.log")
SETTINGS_FILE = os.path.join(BASE_DIR, "panel_settings.json")

APP_NAME = "AniFrame Video Downloader"
PORT = 8000
RUN_CMD = [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", f"--port", str(PORT)]
INSTALL_STEPS = [
    [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
    [sys.executable, "-m", "playwright", "install", "chromium"],
]

BG = "#0a0a0f"
SURFACE = "#111118"
BORDER = "#1e1e2e"
TEXT = "#f1f5f9"
TEXT_MUTED = "#64748b"
ACCENT = "#8b5cf6"
ACCENT_GREEN = "#10b981"
DANGER = "#ef4444"


class ServerPanel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.minsize(800, 500)
        self.root.geometry("900x600")

        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        self.process = None
        self.lock = threading.Lock()
        self.running = False
        self._start_time = None
        self._browser_opened = False

        self._load_settings()

        self._build_header()
        self._build_action_bar()
        self._build_console()
        self._build_status_bar()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._log("AniFrame Server Panel baslatildi", "system")
        self._log(f"Calisma dizini: {BASE_DIR}", "system")
        self._log(f"Port: {PORT}", "system")

        if self.settings.get("auto_start", False):
            self.root.after(500, self.start_server)

    def _load_settings(self):
        self.settings = {}
        if os.path.isfile(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    self.settings = json.load(f)
            except Exception:
                pass

    def _save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except Exception:
            pass

    def _build_header(self):
        header = tk.Frame(self.root, bg=BG, height=60)
        header.pack(fill="x", padx=16, pady=(12, 0))
        header.pack_propagate(False)

        title = tk.Label(
            header, text=APP_NAME,
            fg=TEXT, bg=BG,
            font=("Segoe UI", 14, "bold")
        )
        title.pack(side="left")

        self.status_indicator = tk.Canvas(header, bg=BG, width=20, height=20, highlightthickness=0)
        self.status_indicator.pack(side="right", padx=(0, 8))
        self._draw_circle(self.status_indicator, DANGER)

        self.port_label = tk.Label(
            header, text=f"Port: {PORT}",
            fg=TEXT_MUTED, bg=BG,
            font=("Segoe UI", 10)
        )
        self.port_label.pack(side="right", padx=8)

    def _draw_circle(self, canvas, color):
        canvas.delete("all")
        canvas.create_oval(3, 3, 17, 17, fill=color, outline="")

    def _build_action_bar(self):
        bar = tk.Frame(self.root, bg=SURFACE, height=48)
        bar.pack(fill="x", padx=16, pady=8)

        buttons = [
            ("BAŞLAT", self.start_server, ACCENT_GREEN),
            ("DURDUR", self.stop_server, DANGER),
            ("YENİDEN BAŞLAT", self.restart_server, "#f59e0b"),
            ("TARAYICI", self.open_browser, ACCENT),
            ("KURULUM", self.run_setup, "#60a5fa"),
            ("TEMİZLE", self.clear_log, TEXT_MUTED),
        ]

        for text, cmd, _color in buttons:
            btn = tk.Button(
                bar, text=text, command=cmd,
                bg=SURFACE, fg=TEXT,
                font=("Segoe UI", 9, "bold"),
                relief="flat", borderwidth=1,
                padx=16, pady=4,
                activebackground="#1e1e2e", activeforeground=TEXT,
                cursor="hand2"
            )
            btn.pack(side="left", padx=4, pady=6)
            btn.bind("<Enter>", lambda e, b=btn, c=ACCENT: b.configure(fg=c))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(fg=TEXT))
            setattr(self, f"btn_{text.split()[0].lower()}", btn)

    def _build_console(self):
        console_frame = tk.Frame(self.root, bg=BG)
        console_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self.console = scrolledtext.ScrolledText(
            console_frame,
            bg=SURFACE, fg=TEXT,
            insertbackground=TEXT,
            font=("Consolas", 10),
            relief="flat", borderwidth=1,
            wrap="word",
        )
        self.console.pack(fill="both", expand=True)

        self.console.tag_configure("error", foreground=DANGER)
        self.console.tag_configure("success", foreground=ACCENT_GREEN)
        self.console.tag_configure("system", foreground=ACCENT, font=("Consolas", 10, "italic"))
        self.console.tag_configure("info", foreground=TEXT_MUTED)
        self.console.tag_configure("download", foreground="#3b82f6")
        self.console.tag_configure("ytdl", foreground="#06b6d4")

    def _build_status_bar(self):
        bar = tk.Frame(self.root, bg=SURFACE, height=28)
        bar.pack(fill="x", padx=16, pady=(0, 12))

        self.status_text = tk.Label(
            bar, text="● Durduruldu",
            fg=TEXT_MUTED, bg=SURFACE,
            font=("Segoe UI", 9)
        )
        self.status_text.pack(side="left", padx=8)

        self.uptime_label = tk.Label(
            bar, text="",
            fg=TEXT_MUTED, bg=SURFACE,
            font=("Segoe UI", 9)
        )
        self.uptime_label.pack(side="right", padx=8)

    def _log(self, message, tag="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"

        def _write():
            self.console.insert("end", line, tag)
            self.console.see("end")

        if threading.current_thread() is threading.main_thread():
            _write()
        else:
            self.root.after(0, _write)

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
            if os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
                backup = LOG_FILE.replace(".log", f"_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
                os.rename(LOG_FILE, backup)
                backups = sorted(
                    f for f in os.listdir(BASE_DIR)
                    if f.startswith("server_panel_") and f.endswith(".log")
                )
                for old in backups[:-2]:
                    try:
                        os.remove(os.path.join(BASE_DIR, old))
                    except OSError:
                        pass
        except Exception:
            pass

    def start_server(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self._log("Sunucu zaten calisiyor", "info")
                return

            self._kill_port()
            self._log("Sunucu baslatiliyor...", "system")

            si = None
            if sys.platform == "win32":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = subprocess.SW_HIDE

            try:
                self.process = subprocess.Popen(
                    RUN_CMD,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=BASE_DIR,
                    startupinfo=si,
                    preexec_fn=os.setsid if sys.platform != "win32" else None,
                )
            except Exception as e:
                self._log(f"Sunucu baslatilamadi: {e}", "error")
                return

            self._start_time = time.time()
            self.running = True
            self._update_status("running")
            self._log(f"Sunucu PID: {self.process.pid}", "info")

        t = threading.Thread(target=self._stream_output, daemon=True)
        t.start()

        t2 = threading.Thread(target=self._monitor_process, daemon=True)
        t2.start()

    def _stream_output(self):
        if not self.process:
            return
        try:
            for line in iter(self.process.stdout.readline, ""):
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                # Akıllı tag tespiti
                tag = "info"
                if "[HATA]" in line or "ERROR" in line or "Traceback" in line:
                    tag = "error"
                elif "[TAMAMLANDI]" in line:
                    tag = "success"
                elif "[DL]" in line:
                    tag = "download"
                elif "[YTDL]" in line or "[NATIVE]" in line:
                    tag = "ytdl"
                elif "Sunucu" in line and ("hazir" in line or "baslatildi" in line):
                    tag = "success"
                self._log(line, tag)

                if any(kw in line.lower() for kw in ["uvicorn running", "started server", "application startup"]):
                    self._log(f"Sunucu {PORT} portunda hazir!", "success")
                    if not self._browser_opened:
                        self._browser_opened = True
                        self.root.after(800, lambda: webbrowser.open(f"http://localhost:{PORT}"))
        except Exception:
            pass

    def _monitor_process(self):
        if not self.process:
            return
        self.process.wait()
        with self.lock:
            self.running = False
            self._start_time = None
        self.root.after(0, self._update_status, "stopped")
        code = self.process.returncode
        if code != 0 and code is not None:
            self._log(f"Sunucu beklenmedik sekilde durdu (kod: {code})", "error")
        else:
            self._log("Sunucu durduruldu", "info")

    def stop_server(self):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                self._log("Sunucu zaten durmus", "info")
                return

            self._log("Sunucu durduruluyor...", "system")
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                                   capture_output=True, startupinfo=self._hide_console())
                else:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except Exception as e:
                self._log(f"Durdurma hatasi: {e}", "error")

            self.process = None
            self.running = False
            self._start_time = None
            self._browser_opened = False
            self._update_status("stopped")
            self._log("Sunucu durduruldu", "success")

    def restart_server(self):
        self._log("Sunucu yeniden baslatiliyor...", "system")
        self.stop_server()
        self.root.after(1000, self.start_server)

    def open_browser(self):
        url = f"http://localhost:{PORT}"
        self._log(f"Tarayici aciliyor: {url}", "system")
        webbrowser.open(url)

    def run_setup(self):
        self._log("Kurulum basliyor...", "system")
        for step in INSTALL_STEPS:
            cmd_str = " ".join(step)
            self._log(f"> {cmd_str}", "info")

            si = None
            if sys.platform == "win32":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = subprocess.SW_HIDE

            try:
                result = subprocess.run(step, capture_output=True, text=True, cwd=BASE_DIR,
                                        startupinfo=si, timeout=300)
                if result.returncode == 0:
                    self._log(f"  Basarili (kod: {result.returncode})", "success")
                else:
                    self._log(f"  Hata (kod: {result.returncode}): {result.stderr[:200]}", "error")
            except Exception as e:
                self._log(f"  Hata: {e}", "error")

        self._log("Kurulum tamamlandi", "success")

    def clear_log(self):
        self.console.delete("1.0", "end")
        self._log("Konsol temizlendi", "system")

    def _update_status(self, state):
        if state == "running":
            self._draw_circle(self.status_indicator, ACCENT_GREEN)
            self.status_text.configure(text="● Calisiyor", fg=ACCENT_GREEN)
            self._start_uptime_timer()
        elif state == "starting":
            self._draw_circle(self.status_indicator, "#f59e0b")
            self.status_text.configure(text="● Baslatiliyor", fg="#f59e0b")
        else:
            self._draw_circle(self.status_indicator, DANGER)
            self.status_text.configure(text="● Durduruldu", fg=DANGER)
            self.uptime_label.configure(text="")

    def _start_uptime_timer(self):
        def update():
            if self.running and self._start_time:
                elapsed = int(time.time() - self._start_time)
                h = elapsed // 3600
                m = (elapsed % 3600) // 60
                s = elapsed % 60
                self.uptime_label.configure(text=f"Sure: {h:02d}:{m:02d}:{s:02d}")
                self.root.after(1000, update)

        self.root.after(1000, update)

    def _kill_port(self):
        if sys.platform != "win32":
            return
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            result = s.connect_ex(("127.0.0.1", PORT))
            s.close()

            if result == 0:
                self._log(f"Port {PORT} kullanimda, temizleniyor...", "info")
                subprocess.run(
                    f'for /f "tokens=5" %a in (\'netstat -ano ^| findstr :{PORT}\') do taskkill /F /PID %a',
                    shell=True, capture_output=True,
                    startupinfo=self._hide_console()
                )
        except Exception:
            pass

    def _hide_console(self):
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            return si
        return None

    def _on_close(self):
        self.stop_server()
        self._save_settings()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ServerPanel().run()
