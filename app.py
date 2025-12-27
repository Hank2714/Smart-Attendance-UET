# app.py
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TF_NUM_INTRAOP_THREADS", "1")
os.environ.setdefault("TF_NUM_INTEROP_THREADS", "1")

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk

import cv2
try:
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass


# ========= Windows AppUserModelID (taskbar icon đúng) =========
if sys.platform.startswith("win"):
    try:
        import ctypes
        APPID = "Goonology.SmartAttendance.Desktop.1.0"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APPID)
    except Exception:
        pass


# ========= App paths =========
def get_app_base():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


APP_BASE = get_app_base()
ASSETS_DIR = os.path.join(APP_BASE, "assets")
ICO_PATH   = os.path.join(ASSETS_DIR, "app.ico")
PNG_PATH   = os.path.join(ASSETS_DIR, "app.png")
SPLASH_GIF = os.path.join(ASSETS_DIR, "splash.gif")


def _build_multi_ico_from_png(png_path: str, ico_path: str, bg="#111827"):
    try:
        from PIL import Image
        im = Image.open(png_path).convert("RGBA")
        size = max(im.width, im.height)
        square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        square.paste(im, ((size - im.width)//2, (size - im.height)//2), im)
        bg_rgb = Image.new("RGB", (size, size), bg)
        out = Image.alpha_composite(bg_rgb.convert("RGBA"), square).convert("RGB")
        out.save(
            ico_path, format="ICO",
            sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]
        )
    except Exception:
        pass


def _ensure_icon(window):
    if not os.path.exists(ICO_PATH) and os.path.exists(PNG_PATH):
        _build_multi_ico_from_png(PNG_PATH, ICO_PATH)
    try:
        if os.path.exists(ICO_PATH):
            window.iconbitmap(default=ICO_PATH)
            window.wm_iconbitmap(ICO_PATH)
        if os.path.exists(PNG_PATH):
            img = Image.open(PNG_PATH)
            window._icon = ImageTk.PhotoImage(img)
            window.iconphoto(True, window._icon)
    except Exception:
        pass


# ========= Force icon on taskbar (Windows only) =========
def _force_taskbar_icon_strong(tk_window, ico_path: str | None):
    if not (sys.platform.startswith("win") and ico_path and os.path.exists(ico_path)):
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32

        WM_SETICON  = 0x0080
        ICON_SMALL  = 0
        ICON_BIG    = 1
        GCLP_HICON   = -14
        GCLP_HICONSM = -34
        IMAGE_ICON   = 1
        LR_LOADFROMFILE = 0x0010

        tk_window.update_idletasks()
        hwnd = tk_window.winfo_id()

        hicon_big = user32.LoadImageW(0, ico_path, IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        hicon_sma = user32.LoadImageW(0, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)

        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG,   hicon_big)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_sma)

        SetClassLongPtrW = getattr(user32, "SetClassLongPtrW", None)
        if SetClassLongPtrW:
            SetClassLongPtrW(hwnd, GCLP_HICON,   hicon_big)
            SetClassLongPtrW(hwnd, GCLP_HICONSM, hicon_sma)
    except Exception:
        pass


# ========= Main App =========
class App(tb.Window):
    def __init__(self):
        super().__init__(themename="darkly")

        self.title("Smart Attendance (Goonology®TM)")
        try:
            self.state("zoomed")
        except Exception:
            self.geometry("1600x900")

        _ensure_icon(self)
        _force_taskbar_icon_strong(self, ICO_PATH if os.path.exists(ICO_PATH) else None)

        self.bind(
            "<Map>",
            lambda e: _force_taskbar_icon_strong(
                self, ICO_PATH if os.path.exists(ICO_PATH) else None
            ),
            add="+"
        )

        self.nb = tb.Notebook(self)

        self.people_tab = None
        self.att_tab = None
        self.stat_tab = None
        self.about_tab = None

        self._prev_tab_widget = None

    def build_tabs(self):
        from tabs.home.people_tab import PeopleTab
        from tabs.attendance.attendance_tab import AttendanceTab
        from tabs.Statistic.statistic_tab import StatisticTab
        from tabs.about_tab import AboutTab

        self.people_tab = PeopleTab(self.nb)
        self.att_tab    = AttendanceTab(self.nb)
        self.stat_tab   = StatisticTab(self.nb)
        self.about_tab  = AboutTab(self.nb)

        self.nb.add(self.people_tab, text="Home")
        self.nb.add(self.att_tab,    text="Attendance")
        self.nb.add(self.stat_tab,   text="Statistic")
        self.nb.add(self.about_tab,  text="About")

        # ===== TAB LIFECYCLE (FIX CHUẨN) =====
        def _on_tab_changed(event=None):
            try:
                nb = self.nb
                new_tab = nb.nametowidget(nb.select())

                old_tab = self._prev_tab_widget
                if old_tab and old_tab is not new_tab:
                    if hasattr(old_tab, "on_tab_deselected"):
                        old_tab.on_tab_deselected()

                if hasattr(new_tab, "on_tab_selected"):
                    new_tab.on_tab_selected()

                self._prev_tab_widget = new_tab
            except Exception:
                pass

        self.nb.bind("<<NotebookTabChanged>>", _on_tab_changed, add="+")
        self.nb.after_idle(_on_tab_changed)
        # ====================================

        self.nb.pack(fill=BOTH, expand=YES, padx=8, pady=8)

        # ===== WARMUP: đi qua các tab 1 lượt để init/caching =====
        self.warmup_tabs(delay_ms=180)
        # =========================================================

    def refresh_all(self):
        try:
            if self.people_tab:
                self.people_tab.refresh()
            if self.att_tab:
                self.att_tab.refresh()
        except Exception:
            pass

    def warmup_tabs(self, delay_ms: int = 160):
        """
        Auto select qua tất cả tab 1 lượt để init layout/cache giống thao tác tay.
        Sau đó quay về tab ban đầu.
        """
        try:
            nb = self.nb
            tabs = nb.tabs()
            if not tabs:
                return

            start_tab = nb.select()
            i = 0

            def _step():
                nonlocal i
                if i < len(tabs):
                    try:
                        nb.select(tabs[i])
                    except Exception:
                        pass
                    i += 1
                    nb.after(delay_ms, _step)
                else:
                    try:
                        nb.select(start_tab)
                    except Exception:
                        pass

            nb.after(200, _step)
        except Exception:
            pass


# ========= Entry =========
def run_with_splash():
    from ui.splash import Splash

    app = App()

    splash = Splash(
        app,
        title="Smart Attendance — Initializing...",
        gif_path=SPLASH_GIF,
        width=760,
        height=560,
        icon_image=getattr(app, "_icon", None),
        icon_ico=ICO_PATH if os.path.exists(ICO_PATH) else None
    )

    try:
        splash.wm_attributes("-toolwindow", True)
    except Exception:
        pass

    def step1():
        splash.set_status(25, "Importing modules…")
        app.after(120, step2)

    def step2():
        splash.set_status(60, "Building interface…")
        app.after(180, step3)

    def step3():
        splash.set_status(90, "Finalizing…")
        app.build_tabs()
        app.after(300, finish)

    def finish():
        splash.set_status(100, "Ready.")
        splash.after(350, splash.close)

    app.after(80, step1)
    app.mainloop()


if __name__ == "__main__":
    run_with_splash()
