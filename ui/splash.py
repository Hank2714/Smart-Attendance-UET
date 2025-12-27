# ui/splash.py
from __future__ import annotations
import os
import tkinter as tk
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk, ImageSequence

class Splash(tb.Toplevel):
    """
    Splash độc lập giữa màn hình:
      - GIF động (after)
      - Progressbar + status
      - Tự căn CHÍNH GIỮA màn hình và re-center sau khi hiển thị
    """
    def __init__(self, master, title="Smart Attendance — Initializing...",
                 gif_path=None, width=720, height=520,
                 icon_image: ImageTk.PhotoImage | None = None,
                 icon_ico: str | None = None):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        # icon cho Toplevel
        try:
            if icon_ico:
                self.iconbitmap(default=icon_ico)
                try: self.wm_iconbitmap(icon_ico)
                except Exception: pass
            if icon_image:
                self._icon_ref = icon_image
                self.iconphoto(True, self._icon_ref)
        except Exception:
            pass

        # theme
        self.colors = {
            "bg": "#0D1117",
            "panel": "#161B22",
            "border": "#30363D",
            "text": "#F0F6FC",
            "muted": "#8B949E",
            "accent": "#58A6FF",
        }

        # main panel
        frame = tk.Frame(self, bg=self.colors["panel"],
                         highlightbackground=self.colors["border"], highlightthickness=2)
        frame.pack(fill=BOTH, expand=YES, padx=12, pady=12)

        self.title_lbl = tk.Label(frame, text=title, fg=self.colors["text"],
                                  bg=self.colors["panel"], font=("Segoe UI", 14, "bold"))
        self.title_lbl.pack(pady=(10, 12))

        self.img_lbl = tk.Label(frame, bg=self.colors["panel"])
        self.img_lbl.pack(pady=(6, 10))

        tk.Label(frame, text="Please wait…", fg=self.colors["muted"],
                 bg=self.colors["panel"], font=("Segoe UI", 11)).pack()

        self._status_char_width = 56
        self.status_lbl = tk.Label(
            frame, text="", fg=self.colors["muted"], bg=self.colors["panel"],
            font=("Segoe UI", 10), anchor="center", width=self._status_char_width, justify="center"
        )
        self.status_lbl.configure(wraplength=0)
        self.status_lbl.pack(pady=(2, 10))

        self.pb = tb.Progressbar(frame, mode="indeterminate", length=480, bootstyle="info-striped")
        self.pb.pack(pady=(0, 12))
        self.pb.start(12)

        # geometry & center
        self.update_idletasks()
        self.geometry(f"{width}x{height}")
        self.configure(bg=self.colors["bg"])
        self.deiconify()
        # center ngay khi hiển thị xong
        self.after_idle(self._center)
        # re-center 1 lần khi WM map/điều chỉnh xong
        self.bind("<Map>", lambda e: self.after(10, self._center), add="+")

        # GIF
        self.frames: list[ImageTk.PhotoImage] = []
        self.delays: list[int] = []
        self.frame_idx = 0
        self.gif_after = None
        if gif_path and os.path.exists(gif_path):
            self._load_gif(gif_path)
        else:
            self.img_lbl.config(text="✦", fg=self.colors["accent"], font=("Segoe UI", 60, "bold"))

        if self.frames:
            self._animate_gif()

    # ---- center ở GIỮA màn hình ----
    def _center(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = self.winfo_width(), self.winfo_height()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"+{x}+{y}")

    def _load_gif(self, path):
        try:
            gif = Image.open(path)
            for fr in ImageSequence.Iterator(gif):
                frm = fr.convert("RGBA")
                frm.thumbnail((520, 300), Image.LANCZOS)
                self.frames.append(ImageTk.PhotoImage(frm))
                self.delays.append(fr.info.get("duration", 80))
        except Exception as e:
            print("[Splash] GIF load failed:", e)

    def _animate_gif(self):
        if not self.frames:
            return
        self.img_lbl.configure(image=self.frames[self.frame_idx])
        delay = self.delays[self.frame_idx] if self.delays else 80
        self.frame_idx = (self.frame_idx + 1) % len(self.frames)
        self.gif_after = self.after(delay, self._animate_gif)

    # public API
    def set_status(self, percent: int | None = None, text: str | None = None):
        if text is not None:
            txt = str(text).replace("\n", " ").strip()
            if len(txt) > self._status_char_width:
                txt = txt[:self._status_char_width - 1] + "…"
            self.status_lbl.configure(text=txt)

        if percent is not None:
            try:
                val = max(0, min(100, int(percent)))
                if str(self.pb.cget("mode")) != "determinate":
                    self.pb.stop()
                    self.pb.configure(mode="determinate")
                    self.pb["maximum"] = 100
                self.pb["value"] = val
            except Exception:
                pass

        self.update_idletasks()

    def close(self):
        if self.gif_after:
            try: self.after_cancel(self.gif_after)
            except Exception: pass
        try:
            self.destroy()
        except Exception:
            pass
