"""
Universal Video Downloader  —  v1
Designed by Youssef Alaa Hamada

Changes in v1:
  - NO ffmpeg dependency (completely removed)
  - GitHub-based Check for Updates system (Settings panel)
  - Auto update check on launch (after 4 seconds)
  - Shows changelog from GitHub release notes
  - packaging.version for smart version comparison
  - ffmpeg check cached at startup (no repeated subprocess calls)
"""

import ctypes, os, platform, subprocess, threading, time, json, queue, webbrowser
import tkinter as tk
from tkinter import filedialog
from datetime import datetime
import io, sys, urllib.request

# ── Version & Update ─────────────────────────────────────────────────────────
APP_VERSION  = "5.0.0"
GITHUB_API   = "https://api.github.com/repos/yo918/Universal-Video-Downloader/releases/latest"

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Youssef.UniversalVideoDownloader.v1")
    except Exception:
        pass

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES, DND_TEXT
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

import customtkinter as ctk
from yt_dlp import YoutubeDL

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from win10toast import ToastNotifier
    TOAST_AVAILABLE = True
except ImportError:
    TOAST_AVAILABLE = False

try:
    from packaging import version as pkg_version
    PACKAGING_AVAILABLE = True
except ImportError:
    PACKAGING_AVAILABLE = False

try:
    import requests as _requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# =============================================================================
#  Update checker (runs in background thread, result posted to UI thread)
# =============================================================================
def check_for_updates():
    """
    Returns (has_update: bool, latest_version: str|None,
             download_url: str|None, changelog: str|None)
    Uses urllib so no extra dependency — requests optional.
    """
    try:
        req = urllib.request.Request(
            GITHUB_API,
            headers={"User-Agent": "UniversalVideoDownloader/5.0",
                     "Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())

        latest_str  = data.get("tag_name", "0.0.0").lstrip("v")
        download_url = data.get("html_url", "")
        changelog    = data.get("body", "").strip() or "No changelog provided."

        if PACKAGING_AVAILABLE:
            has_update = pkg_version.parse(latest_str) > pkg_version.parse(APP_VERSION)
        else:
            has_update = latest_str != APP_VERSION

        return has_update, latest_str, download_url, changelog

    except Exception as e:
        return False, None, None, str(e)


# =============================================================================
#  Taskbar Progress  (Windows only)
# =============================================================================
class TaskbarProgress:
    NOPROGRESS    = 0x0
    INDETERMINATE = 0x1
    NORMAL        = 0x2
    ERROR         = 0x4
    PAUSED        = 0x8

    def __init__(self):
        self._ok = False; self._iface = None; self._hwnd = 0
        if not IS_WINDOWS: return
        try:
            ole32 = ctypes.windll.ole32
            hr = ole32.CoInitializeEx(None, 0x0)
            if hr == 0x80010106: ole32.CoInitializeEx(None, 0x2)
            CLSID = bytes([0x19,0xA8,0xFC,0x56,0x5E,0x00,0xCE,0x11,
                           0xA3,0xEF,0x00,0xAA,0x00,0x60,0x6B,0x2A])
            IID   = bytes([0x91,0xFB,0x1A,0xEA,0x28,0x9E,0x89,0x42,
                           0xA7,0x51,0xFD,0xBC,0xD8,0x8C,0x2A,0x50])
            ppv = ctypes.c_void_p()
            hr2 = ole32.CoCreateInstance((ctypes.c_byte*16)(*CLSID), None, 1,
                                          (ctypes.c_byte*16)(*IID), ctypes.byref(ppv))
            if hr2 != 0 or not ppv.value: return
            vtbl   = ctypes.cast(ppv.value, ctypes.POINTER(ctypes.c_void_p))
            HrInit = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtbl[3])
            if HrInit(ppv.value) != 0: return
            self._iface = ppv; self._ok = True
        except Exception as e:
            print(f"[TaskbarProgress.init] {e}")

    def attach(self, hwnd): self._hwnd = hwnd

    def set(self, state, value=0.0, total=100.0):
        if not self._ok or not self._hwnd or not self._iface: return
        try:
            iv   = self._iface.value
            hwnd = ctypes.c_void_p(self._hwnd)
            vtbl = ctypes.cast(iv, ctypes.POINTER(ctypes.c_void_p))
            ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p,
                               ctypes.c_void_p, ctypes.c_int)(vtbl[10])(iv, hwnd, state)
            if state in (self.NORMAL, self.PAUSED, self.ERROR):
                ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_ulonglong, ctypes.c_ulonglong)(vtbl[9])(
                    iv, hwnd, max(0, int(value)), max(1, int(total)))
        except Exception as e:
            print(f"[TaskbarProgress.set] {e}")

    def reset(self): self.set(self.NOPROGRESS)


def _get_toplevel_hwnd(widget):
    if not IS_WINDOWS: return 0
    try:
        hwnd = widget.winfo_id()
        GWL_STYLE = -16; WS_CAPTION = 0x00C00000
        u32 = ctypes.windll.user32
        for _ in range(10):
            if u32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CAPTION: return hwnd
            p = u32.GetParent(hwnd)
            if not p: break
            hwnd = p
        return hwnd
    except: return 0


# =============================================================================
#  Strings
# =============================================================================
STRINGS = {
    "title":              "Universal Video Downloader",
    "subtitle":           "Designed by Youssef Alaa Hamada Hashem Ali",
    "url_placeholder":    "Paste video or playlist URL…",
    "download_btn":       "Download",
    "quality":            "Quality",
    "folder":             "Save to",
    "browse":             "Browse",
    "open_folder":        "Open Folder",
    "pause":              "⏸  Pause",
    "resume":             "▶  Resume",
    "cancel":             "✕  Cancel",
    "history_tab":        "🕓  History",
    "settings_tab":       "⚙  Settings",
    "clear_history":      "Clear All",
    "status_idle":        "Ready to download",
    "status_preparing":   "Preparing…",
    "status_downloading": "Downloading",
    "status_completed":   "✓  Complete",
    "status_failed":      "✗  Failed",
    "status_cancelled":   "Cancelled",
    "status_paused":      "Paused",
    "notif_done_title":   "Download Complete",
    "notif_done_msg":     "Saved to: {folder}",
    "notif_pl_title":     "Playlist Complete",
    "notif_pl_msg":       "{n} videos downloaded\nSaved to: {folder}",
    "err_no_url":         "Please enter a URL",
    "err_invalid_url":    "URL must start with http:// or https://",
    "err_in_progress":    "A download is already in progress",
    "err_network":        "Network error. Check your connection!",
    "err_unavailable":    "Video is unavailable or private.",
    "err_copyright":      "Video is copyright protected.",
    "err_age":            "Age-restricted video.",
    "err_generic":        "Download failed",
    "no_history":         "No downloads yet",
    "fetching":           "Fetching info…",
    "title_lbl":          "Title",
    "duration_lbl":       "Duration",
    "pl_progress":        "Playlist: {done} / {total}",
    "pl_badge":           "{n} videos",
    "s_appearance":       "Appearance",
    "s_dark":             "🌙  Dark",
    "s_light":            "☀️  Light",
    "s_ask_folder":       "Ask where to save every time",
    "s_ask_folder_sub":   "A folder picker opens before each download",
    "s_default_folder":   "Default save folder",
    "s_quality_default":  "Default quality",
    "s_updates":          "Updates",
    "s_check_updates":    "🔄  Check for Updates",
    "s_checking":         "Checking…",
    "s_up_to_date":       "✓  You're up to date  (v{v})",
    "s_update_found":     "🆕  v{v} available — click to download",
    "s_update_error":     "⚠  Could not check for updates",
    "h_open_file":        "▶ Play",
    "h_open_folder":      "📂 Folder",
    "drop_hint":          "or drag & drop a URL here",
    "version_lbl":        "Version  v{v}",
}

