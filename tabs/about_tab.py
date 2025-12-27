# tabs/about_tab.py
import os
import sys
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import messagebox
from datetime import date

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except Exception:
    PIL_OK = False


# ---- APP_BASE hỗ trợ khi chạy .exe (PyInstaller) ----
def get_app_base() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    # this file is tabs/about_tab.py -> go up one to project root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_BASE = get_app_base()
ASSETS   = os.path.join(APP_BASE, "assets")


class AboutTab(tb.Frame):
    """
    Tab 'About' với nút 'Ủng hộ tôi' mở cửa sổ Donate kiểu meme (vô hại).
    - Không lưu, không gửi, không xử lý dữ liệu người dùng.
    - Nhập xong bấm nút sẽ tự động xoá các ô input.
    """
    def __init__(self, master):
        super().__init__(master)
        self._meme_imgtk = None  # giữ reference ảnh meme nếu có
        self._build()

    # ---------------- UI ----------------
    def _build(self):
        # Card mô tả
        card = tb.Labelframe(self, text="About SmartAttendance")
        card.pack(fill=X, padx=10, pady=10)

        tb.Label(card,
                 text="Smart Attendance (Goonology®TM)",
                 font="-size 12 -weight bold").pack(anchor=W, padx=12, pady=(10, 2))

        tb.Label(card,
                 text=f"Version: 1.0  •  Build: 10/28/2025").pack(anchor=W, padx=12)

        tb.Label(card,
                 text="DeepFace + MySQL + ttkbootstrap  •  Demo UI for learning",
                 foreground="#84d2ff").pack(anchor=W, padx=12, pady=(0, 8))

        # Nút mở donate window (meme)
        btns = tb.Frame(card)
        btns.pack(anchor=W, padx=12, pady=(0, 10))
        tb.Button(btns, text="Support Us ☕", bootstyle=SUCCESS,
                  command=self._open_donate_window).pack(side=LEFT)

        # Ghi chú
        tb.Label(self,
                 text="Note: This app does NOT collect any payment data. The donate window is a meme for fun.",
                 foreground="#9ad1ff").pack(anchor=W, padx=12)

    # ---------------- Donate (meme) window ----------------
    def _open_donate_window(self):
        win = tb.Toplevel(self)
        win.title("Totally Not Malware 💳")
        win.geometry("560x320")
        win.resizable(False, False)

        root = tb.Frame(win)
        root.pack(fill=BOTH, expand=YES, padx=10, pady=10)

        # Left: Meme image (optional)
        left = tb.Frame(root)
        left.grid(row=0, column=0, sticky=NS, padx=(0, 10))

        img_path = None
        for cand in ("donate.png", "begging.png"):
            p = os.path.join(ASSETS, cand)
            if os.path.exists(p):
                img_path = p
                break

        if PIL_OK and img_path:
            try:
                im = Image.open(img_path).convert("RGB")
                im.thumbnail((220, 220))
                self._meme_imgtk = ImageTk.PhotoImage(im)
                tb.Label(left, image=self._meme_imgtk).pack()
            except Exception:
                tb.Label(left, text="(no image)").pack()
        else:
            tb.Label(left, text="(image not available)").pack()

        # Right: Fake form
        right = tb.Frame(root)
        right.grid(row=0, column=1, sticky=NSEW)

        prompt = (
            "H-hi there...\n"
            "Do you th-think I could have your credit card information, p-please?"
        )
        tb.Label(right, text=prompt, justify=LEFT, wraplength=310, anchor=W).grid(row=0, column=0, columnspan=2, sticky=W, pady=(0, 8))

        tb.Label(right, text="Card number:").grid(row=1, column=0, sticky=E, padx=4, pady=4)
        ent_card = tb.Entry(right, width=32)
        ent_card.grid(row=1, column=1, sticky=W, padx=4, pady=4)

        tb.Label(right, text="Expiry date:").grid(row=2, column=0, sticky=E, padx=4, pady=4)
        ent_exp  = tb.Entry(right, width=32)
        ent_exp.grid(row=2, column=1, sticky=W, padx=4, pady=4)

        tb.Label(right, text="Security code:").grid(row=3, column=0, sticky=E, padx=4, pady=4)
        ent_cvv  = tb.Entry(right, width=32, show="•")
        ent_cvv.grid(row=3, column=1, sticky=W, padx=4, pady=4)

        def do_thanks():
            # Xoá sạch input – không lưu, không gửi
            for e in (ent_card, ent_exp, ent_cvv):
                e.delete(0, END)
            messagebox.showinfo("Totally Safe", "Th-thanks...\n(Don't worry, this window does absolutely nothing)")

        tb.Button(right, text="Th-thanks", bootstyle=INFO, command=do_thanks)\
          .grid(row=4, column=0, columnspan=2, pady=(10, 0))

        # Footnote an toàn
        tb.Label(right,
                 text="⚠️ Fake meme window. No data is saved or transmitted.",
                 foreground="#ffb86c", justify=LEFT, wraplength=310).grid(row=5, column=0, columnspan=2, sticky=W, pady=(6,0))

        # Grid weights
        root.columnconfigure(1, weight=1)
        right.columnconfigure(1, weight=1)