THUMB_W = 320
THUMB_H = 180


# =============================================================================
#  DownloadTask
# =============================================================================
class DownloadTask:
    def __init__(self, url, ydl_opts, ev_queue):
        self.url = url; self.ydl_opts = ydl_opts; self._q = ev_queue
        self._pause = threading.Event(); self._pause.set()
        self._cancel = False; self.is_paused = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):  self._thread.start()
    def pause(self):  self._pause.clear(); self.is_paused = True
    def resume(self): self._pause.set();   self.is_paused = False
    def cancel(self): self._cancel = True; self._pause.set()

    def _run(self):
        def hook(d):
            if self._cancel: raise Exception("Cancelled")
            while not self._pause.is_set():
                time.sleep(0.05)
                if self._cancel: raise Exception("Cancelled")
            evt = dict(d); evt["_type"] = d["status"]; self._q.put(evt)
        opts = dict(self.ydl_opts); opts["progress_hooks"] = [hook]
        try:
            with YoutubeDL(opts) as ydl: ydl.download([self.url])
        except Exception as e:
            if not self._cancel: self._q.put({"_type": "error", "msg": str(e)})


# =============================================================================
#  App
# =============================================================================
class App(ctk.CTk):
    SETTINGS_FILE = "downloader_settings.json"
    HISTORY_FILE  = "downloader_history.json"

    DARK = dict(
        bg="#0b0c11", panel="#11121a", card="#181924", card2="#1e2030",
        border="#252840", accent="#3d6ef7", accent2="#6b93ff",
        accent_dim="#1a2a6e", text="#e4e6f0", sub="#8890b0",
        dim="#303450", danger="#f04040", success="#3fcf8e",
    )
    LIGHT = dict(
        bg="#eef0f8", panel="#ffffff", card="#f0f2fc", card2="#e2e6f5",
        border="#c0c6de", accent="#2d5be3", accent2="#1a3fbb",
        accent_dim="#dce5ff", text="#0d1020", sub="#3a4070",
        dim="#7880a8", danger="#b91c1c", success="#15803d",
    )

    def __init__(self):
        super().__init__()
        if DND_AVAILABLE:
            try: TkinterDnD._require(self)
            except Exception as e: print(f"[DnD] {e}")

        self._load_settings()
        self._load_history()

        self.folder     = self._s.get("folder", os.path.expanduser("~/Downloads"))
        self.dark_mode  = self._s.get("dark_mode", True)
        self.ask_folder = self._s.get("ask_folder", False)
        self._q_var     = tk.StringVar(value=self._s.get("quality", "720p"))

        self.task = None; self._dl_q = queue.Queue()
        self.pl_count = 0; self.pl_total = 1
        self._fetch_cancel = threading.Event()
        self._thumb_img = None; self._sidebar_vis = None
        self._cur_folder = self.folder; self._url_after = None
        self._dl_locked = False
        self._thumb_loaded = False

        # Update state
        self._update_url = None
        self._update_btn = None        # reference set after build

        self.toaster  = ToastNotifier() if TOAST_AVAILABLE else None
        self._taskbar = TaskbarProgress()

        ctk.set_appearance_mode("Dark" if self.dark_mode else "Light")
        ctk.set_default_color_theme("blue")

        self.title(self.T("title"))
        icon_path = os.path.join(self._get_base_dir(), "icon3.ico")
        if os.path.isfile(icon_path):
            self.iconbitmap(icon_path)
        self.geometry("1020x700"); self.minsize(860, 580)
        self.resizable(True, True)

        self._build_ui()
        self.after(800,  self._attach_taskbar)
        self.after(4000, self._auto_check_updates)   # silent auto-check
        self._poll_queue()

        self.bind_all("<Control-v>",       self._global_paste_event, add="+")
        self.bind_all("<Control-V>",       self._global_paste_event, add="+")
        self.bind_all("<Control-KeyPress>", self._ctrl_keypress,      add="+")

    # ── helpers ───────────────────────────────────────────────────────────────
    def T(self, k, **kw):
        s = STRINGS.get(k, k); return s.format(**kw) if kw else s

    @property
    def C(self): return self.DARK if self.dark_mode else self.LIGHT

    def _get_base_dir(self):
        return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.abspath(__file__))

    # ── custom dialog ─────────────────────────────────────────────────────────
    def _show_msg_box(self, title, msg, is_error=False):
        dlg = ctk.CTkToplevel(self)
        dlg.title(title); dlg.geometry("500x320"); dlg.transient(self); dlg.grab_set()
        dlg.configure(fg_color=self.C["bg"])
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width()  // 2) - 250
        y = self.winfo_y() + (self.winfo_height() // 2) - 160
        dlg.geometry(f"+{x}+{y}")
        color = self.C["danger"] if is_error else self.C["accent"]
        icon  = "✖" if is_error else "⚠"
        hdr = ctk.CTkFrame(dlg, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 10))
        ctk.CTkLabel(hdr, text=icon, font=ctk.CTkFont("Arial", 24, "bold"),
                     text_color=color).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(hdr, text=title, font=ctk.CTkFont("Tajawal", 16, "bold"),
                     text_color=self.C["text"]).pack(side="left")
        txt = ctk.CTkTextbox(dlg, font=ctk.CTkFont("Consolas", 12),
                             fg_color=self.C["card"], border_color=self.C["border"],
                             border_width=1, text_color=self.C["sub"], wrap="word")
        txt.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        txt.insert("0.0", msg); txt.configure(state="disabled")
        ctk.CTkButton(dlg, text="OK", width=120, height=36,
                      font=ctk.CTkFont("Tajawal", 13, "bold"),
                      fg_color=self.C["card"], hover_color=self.C["card2"],
                      border_color=self.C["border"], border_width=1,
                      text_color=self.C["text"], command=dlg.destroy).pack(pady=(0, 20))

    # ── Update dialog (richer, shows changelog) ───────────────────────────────
    def _show_update_dialog(self, latest_ver, download_url, changelog):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Update Available"); dlg.geometry("540x420")
        dlg.transient(self); dlg.grab_set()
        dlg.configure(fg_color=self.C["bg"])
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width()  // 2) - 270
        y = self.winfo_y() + (self.winfo_height() // 2) - 210
        dlg.geometry(f"+{x}+{y}")

        # Header
        hdr = ctk.CTkFrame(dlg, fg_color=self.C["card"], corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="🆕  New Update Available",
                     font=ctk.CTkFont("Tajawal", 17, "bold"),
                     text_color=self.C["accent2"]).pack(side="left", padx=20, pady=16)
        ver_badge = ctk.CTkLabel(hdr,
                                 text=f"v{latest_ver}",
                                 font=ctk.CTkFont("Tajawal", 13, "bold"),
                                 fg_color=self.C["accent_dim"],
                                 text_color=self.C["accent2"],
                                 corner_radius=6, padx=10, pady=4)
        ver_badge.pack(side="right", padx=20)

        # Current vs new
        info = ctk.CTkFrame(dlg, fg_color="transparent")
        info.pack(fill="x", padx=20, pady=(14, 6))
        ctk.CTkLabel(info,
                     text=f"Current: v{APP_VERSION}   →   Latest: v{latest_ver}",
                     font=ctk.CTkFont("Tajawal", 12),
                     text_color=self.C["sub"]).pack(anchor="w")

        # Changelog
        ctk.CTkLabel(dlg, text="What's new:",
                     font=ctk.CTkFont("Tajawal", 12, "bold"),
                     text_color=self.C["text"]).pack(anchor="w", padx=20, pady=(8, 2))
        cl_box = ctk.CTkTextbox(dlg, font=ctk.CTkFont("Consolas", 11),
                                fg_color=self.C["card"], border_color=self.C["border"],
                                border_width=1, text_color=self.C["sub"], wrap="word",
                                height=140)
        cl_box.pack(fill="x", padx=20, pady=(0, 14))
        cl_box.insert("0.0", changelog); cl_box.configure(state="disabled")

        # Buttons
        bf = ctk.CTkFrame(dlg, fg_color="transparent")
        bf.pack(fill="x", padx=20, pady=(0, 20))

        def _open_and_close():
            webbrowser.open(download_url)
            dlg.destroy()

        ctk.CTkButton(bf, text="⬇  Download Update", height=42,
                      font=ctk.CTkFont("Tajawal", 13, "bold"),
                      fg_color=self.C["accent"], hover_color=self.C["accent2"],
                      text_color="#fff", command=_open_and_close).pack(
                          side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(bf, text="Later", height=42, width=90,
                      font=ctk.CTkFont("Tajawal", 13),
                      fg_color=self.C["card"], hover_color=self.C["card2"],
                      border_color=self.C["border"], border_width=1,
                      text_color=self.C["sub"], command=dlg.destroy).pack(side="right")

    # ── Update logic ──────────────────────────────────────────────────────────
    def _auto_check_updates(self):
        """Silent background check — only shows dialog if update found."""
        threading.Thread(target=self._update_worker, args=(False,), daemon=True).start()

    def _manual_check_updates(self):
        """User clicked the button — always shows result."""
        if self._update_btn:
            self._update_btn.configure(text=self.T("s_checking"), state="disabled")
        threading.Thread(target=self._update_worker, args=(True,), daemon=True).start()

    def _update_worker(self, manual: bool):
        has_update, latest, url, changelog = check_for_updates()
        self.after(0, lambda: self._update_done(manual, has_update, latest, url, changelog))

    def _update_done(self, manual, has_update, latest, url, changelog):
        if self._update_btn:
            if has_update and url:
                # ✅ تحديث موجود — الزرار شغال ويفتح صفحة التحميل
                self._update_btn.configure(
                    text=self.T("s_update_found", v=latest),
                    state="normal",
                    fg_color=self.C["accent_dim"],
                    text_color=self.C["accent2"],
                    command=lambda: webbrowser.open(url))
                self._update_url = url
            elif latest:
                # ✅ أحدث نسخة — الزرار موجود بس مش بيتضغط (frozen)
                self._update_btn.configure(
                    text=self.T("s_up_to_date", v=latest),
                    state="disabled",
                    fg_color=self.C["card"],
                    text_color=self.C["success"])
                self._update_url = None
            else:
                # ⚠️ فشل الاتصال — الزرار موجود بس مش بيتضغط (frozen)
                self._update_btn.configure(
                    text=self.T("s_update_error"),
                    state="disabled",
                    fg_color=self.C["card"],
                    text_color=self.C["dim"])
                self._update_url = None

        if has_update and url:
            self._show_update_dialog(latest, url, changelog or "")
        elif manual and not has_update and latest:
            self._show_msg_box("Up to date",
                               f"You are already on the latest version (v{latest}).", False)
        elif manual and not latest:
            self._show_msg_box("Update check failed",
                               f"Could not connect to GitHub.\n\nDetails: {changelog}", True)

    # ── taskbar ───────────────────────────────────────────────────────────────
    def _attach_taskbar(self):
        if not IS_WINDOWS: return
        self.update_idletasks()
        hwnd = _get_toplevel_hwnd(self)
        if hwnd: self._taskbar.attach(hwnd)
        else: self.after(1500, self._attach_taskbar)

    # ── settings / history ────────────────────────────────────────────────────
    def _load_settings(self):
        try:
            with open(self.SETTINGS_FILE, "r", encoding="utf-8") as f:
                self._s = json.load(f)
        except: self._s = {}

    def _save_settings(self, *_):
        self._s.update(folder=self.folder, dark_mode=self.dark_mode,
                       ask_folder=self.ask_folder, quality=self._q_var.get())
        try:
            with open(self.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._s, f, indent=2, ensure_ascii=False)
        except: pass

    def _load_history(self):
        try:
            with open(self.HISTORY_FILE, "r", encoding="utf-8") as f:
                self._history = json.load(f)
        except: self._history = []

    def _save_history_file(self):
        try:
            with open(self.HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._history[-300:], f, indent=2, ensure_ascii=False)
        except: pass

    def _add_history(self, title, url, folder, filepath=""):
        self._history.insert(0, dict(title=title, url=url, folder=folder,
            filepath=filepath, date=datetime.now().strftime("%Y-%m-%d  %H:%M")))
        self._save_history_file()
        if self._sidebar_vis == "history": self._refresh_history()

    # ── queue ─────────────────────────────────────────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                evt = self._dl_q.get_nowait(); t = evt.get("_type", "")
                if   t == "downloading": self._upd_progress(evt)
                elif t == "finished":    self._handle_complete(evt)
                elif t == "error":       self._handle_error(evt["msg"])
        except queue.Empty: pass
        self.after(80, self._poll_queue)

    # =========================================================================
    #  UI BUILD
    # =========================================================================
    def _build_ui(self):
        self.configure(fg_color=self.C["bg"])
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=0)
        self._build_topbar()
        self._build_main()
        self._build_sidebar_frame()
        self._setup_dnd()
        self._apply_lang()

    def _destroy_ui(self):
        for child in list(self.winfo_children()): child.destroy()

    def _apply_theme(self, dark: bool):
        self.dark_mode = dark; self._save_settings()
        ctk.set_appearance_mode("Dark" if dark else "Light")
        url_text    = self._get_url()
        sidebar_vis = self._sidebar_vis
        prog_pct    = getattr(self, "_prog_pct", 0.0)
        ask_var_val = getattr(self, "_ask_var", None)
        ask_val     = ask_var_val.get() if ask_var_val else self.ask_folder
        self._destroy_ui(); self._sidebar_vis = None
        self._dl_locked = False; self._thumb_loaded = False
        self._update_btn = None
        self._build_ui()
        if url_text:
            self._url_entry.delete(0, "end"); self._url_entry.insert(0, url_text)
        self._prog_pct = prog_pct; self._draw_progress()
        self.ask_folder = ask_val
        if hasattr(self, "_ask_var"): self._ask_var.set(ask_val)
        if sidebar_vis: self._show_sidebar(sidebar_vis)

    def _build_topbar(self):
        C = self.C
        bar = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0, height=58)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False); bar.grid_columnconfigure(1, weight=1)
        tf = ctk.CTkFrame(bar, fg_color="transparent"); tf.grid(row=0, column=0, sticky="w", padx=22)
        self._app_title = ctk.CTkLabel(tf, text="",
            font=ctk.CTkFont("Tajawal", 24, weight="bold"), text_color=C["text"])
        self._app_title.pack(side="left")
        self._app_sub = ctk.CTkLabel(tf, text="",
            font=ctk.CTkFont("Tajawal", 18, "bold"), text_color=C["sub"])
        self._app_sub.pack(side="left")
        rf = ctk.CTkFrame(bar, fg_color="transparent"); rf.grid(row=0, column=2, sticky="e", padx=16)
        bkw = dict(height=32, corner_radius=8, font=ctk.CTkFont("Tajawal", 11, weight="bold"),
                   fg_color=C["card"], hover_color=C["card2"],
                   text_color=C["sub"], border_width=1, border_color=C["border"])
        self._hist_tab_btn = ctk.CTkButton(rf, text="", width=130,
            command=lambda: self._toggle_sidebar("history"), **bkw)
        self._hist_tab_btn.pack(side="right", padx=(6, 0))
        self._sett_tab_btn = ctk.CTkButton(rf, text="", width=130,
            command=lambda: self._toggle_sidebar("settings"), **bkw)
        self._sett_tab_btn.pack(side="right")
        tk.Frame(self, bg=C["border"], height=1).grid(row=0, column=0, columnspan=2, sticky="sew")

    def _build_main(self):
        C = self.C; PAD = 32
        self._main = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0)
        self._main.grid(row=1, column=0, sticky="nsew")
        self._main.grid_columnconfigure(0, weight=1)
        self._main.grid_rowconfigure(9, weight=1)

        uf = ctk.CTkFrame(self._main, fg_color="transparent")
        uf.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(24, 0))
        uf.grid_columnconfigure(0, weight=1)
        self._url_entry = ctk.CTkEntry(
            uf, font=ctk.CTkFont("Tajawal", 13), height=48, corner_radius=10,
            fg_color=C["card"], border_color=C["border"], border_width=1,
            text_color=C["text"], placeholder_text=self.T("url_placeholder"),
            placeholder_text_color=C["sub"], justify="left")
        self._url_entry.grid(row=0, column=0, sticky="ew")
        self._url_entry.bind("<KeyRelease>", self._on_key)
        self._url_entry.bind("<FocusOut>",   self._trigger_fetch)
        self._url_entry.bind("<Button-3>",   self._show_ctx_menu)
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label="Paste", command=self._paste_url)
        self._ctx_menu.add_command(label="Clear", command=lambda: self._url_entry.delete(0, "end"))
        self._drop_lbl = ctk.CTkLabel(uf, text="", font=ctk.CTkFont("Tajawal", 9), text_color=C["dim"])
        self._drop_lbl.grid(row=1, column=0, sticky="e", pady=(2, 0))

        qf = ctk.CTkFrame(self._main, fg_color="transparent")
        qf.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(10, 0))
        qf.grid_columnconfigure(1, weight=1)
        qc = ctk.CTkFrame(qf, fg_color=C["card"], corner_radius=8, border_width=1, border_color=C["border"])
        qc.grid(row=0, column=0, padx=(0, 8))
        self._qual_lbl = ctk.CTkLabel(qc, text="", font=ctk.CTkFont("Tajawal", 12), text_color=C["sub"])
        self._qual_lbl.pack(side="left", padx=(12, 4))
        self._qual_menu = ctk.CTkOptionMenu(
            qc, variable=self._q_var, values=["480p", "720p", "1080p", "Best", "Audio"],
            width=100, height=34, font=ctk.CTkFont("Tajawal", 12),
            fg_color=C["card"], button_color=C["card"], button_hover_color=C["card2"],
            text_color=C["text"], dropdown_fg_color=C["card"], dropdown_text_color=C["text"],
            dropdown_hover_color=C["card2"], command=self._save_settings)
        self._qual_menu.pack(side="left", padx=(0, 6), pady=6)
        fc = ctk.CTkFrame(qf, fg_color=C["card"], corner_radius=8, border_width=1, border_color=C["border"])
        fc.grid(row=0, column=1, sticky="ew"); fc.grid_columnconfigure(0, weight=1)
        self._folder_lbl = ctk.CTkLabel(fc, text=self._short_path(self.folder),
            font=ctk.CTkFont("Tajawal", 11), text_color=C["sub"], anchor="w")
        self._folder_lbl.grid(row=0, column=0, sticky="ew", padx=(12, 4))
        self._browse_btn = ctk.CTkButton(fc, text="", width=80, height=30,
            font=ctk.CTkFont("Tajawal", 11), fg_color=C["accent_dim"], hover_color=C["accent"],
            text_color=C["accent2"], corner_radius=6, command=self._change_folder)
        self._browse_btn.grid(row=0, column=1, padx=6, pady=5)

        ic = ctk.CTkFrame(self._main, fg_color=C["card"], corner_radius=12, border_width=1, border_color=C["border"])
        ic.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(14, 0))
        ic.grid_columnconfigure(1, weight=1)
        thumb_container = ctk.CTkFrame(ic, fg_color=C["card2"], corner_radius=10,
                                        width=THUMB_W, height=THUMB_H)
        thumb_container.grid(row=0, column=0, padx=16, pady=16, rowspan=2)
        thumb_container.grid_propagate(False); thumb_container.pack_propagate(False)
        self._thumb_lbl = ctk.CTkLabel(thumb_container, text="🎬",
            font=ctk.CTkFont("Tajawal", 48), text_color=C["dim"], fg_color="transparent")
        self._thumb_lbl.place(relx=0.5, rely=0.5, anchor="center")
        tr = ctk.CTkFrame(ic, fg_color="transparent")
        tr.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(20, 4))
        tr.grid_columnconfigure(1, weight=1)
        self._info_title_key = ctk.CTkLabel(tr, text="", font=ctk.CTkFont("Tajawal", 16, weight="bold"),
            text_color=C["accent2"], width=68, anchor="w")
        self._info_title_key.grid(row=0, column=0, sticky="w")
        self._vid_title = ctk.CTkLabel(tr, text="", font=ctk.CTkFont("Tajawal", 15, weight="bold"),
            text_color=C["text"], anchor="w", wraplength=360, justify="left")
        self._vid_title.grid(row=0, column=1, sticky="ew")
        dr = ctk.CTkFrame(ic, fg_color="transparent")
        dr.grid(row=1, column=1, sticky="new", padx=(0, 16), pady=(0, 16))
        self._info_dur_key = ctk.CTkLabel(dr, text="", font=ctk.CTkFont("Tajawal", 16, weight="bold"),
            text_color=C["accent2"], width=68, anchor="w")
        self._info_dur_key.pack(side="left")
        self._vid_dur = ctk.CTkLabel(dr, text="", font=ctk.CTkFont("Tajawal", 15), text_color=C["sub"])
        self._vid_dur.pack(side="left", padx=(6, 0))
        self._pl_badge = ctk.CTkLabel(dr, text="", font=ctk.CTkFont("Tajawal", 10, weight="bold"),
            fg_color=C["accent_dim"], text_color=C["accent2"], corner_radius=6, padx=8, pady=2)
        self._pl_badge.pack(side="left", padx=(10, 0))

        self._dl_btn = ctk.CTkButton(self._main, text="", height=52, corner_radius=10,
            font=ctk.CTkFont("Tajawal", 14, weight="bold"), fg_color=C["accent"], hover_color=C["accent2"],
            text_color="#ffffff", command=self._start_download)
        self._dl_btn.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(18, 0))

        ctrl = ctk.CTkFrame(self._main, fg_color="transparent")
        ctrl.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(8, 0))
        ctrl.grid_columnconfigure((0, 1), weight=1)
        bk2 = dict(height=42, corner_radius=10, font=ctk.CTkFont("Tajawal", 12),
                   fg_color=C["card"], border_width=1, border_color=C["border"])
        self._pause_btn = ctk.CTkButton(ctrl, text="", hover_color=C["card2"], text_color=C["text"],
            state="disabled", command=self._toggle_pause, **bk2)
        self._pause_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._cancel_btn = ctk.CTkButton(ctrl, text="", hover_color="#3b1515", text_color=C["danger"],
            state="disabled", command=self._cancel_download, **bk2)
        self._cancel_btn.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self._open_btn = ctk.CTkButton(ctrl, text="", width=134, hover_color=C["card2"], text_color=C["sub"],
            command=self._open_folder, **bk2)
        self._open_btn.grid(row=0, column=2)

        self._pl_prog_lbl = ctk.CTkLabel(self._main, text="", font=ctk.CTkFont("Tajawal", 12, weight="bold"),
            text_color=C["accent2"], anchor="w")
        self._pl_prog_lbl.grid(row=5, column=0, sticky="ew", padx=PAD, pady=(10, 0))
        self._prog_canvas = tk.Canvas(self._main, height=10, highlightthickness=0, bd=0, bg=C["panel"])
        self._prog_canvas.grid(row=6, column=0, sticky="ew", padx=PAD, pady=(6, 0))
        self._prog_canvas.bind("<Configure>", self._draw_progress)
        self._prog_pct = getattr(self, "_prog_pct", 0.0)
        st = ctk.CTkFrame(self._main, fg_color="transparent")
        st.grid(row=7, column=0, sticky="ew", padx=PAD, pady=(6, 0))
        self._status_lbl = ctk.CTkLabel(st, text="", font=ctk.CTkFont("Tajawal", 12), text_color=C["sub"], anchor="w")
        self._status_lbl.pack(side="left")
        self._speed_lbl = ctk.CTkLabel(st, text="", font=ctk.CTkFont("Tajawal", 12, weight="bold"),
            text_color=C["accent2"], anchor="e")
        self._speed_lbl.pack(side="right")
        ctk.CTkFrame(self._main, fg_color="transparent").grid(row=9, column=0, sticky="nsew")

        footer = ctk.CTkFrame(self._main, fg_color="transparent")
        footer.grid(row=10, column=0, pady=(0, 20))
        ctk.CTkLabel(footer, text="For suggestions and complaints, ",
            font=ctk.CTkFont("Tajawal", 12), text_color=C["sub"]).pack(side="left")
        link_lbl = ctk.CTkLabel(footer, text="click here",
            font=ctk.CTkFont("Tajawal", 12, underline=True),
            text_color=C["accent"], cursor="hand2")
        link_lbl.pack(side="left")
        link_lbl.bind("<Button-1>", lambda e: webbrowser.open("https://wa.me/201126214380"))
        link_lbl.bind("<Enter>", lambda e: link_lbl.configure(text_color=self.C["accent2"]))
        link_lbl.bind("<Leave>", lambda e: link_lbl.configure(text_color=self.C["accent"]))

    def _build_sidebar_frame(self):
        C = self.C
        self._sidebar = ctk.CTkFrame(self, fg_color=C["bg"], corner_radius=0, width=330,
                                      border_width=1, border_color=C["border"])
        self._sidebar.grid_propagate(False)
        self._sidebar.grid_rowconfigure(1, weight=1)
        self._sidebar.grid_columnconfigure(0, weight=1)
        self._build_history_panel()
        self._build_settings_panel()

    def _show_sidebar(self, which):
        C = self.C; self._sidebar_vis = which
        self._sidebar.grid(row=1, column=1, sticky="nsew")
        if which == "history":
            self._hist_tab_btn.configure(fg_color=C["accent"], text_color="#fff")
            self._sett_tab_btn.configure(fg_color=C["card"],   text_color=C["sub"])
            self._hist_panel.grid(row=1, column=0, sticky="nsew")
            self._sett_panel.grid_forget(); self._refresh_history()
        else:
            self._sett_tab_btn.configure(fg_color=C["accent"], text_color="#fff")
            self._hist_tab_btn.configure(fg_color=C["card"],   text_color=C["sub"])
            self._sett_panel.grid(row=1, column=0, sticky="nsew")
            self._hist_panel.grid_forget()

    def _hide_sidebar(self):
        C = self.C; self._sidebar_vis = None; self._sidebar.grid_forget()
        self._hist_tab_btn.configure(fg_color=C["card"], text_color=C["sub"])
        self._sett_tab_btn.configure(fg_color=C["card"], text_color=C["sub"])

    def _toggle_sidebar(self, which):
        if self._sidebar_vis == which: self._hide_sidebar()
        else: self._show_sidebar(which)

    def _build_history_panel(self):
        C = self.C
        self._hist_panel = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._hist_panel.grid_columnconfigure(0, weight=1); self._hist_panel.grid_rowconfigure(1, weight=1)
        hdr = ctk.CTkFrame(self._hist_panel, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(18, 8)); hdr.grid_columnconfigure(0, weight=1)
        self._hist_title_lbl = ctk.CTkLabel(hdr, text="", font=ctk.CTkFont("Tajawal", 14, weight="bold"), text_color=C["text"])
        self._hist_title_lbl.grid(row=0, column=0, sticky="w")
        self._clear_btn = ctk.CTkButton(hdr, text="", width=72, height=28, font=ctk.CTkFont("Tajawal", 11),
            fg_color=C["card"], hover_color=C["card2"], text_color=C["sub"], corner_radius=6,
            border_width=1, border_color=C["border"], command=self._clear_history)
        self._clear_btn.grid(row=0, column=1, sticky="e")
        self._hist_scroll = ctk.CTkScrollableFrame(self._hist_panel, fg_color="transparent",
            scrollbar_button_color=C["card"], scrollbar_button_hover_color=C["card2"])
        self._hist_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self._hist_scroll.grid_columnconfigure(0, weight=1)

    def _refresh_history(self):
        for w in self._hist_scroll.winfo_children(): w.destroy()
        C = self.C
        if not self._history:
            ctk.CTkLabel(self._hist_scroll, text=self.T("no_history"), font=ctk.CTkFont("Tajawal", 13),
                         text_color=C["dim"]).pack(pady=40); return
        for entry in self._history[:80]:
            card = ctk.CTkFrame(self._hist_scroll, fg_color=C["card"], corner_radius=8,
                                border_width=1, border_color=C["border"])
            card.pack(fill="x", pady=3, padx=2)
            top = ctk.CTkFrame(card, fg_color="transparent"); top.pack(fill="x", padx=10, pady=(8, 0))
            ctk.CTkLabel(top, text=entry.get("title", "—")[:38], font=ctk.CTkFont("Tajawal", 12, weight="bold"),
                text_color=C["text"], anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(card, text=entry.get("date", ""), font=ctk.CTkFont("Tajawal", 10),
                text_color=C["dim"], anchor="w").pack(anchor="w", padx=10, pady=(2, 0))
            btns = ctk.CTkFrame(card, fg_color="transparent"); btns.pack(fill="x", padx=8, pady=(4, 8))
            fp = entry.get("filepath", ""); fld = entry.get("folder", "")
            bkw = dict(height=28, corner_radius=6, font=ctk.CTkFont("Tajawal", 11),
                       fg_color=C["card2"], hover_color=C["border"],
                       text_color=C["accent2"], border_width=1, border_color=C["border"])

            def _play(p=fp, folder=fld):
                target = p if (p and os.path.isfile(p)) else None
                if not target and p and fld:
                    stem = os.path.splitext(os.path.basename(p))[0]
                    for ext in (".mp4", ".mkv", ".m4a", ".webm", ".opus", ".mp3", ".ogg"):
                        candidate = os.path.join(fld, stem + ext)
                        if os.path.isfile(candidate): target = candidate; break
                if not target:
                    self._show_msg_box("Warning", "File not found:\n" + str(p), False); return
                try:
                    if IS_WINDOWS: os.startfile(target)
                    elif platform.system() == "Darwin": subprocess.Popen(["open", target])
                    else: subprocess.Popen(["xdg-open", target])
                except Exception as e: self._show_msg_box("Error", str(e), True)

            ctk.CTkButton(btns, text=self.T("h_open_file"), width=84, command=_play, **bkw).pack(side="left", padx=(0, 5))

            def _folder(d=fld, p=fp):
                target = os.path.dirname(p) if p and os.path.isfile(p) else d
                if not target: return
                try:
                    if IS_WINDOWS:
                        if p and os.path.isfile(p): subprocess.Popen(["explorer", "/select,", p])
                        else: os.startfile(target)
                    elif platform.system() == "Darwin": subprocess.Popen(["open", target])
                    else: subprocess.Popen(["xdg-open", target])
                except Exception as e: self._show_msg_box("Error", str(e), True)

            ctk.CTkButton(btns, text=self.T("h_open_folder"), width=84, command=_folder, **bkw).pack(side="left")

    def _clear_history(self):
        self._history = []; self._save_history_file(); self._refresh_history()

    def _build_settings_panel(self):
        C = self.C
        self._sett_panel = ctk.CTkScrollableFrame(self._sidebar, fg_color="transparent",
            scrollbar_button_color=C["card"], scrollbar_button_hover_color=C["card2"])
        self._sett_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self._sett_panel, text=self.T("settings_tab"),
            font=ctk.CTkFont("Tajawal", 15, weight="bold"), text_color=C["text"], anchor="w"
        ).pack(fill="x", padx=14, pady=(18, 12))

        # ── Appearance ────────────────────────────────────────────────────────
        self._s_sec(self.T("s_appearance"))
        mr = ctk.CTkFrame(self._sett_panel, fg_color=C["card"], corner_radius=8,
                          border_width=1, border_color=C["border"])
        mr.pack(fill="x", padx=14, pady=(0, 14))
        self._dark_btn = ctk.CTkButton(mr, text=self.T("s_dark"), height=38, corner_radius=6,
            font=ctk.CTkFont("Tajawal", 13),
            fg_color=C["accent"] if self.dark_mode else C["card2"],
            hover_color=C["accent2"], text_color="#fff" if self.dark_mode else C["sub"],
            command=lambda: self._apply_theme(True))
        self._dark_btn.pack(side="left", expand=True, fill="x", padx=(6, 3), pady=8)
        self._light_btn = ctk.CTkButton(mr, text=self.T("s_light"), height=38, corner_radius=6,
            font=ctk.CTkFont("Tajawal", 13),
            fg_color=C["card2"] if self.dark_mode else C["accent"],
            hover_color=C["accent2"], text_color=C["sub"] if self.dark_mode else "#fff",
            command=lambda: self._apply_theme(False))
        self._light_btn.pack(side="left", expand=True, fill="x", padx=(3, 6), pady=8)

        # ── Ask folder ────────────────────────────────────────────────────────
        self._s_sec(self.T("s_ask_folder"))
        ar = ctk.CTkFrame(self._sett_panel, fg_color=C["card"], corner_radius=8,
                          border_width=1, border_color=C["border"])
        ar.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(ar, text=self.T("s_ask_folder_sub"), font=ctk.CTkFont("Tajawal", 11),
            text_color=C["text"], wraplength=220, justify="left", anchor="w"
        ).pack(side="left", padx=12, pady=10, fill="x", expand=True)
        self._ask_var = tk.BooleanVar(value=self.ask_folder)
        ctk.CTkSwitch(ar, text="", variable=self._ask_var, width=44,
            fg_color=C["dim"], progress_color=C["accent"],
            command=self._on_ask_toggle).pack(side="right", padx=12)

        # ── Default folder ────────────────────────────────────────────────────
        self._s_sec(self.T("s_default_folder"))
        dr = ctk.CTkFrame(self._sett_panel, fg_color=C["card"], corner_radius=8,
                          border_width=1, border_color=C["border"])
        dr.pack(fill="x", padx=14, pady=(0, 14)); dr.grid_columnconfigure(0, weight=1)
        self._sett_folder_lbl = ctk.CTkLabel(dr, text=self._short_path(self.folder, 26),
            font=ctk.CTkFont("Tajawal", 11), text_color=C["text"], anchor="w")
        self._sett_folder_lbl.grid(row=0, column=0, padx=12, pady=10, sticky="w")
        ctk.CTkButton(dr, text=self.T("browse"), width=68, height=30,
            font=ctk.CTkFont("Tajawal", 11), fg_color=C["accent_dim"], hover_color=C["accent"],
            text_color=C["accent2"], corner_radius=6, command=self._change_folder
        ).grid(row=0, column=1, padx=8, pady=8)

        # ── Default quality ───────────────────────────────────────────────────
        self._s_sec(self.T("s_quality_default"))
        qr = ctk.CTkFrame(self._sett_panel, fg_color=C["card"], corner_radius=8,
                          border_width=1, border_color=C["border"])
        qr.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkOptionMenu(qr, variable=self._q_var, values=["480p", "720p", "1080p", "Best", "Audio"],
            width=120, height=36, font=ctk.CTkFont("Tajawal", 12),
            fg_color=C["card"], button_color=C["card"], button_hover_color=C["card2"],
            text_color=C["text"], dropdown_text_color=C["text"],
            dropdown_fg_color=C["card"], dropdown_hover_color=C["card2"],
            command=self._save_settings).pack(padx=10, pady=10, anchor="w")

        # ── ★ Check for Updates ───────────────────────────────────────────────
        self._s_sec(self.T("s_updates"))
        upd_frame = ctk.CTkFrame(self._sett_panel, fg_color=C["card"], corner_radius=8,
                                  border_width=1, border_color=C["border"])
        upd_frame.pack(fill="x", padx=14, pady=(0, 14))

        self._update_btn = ctk.CTkButton(
            upd_frame,
            text=self.T("s_check_updates"),
            height=40,
            font=ctk.CTkFont("Tajawal", 13, "bold"),
            fg_color=C["card"],
            hover_color=C["card2"],
            border_color=C["border"],
            border_width=1,
            text_color=C["accent2"],
            corner_radius=8,
            command=self._manual_check_updates
        )
        self._update_btn.pack(fill="x", padx=10, pady=(10, 6))

        # Version label
        ctk.CTkLabel(upd_frame, text=self.T("version_lbl", v=APP_VERSION),
            font=ctk.CTkFont("Tajawal", 10), text_color=C["dim"]).pack(pady=(0, 10))

    def _s_sec(self, label):
        ctk.CTkLabel(self._sett_panel, text=label, font=ctk.CTkFont("Tajawal", 12, weight="bold"),
                     text_color=self.C["sub"], anchor="w").pack(fill="x", padx=14, pady=(8, 4))

    def _on_ask_toggle(self):
        self.ask_folder = self._ask_var.get(); self._save_settings()

    def _apply_lang(self):
        self.title(self.T("title"))
        self._app_title.configure(text=self.T("title"))
        self._app_sub.configure(text="  —  " + self.T("subtitle"))
        self._hist_tab_btn.configure(text=self.T("history_tab"))
        self._sett_tab_btn.configure(text=self.T("settings_tab"))
        self._qual_lbl.configure(text=self.T("quality") + ":")
        self._dl_btn.configure(text=self.T("download_btn"))
        self._pause_btn.configure(text=self.T("resume") if (self.task and self.task.is_paused) else self.T("pause"))
        self._cancel_btn.configure(text=self.T("cancel"))
        self._open_btn.configure(text=self.T("open_folder"))
        self._browse_btn.configure(text=self.T("browse"))
        self._status_lbl.configure(text=self.T("status_idle"))
        self._info_title_key.configure(text=self.T("title_lbl") + ":")
        self._info_dur_key.configure(text=self.T("duration_lbl") + ":")
        self._hist_title_lbl.configure(text=self.T("history_tab"))
        self._clear_btn.configure(text=self.T("clear_history"))
        self._drop_lbl.configure(text=self.T("drop_hint") if DND_AVAILABLE else "")

    def _get_url(self) -> str:
        try: return self._url_entry.get().strip()
        except: return ""

    # ── paste / keyboard ──────────────────────────────────────────────────────
    def _ctrl_keypress(self, event):
        if event.keycode == 86 or event.keysym in ("v", "V"):
            focused = self.focus_get(); entry_widget = self._url_entry
            inner = getattr(entry_widget, "_entry", None)
            if focused in (entry_widget, inner):
                self._paste_url(); return "break"

    def _global_paste_event(self, event=None):
        focused = self.focus_get(); entry_widget = self._url_entry
        inner = getattr(entry_widget, "_entry", None)
        if focused in (entry_widget, inner):
            self._paste_url(); return "break"

    def _paste_url(self):
        try: text = self.clipboard_get()
        except: return
        self._url_entry.delete(0, "end"); self._url_entry.insert(0, text.strip())
        self.after(100, self._trigger_fetch)

    def _show_ctx_menu(self, event):
        try: self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally: self._ctx_menu.grab_release()

    def _setup_dnd(self):
        if not DND_AVAILABLE: return
        for widget in (self, self._url_entry):
            try:
                widget.drop_target_register(DND_TEXT, DND_FILES)
                widget.dnd_bind("<<Drop>>",      self._on_drop)
                widget.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DragLeave>>", self._on_drag_leave)
            except Exception as e: print(f"[DnD] {e}")

    def _on_drag_enter(self, _): self._url_entry.configure(border_color=self.C["accent2"])
    def _on_drag_leave(self, _): self._url_entry.configure(border_color=self.C["border"])

    def _on_drop(self, event):
        self._url_entry.configure(border_color=self.C["border"])
        raw = event.data.strip().strip("{}"); url = raw.split()[0] if raw else ""
        if url and os.path.isfile(url):
            try:
                with open(url, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("URL="): url = line[4:]; break
                        if line.startswith(("http://", "https://")): url = line; break
            except: pass
        if url: self._set_url(url)

    def _set_url(self, url: str):
        self._url_entry.delete(0, "end"); self._url_entry.insert(0, url); self._trigger_fetch()

    def _draw_progress(self, _=None):
        c = self._prog_canvas; C = self.C; c.configure(bg=C["panel"]); c.delete("all")
        w = c.winfo_width(); h = 10; r = 5
        c.create_rectangle(r, 0, w-r, h, fill=C["card"], outline="")
        c.create_oval(0, 0, r*2, h, fill=C["card"], outline="")
        c.create_oval(w-r*2, 0, w, h, fill=C["card"], outline="")
        fw = int(w * self._prog_pct / 100)
        if fw > r*2:
            c.create_rectangle(r, 0, fw-r, h, fill=C["accent"], outline="")
            c.create_oval(0, 0, r*2, h, fill=C["accent"], outline="")
            tip = min(fw, w)
            c.create_oval(tip-r*2-4, -1, tip+4, h+1, fill=C["accent2"], outline="")

    def _set_progress(self, pct):
        self._prog_pct = max(0.0, min(100.0, pct)); self._draw_progress()
        self._taskbar.set(TaskbarProgress.NORMAL, self._prog_pct, 100)

    def _on_key(self, _=None):
        if self._url_after: self.after_cancel(self._url_after)
        self._url_after = self.after(800, self._trigger_fetch)

    def _trigger_fetch(self, _=None):
        url = self._get_url()
        if not url.startswith(("http://", "https://")): return
        self._fetch_cancel.set(); time.sleep(0.02); self._fetch_cancel.clear()
        self._thumb_loaded = False
        self._vid_title.configure(text=self.T("fetching"))
        self._vid_dur.configure(text=""); self._pl_badge.configure(text="")
        self._thumb_lbl.configure(image=None, text="⏳")
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()

    def _fetch_worker(self, url):
        if self._fetch_cancel.is_set(): return
        try:
            opts = {"quiet": True, "no_warnings": True,
                    "extractor_args": {"youtube": {"player_client": ["android"]}}}
            with YoutubeDL(opts) as ydl: info = ydl.extract_info(url, download=False)
            if self._fetch_cancel.is_set(): return
            if "entries" in info:
                count = info.get("playlist_count") or len(list(info["entries"]))
                pl_title = info.get("title", "Playlist")
                self.after(0, lambda: self._vid_title.configure(text=f"📁 {pl_title}"))
                self.after(0, lambda c=count: (self._vid_dur.configure(text=""),
                                               self._pl_badge.configure(text=self.T("pl_badge", n=c))))
            else:
                title = info.get("title", "—"); dur = info.get("duration", 0) or 0
                m, s  = divmod(int(dur), 60)
                thumbs = info.get("thumbnails", [])
                thumb_url = thumbs[-1].get("url") if thumbs else None
                self.after(0, lambda t=title: self._vid_title.configure(text=t))
                self.after(0, lambda d=f"{m}:{s:02d}": (self._vid_dur.configure(text=d),
                                                          self._pl_badge.configure(text="")))
                if PIL_AVAILABLE and thumb_url and not self._fetch_cancel.is_set():
                    try:
                        with urllib.request.urlopen(thumb_url, timeout=6) as r: data = r.read()
                        img = Image.open(io.BytesIO(data)).resize((THUMB_W, THUMB_H), Image.LANCZOS)
                        photo = ImageTk.PhotoImage(img)
                        if not self._fetch_cancel.is_set():
                            self.after(0, lambda p=photo: self._set_thumb(p))
                    except: pass
        except Exception as e:
            if not self._fetch_cancel.is_set():
                self.after(0, lambda: self._vid_title.configure(text=f"⚠  {str(e)[:70]}"))

    def _set_thumb(self, photo):
        if self._thumb_loaded: return
        self._thumb_img = photo
        self._thumb_lbl.configure(image=photo, text=""); self._thumb_loaded = True

    # =========================================================================
    #  Download — NO ffmpeg, pure native formats
    # =========================================================================
    def _start_download(self):
        url = self._get_url()
        if not url:
            self._show_msg_box(self.T("title"), self.T("err_no_url"), False); return
        if not url.startswith(("http://", "https://")):
            self._show_msg_box(self.T("title"), self.T("err_invalid_url"), False); return
        if self.task:
            self._show_msg_box(self.T("title"), self.T("err_in_progress"), False); return

        save_folder = self.folder
        if self.ask_folder:
            f = filedialog.askdirectory(initialdir=self.folder, title=self.T("folder"))
            if not f: return
            save_folder = f

        self.pl_count = 0; self.pl_total = 1
        def _detect():
            try:
                with YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if "entries" in info:
                        self.pl_total = info.get("playlist_count") or len(list(info["entries"]))
            except: pass
        threading.Thread(target=_detect, daemon=True).start()

        quality = self._q_var.get()

        # ── Format selection WITHOUT ffmpeg ───────────────────────────────────
        # Priority: pre-muxed mp4 → best single-file container → best available
        qmap = {
            "480p":  ("best[height<=480][ext=mp4]"
                      "/best[height<=480][vcodec!=none][acodec!=none]"
                      "/best[height<=480]/best"),
            "720p":  ("best[height<=720][ext=mp4]"
                      "/best[height<=720][vcodec!=none][acodec!=none]"
                      "/best[height<=720]/best"),
            "1080p": ("best[height<=1080][ext=mp4]"
                      "/best[height<=1080][vcodec!=none][acodec!=none]"
                      "/best[height<=1080]/best"),
            "Best":  ("best[ext=mp4]"
                      "/best[vcodec!=none][acodec!=none]"
                      "/best"),
        }

        ydl_opts = dict(
            outtmpl=os.path.join(save_folder, "%(title)s.%(ext)s"),
            quiet=True,
            no_warnings=True,
            overwrites=False,
            concurrent_fragment_downloads=4,
            extractor_args={"youtube": {"player_client": ["android"]}},
            # Explicitly disable any postprocessing that needs ffmpeg
            postprocessors=[],
            prefer_ffmpeg=False,
            keepvideo=False,
        )

        if quality == "Audio":
            # Best native audio — m4a preferred (plays everywhere), then webm/opus
            ydl_opts["format"] = (
                "bestaudio[ext=m4a]"
                "/bestaudio[ext=webm]"
                "/bestaudio[ext=opus]"
                "/bestaudio"
            )
        else:
            ydl_opts["format"] = qmap.get(quality, qmap["720p"])

        self._cur_folder = save_folder; self._set_progress(0)
        self._pl_prog_lbl.configure(text="")
        self._status_lbl.configure(text=self.T("status_preparing"))
        self._speed_lbl.configure(text="")
        self._pause_btn.configure(state="normal", text=self.T("pause"))
        self._cancel_btn.configure(state="normal")
        self._dl_btn.configure(state="disabled", fg_color=self.C["dim"])
        self._taskbar.set(TaskbarProgress.INDETERMINATE)
        self._dl_locked = True; self._url_entry.configure(state="readonly")
        self.task = DownloadTask(url, ydl_opts, self._dl_q); self.task.start()

    # ── progress / complete / error ───────────────────────────────────────────
    def _upd_progress(self, d):
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        done  = d.get("downloaded_bytes", 0)
        speed = d.get("speed") or 0
        eta   = d.get("eta") or 0
        if total and total > 0:
            pct = done / total * 100; self._set_progress(pct)
            self._status_lbl.configure(
                text=f"{self.T('status_downloading')}  {pct:.1f}%  —  ETA {self._fmt_eta(eta)}")
        else:
            self._status_lbl.configure(text=self.T("status_downloading") + "…")
        self._speed_lbl.configure(text=self._fmt_speed(speed))

    def _handle_complete(self, d):
        fp = d.get("filename", "")
        title = os.path.splitext(os.path.basename(fp))[0] if fp else "Video"
        url = self._get_url(); fld = self._cur_folder
        if self.pl_total > 1:
            self.pl_count += 1; pct = self.pl_count / self.pl_total * 100
            self._set_progress(pct)
            self._pl_prog_lbl.configure(text=self.T("pl_progress", done=self.pl_count, total=self.pl_total))
            if self.pl_count >= self.pl_total:
                self._add_history(title, url, fld, fp)
                self._notify(self.T("notif_pl_title"), self.T("notif_pl_msg", n=self.pl_total, folder=fld))
                self._status_lbl.configure(text=self.T("status_completed"))
                self._taskbar.reset(); self._reset_ui()
        else:
            self._set_progress(100); self._status_lbl.configure(text=self.T("status_completed"))
            self._add_history(title, url, fld, fp)
            self._notify(self.T("notif_done_title"), self.T("notif_done_msg", folder=fld))
            self._taskbar.reset(); self._reset_ui()

    def _notify(self, title, msg):
        if self.toaster and TOAST_AVAILABLE:
            try: self.toaster.show_toast(title, msg, duration=6, threaded=True); return
            except: pass
        self._show_msg_box(title, msg, False)

    def _handle_error(self, err):
        self._taskbar.set(TaskbarProgress.ERROR, 50, 100)
        self.after(3000, self._taskbar.reset)
        if "Cancelled" in err: return
        el = err.lower()
        if "network" in el or "connection" in el or "timeout" in el or "errno" in el:
            msg = self.T("err_network")
        elif "unavailable" in el or "private" in el or "removed" in el or "not available" in el:
            msg = self.T("err_unavailable")
        elif "copyright" in el or "blocked" in el or "forbidden" in el:
            msg = self.T("err_copyright")
        elif "age" in el or "sign in" in el or "login" in el:
            msg = self.T("err_age")
        elif "format" in el or "codec" in el:
            msg = ("No pre-muxed format found for this quality.\n"
                   "Try a lower resolution or 'Best' mode.")
        elif "playlist" in el or "entries" in el:
            msg = "Could not load playlist. Check the URL."
        else:
            msg = self.T("err_generic")
        self._status_lbl.configure(text=self.T("status_failed"))
        self._show_msg_box(self.T("title"), msg, True)
        self._reset_ui()

    def _reset_ui(self):
        self._pause_btn.configure(state="disabled", text=self.T("pause"))
        self._cancel_btn.configure(state="disabled")
        self._speed_lbl.configure(text=""); self._dl_locked = False
        self._url_entry.configure(state="normal")
        self._dl_btn.configure(state="normal", fg_color=self.C["accent"])
        self.task = None

    def _toggle_pause(self):
        if not self.task: return
        if self.task.is_paused:
            self.task.resume(); self._pause_btn.configure(text=self.T("pause"))
            self._status_lbl.configure(text=self.T("status_downloading") + "…")
            self._taskbar.set(TaskbarProgress.NORMAL, self._prog_pct, 100)
        else:
            self.task.pause(); self._pause_btn.configure(text=self.T("resume"))
            self._status_lbl.configure(text=self.T("status_paused"))
            self._taskbar.set(TaskbarProgress.PAUSED, self._prog_pct, 100)

    def _cancel_download(self):
        if self.task: self.task.cancel()
        self._set_progress(0); self._pl_prog_lbl.configure(text="")
        self._status_lbl.configure(text=self.T("status_cancelled"))
        self._speed_lbl.configure(text=""); self._taskbar.reset(); self._reset_ui()

    def _change_folder(self):
        f = filedialog.askdirectory(initialdir=self.folder)
        if f:
            self.folder = f; self._folder_lbl.configure(text=self._short_path(f))
            if hasattr(self, "_sett_folder_lbl"):
                self._sett_folder_lbl.configure(text=self._short_path(f, 26))
            self._save_settings()

    def _open_folder(self):
        try:
            if IS_WINDOWS: os.startfile(self.folder)
            elif platform.system() == "Darwin": subprocess.Popen(["open", self.folder])
            else: subprocess.Popen(["xdg-open", self.folder])
        except Exception as e: self._show_msg_box(self.T("title"), str(e), True)

    @staticmethod
    def _fmt_speed(bps):
        if not bps: return ""
        return f"{bps/1024:.1f} KB/s" if bps < 1024**2 else f"{bps/1024**2:.1f} MB/s"

    @staticmethod
    def _fmt_eta(s):
        if not s: return "—"
        m, s = divmod(int(s), 60); h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @staticmethod
    def _short_path(path, max_len=42):
        if len(path) <= max_len: return path
        parts = path.replace("\\", "/").split("/")
        return ("…/" + "/".join(parts[-2:])) if len(parts) > 3 else path


if __name__ == "__main__":
    App().mainloop()