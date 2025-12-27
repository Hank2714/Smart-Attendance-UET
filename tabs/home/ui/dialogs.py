# ui/dialogs.py
from __future__ import annotations
from typing import Optional, Any, Tuple, Callable
import os
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog, messagebox, Toplevel
from PIL import Image, ImageTk
import cv2
from datetime import date

# --- Optional MTCNN face crop ---
try:
    from mtcnn import MTCNN as _MTCNN_pkg
    _HAS_MTCNN = True
except Exception:
    _HAS_MTCNN = False
    _MTCNN_pkg = None  # type: ignore


# ------------------ helpers ------------------
def _center_on_parent(win: Toplevel):
    """Đặt cửa sổ vào giữa màn hình (hoặc giữa parent nếu có)."""
    try:
        win.update_idletasks()
        parent = win.master if win.master and win.master.winfo_exists() else None
        if parent:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            ww = win.winfo_width()
            wh = win.winfo_height()
            x = px + (pw - ww) // 2
            y = py + (ph - wh) // 2
        else:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            ww = win.winfo_width()
            wh = win.winfo_height()
            x = (sw - ww) // 2
            y = (sh - wh) // 2
        win.geometry(f"+{max(0,x)}+{max(0,y)}")
    except Exception:
        pass


# ======================================================================
# CreateEmployeeDialog
# ======================================================================
class CreateEmployeeDialog(Toplevel):
    """
    Form tạo nhân viên + chụp/đính kèm ảnh mặt (crop MTCNN nếu có).
    result = (ok: bool, info_dict, face_payload[PIL.Image]|None)
    """
    def __init__(self, parent, last_frame_supplier: Callable[[], Optional[Any]]):
        super().__init__(parent)
        self.title("Create Employee")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        self._last_frame_supplier = last_frame_supplier
        self._captured_img: Optional[Image.Image] = None
        self._preview_tk: Optional[ImageTk.PhotoImage] = None
        self._mode_camera_view = True

        frm = tb.Frame(self)
        frm.pack(fill=BOTH, expand=YES, padx=12, pady=10)

        tb.Label(frm, text="Student ID *").grid(row=0, column=0, sticky=E, padx=6, pady=6)
        tb.Label(frm, text="Full name *").grid(row=1, column=0, sticky=E, padx=6, pady=6)
        tb.Label(frm, text="Email").grid(     row=2, column=0, sticky=E, padx=6, pady=6)
        tb.Label(frm, text="Phone").grid(     row=3, column=0, sticky=E, padx=6, pady=6)
        tb.Label(frm, text="Hire date").grid( row=4, column=0, sticky=E, padx=6, pady=6)

        self.ent_sid   = tb.Entry(frm, width=24)
        self.ent_name  = tb.Entry(frm, width=24)
        self.ent_mail  = tb.Entry(frm, width=24)
        self.ent_phone = tb.Entry(frm, width=24)
        # DateEntry dùng cho thống kê (Daily/Roster) — default là hôm nay
        self.dp_hire   = tb.DateEntry(frm, bootstyle=INFO, width=22, dateformat="%Y-%m-%d")

        self.ent_sid.grid(  row=0, column=1, sticky=W, padx=6, pady=6)
        self.ent_name.grid( row=1, column=1, sticky=W, padx=6, pady=6)
        self.ent_mail.grid( row=2, column=1, sticky=W, padx=6, pady=6)
        self.ent_phone.grid(row=3, column=1, sticky=W, padx=6, pady=6)
        self.dp_hire.grid(  row=4, column=1, sticky=W, padx=6, pady=6)

        cam_box = tb.Labelframe(frm, text="Face (optional but recommended)")
        cam_box.grid(row=0, column=2, rowspan=6, sticky=NS, padx=(12, 0), pady=6)

        self.canvas = tb.Canvas(cam_box, width=280, height=210, highlightthickness=0, bd=0)
        self.canvas.pack(padx=8, pady=(8, 4))

        row_btns = tb.Frame(cam_box)
        row_btns.pack(fill=X, padx=8, pady=(0, 8))

        self.btn_capture = tb.Button(row_btns, text="Capture", bootstyle=INFO, command=self._capture_from_camera)
        self.btn_upload  = tb.Button(row_btns, text="Upload...", bootstyle=INFO, command=self._upload_file)
        self.btn_retake  = tb.Button(row_btns, text="Retake", bootstyle=PRIMARY, command=self._retake, state=DISABLED)

        self.btn_capture.pack(side=LEFT, padx=2)
        self.btn_upload.pack(side=LEFT,  padx=2)
        self.btn_retake.pack(side=LEFT,  padx=8)

        act = tb.Frame(self)
        act.pack(fill=X, padx=12, pady=(0, 10))

        self.btn_ok = tb.Button(act, text="Create", bootstyle=SUCCESS, command=self._do_create)
        self.btn_ok.pack(side=RIGHT, padx=4)
        tb.Button(act, text="Cancel", bootstyle=DANGER, command=self._cancel).pack(side=RIGHT, padx=4)

        self._poll_preview()
        self.result = (False, {}, None)

        _center_on_parent(self)

    # ----- live preview loop -----
    def _poll_preview(self):
        if self._mode_camera_view:
            frame = None
            try:
                frame = self._last_frame_supplier()
            except Exception:
                frame = None
            if frame is not None:
                self._render_bgr_to_canvas(frame)
            else:
                self.canvas.delete("all")
                self.canvas.create_text(140, 105, text="(Camera preview)", fill="#999")
        if self.winfo_exists():
            self.after(80, self._poll_preview)

    def _render_bgr_to_canvas(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = min(280 / w, 210 / h)
        nw, nh = int(w * scale), int(h * scale)
        img = Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR)
        self._preview_tk = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(140, 105, image=self._preview_tk)

    def _crop_face_or_original(self, rgb: Any) -> Image.Image:
        if not _HAS_MTCNN:
            return Image.fromarray(rgb)
        try:
            mtcnn = _MTCNN_pkg()
            res = mtcnn.detect_faces(rgb)
            best = None
            best_score = (-1, -1)
            for r in (res or []):
                conf = float(r.get("confidence", 0.0))
                x, y, w, h = r.get("box", [0, 0, 0, 0])
                if w <= 0 or h <= 0:
                    continue
                score = (conf, w * h)
                if score > best_score:
                    best_score = score
                    best = (x, y, w, h)
            if best is None:
                return Image.fromarray(rgb)

            x, y, w, h = best
            H, W = rgb.shape[:2]
            pad = int(0.12 * max(w, h))
            xa, ya = max(0, x - pad), max(0, y - pad)
            xb, yb = min(W, x + w + pad), min(H, y + h + pad)
            crop = rgb[ya:yb, xa:xb]
            if crop.size == 0:
                return Image.fromarray(rgb)
            return Image.fromarray(crop)
        except Exception:
            return Image.fromarray(rgb)

    def _capture_from_camera(self):
        frame = None
        try:
            frame = self._last_frame_supplier()
        except Exception:
            pass
        if frame is None:
            messagebox.showwarning("Camera", "Không có khung hình từ camera.")
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._captured_img = self._crop_face_or_original(rgb)
        self._mode_camera_view = False
        self._render_pil_on_canvas(self._captured_img)
        self.btn_retake.config(state=NORMAL)

    def _upload_file(self):
        path = filedialog.askopenfilename(
            title="Chọn ảnh khuôn mặt",
            filetypes=[("Images", "*.jpg;*.jpeg;*.png;*.bmp;*.webp")]
        )
        if not path:
            return
        try:
            bgr = cv2.imread(path)
            if bgr is None:
                raise ValueError("Không đọc được ảnh.")
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        except Exception:
            messagebox.showwarning("Ảnh", "Tập tin ảnh không hợp lệ.")
            return

        self._captured_img = self._crop_face_or_original(rgb).convert("RGB")
        self._mode_camera_view = False
        self._render_pil_on_canvas(self._captured_img)
        self.btn_retake.config(state=NORMAL)

    def _render_pil_on_canvas(self, im: Image.Image):
        w, h = im.size
        scale = min(280 / w, 210 / h)
        nw, nh = int(w * scale), int(h * scale)
        im2 = im.resize((nw, nh), Image.BILINEAR)
        self._preview_tk = ImageTk.PhotoImage(im2)
        self.canvas.delete("all")
        self.canvas.create_image(140, 105, image=self._preview_tk)

    def _retake(self):
        self._captured_img = None
        self._mode_camera_view = True
        self.btn_retake.config(state=DISABLED)

    def _do_create(self):
        sid_raw = self.ent_sid.get().strip()
        name    = self.ent_name.get().strip()
        email   = (self.ent_mail.get().strip() or None)
        phone   = (self.ent_phone.get().strip() or None)

        # hire_date: trả về datetime/date tuỳ ttkbootstrap version -> normalize về datetime.date
        hire_dt: date | None = None
        try:
            hd = self.dp_hire.get_date()
            hire_dt = hd.date() if hasattr(hd, "date") else hd
        except Exception:
            hire_dt = None

        if not sid_raw.isdigit():
            messagebox.showwarning("Dữ liệu", "Student ID phải là số dương.")
            return
        if not name:
            messagebox.showwarning("Thiếu", "Vui lòng nhập Full name.")
            return

        info = {
            "student_id": int(sid_raw),
            "full_name": name,
            "email": email,
            "phone": phone,
            "hire_date": hire_dt,
        }
        face_payload = self._captured_img if self._captured_img is not None else None
        self.result = (True, info, face_payload)
        self.destroy()

    def _cancel(self):
        self.result = (False, {}, None)
        self.destroy()


# ======================================================================
# ChangeFaceDialog
# ======================================================================
class ChangeFaceDialog(Toplevel):
    """
    Chụp/đính kèm ảnh thay thế khuôn mặt hiện tại.
    result = (ok: bool, PIL.Image|None)
    """
    def __init__(self, parent, last_frame_supplier: Callable[[], Optional[Any]]):
        super().__init__(parent)
        self.title("Change / Upload Face")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        self._last_frame_supplier = last_frame_supplier
        self._mode = tb.StringVar(value="camera")
        self._captured: Optional[Image.Image] = None
        self._preview_tk: Optional[ImageTk.PhotoImage] = None

        row = tb.Frame(self)
        row.pack(fill=BOTH, expand=YES, padx=12, pady=12)

        radios = tb.Frame(row)
        radios.grid(row=0, column=0, columnspan=3, sticky=W, pady=(0, 6))
        tb.Radiobutton(radios, text="Use Camera", variable=self._mode, value="camera",
                       command=self._switch_source).pack(side=LEFT, padx=6)
        tb.Radiobutton(radios, text="Choose File", variable=self._mode, value="file",
                       command=self._switch_source).pack(side=LEFT, padx=6)

        self.canvas = tb.Canvas(row, width=280, height=210, highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=0, columnspan=3, pady=(4, 6))

        self.btn_capture = tb.Button(row, text="Capture", bootstyle=INFO, command=self._capture)
        self.btn_retake  = tb.Button(row, text="Retake", bootstyle=PRIMARY, command=self._retake, state=DISABLED)
        self.btn_upload  = tb.Button(row, text="Upload...", bootstyle=INFO, command=self._upload)

        self.btn_capture.grid(row=2, column=0, sticky=W, padx=4, pady=(2, 8))
        self.btn_retake.grid( row=2, column=1, sticky=W, padx=4, pady=(2, 8))
        self.btn_upload.grid( row=2, column=2, sticky=E, padx=4, pady=(2, 8))

        act = tb.Frame(self)
        act.pack(fill=X, padx=12, pady=(0, 10))
        self.btn_ok = tb.Button(act, text="Apply", bootstyle=SUCCESS, command=self._ok, state=DISABLED)
        self.btn_ok.pack(side=RIGHT, padx=4)
        tb.Button(act, text="Cancel", bootstyle=DANGER, command=self._cancel).pack(side=RIGHT, padx=4)

        self._switch_source()
        self._poll()
        self.result = (False, None)

        _center_on_parent(self)

    def _switch_source(self):
        mode = self._mode.get()
        self._captured = None
        self.btn_ok.config(state=DISABLED)
        self.btn_retake.config(state=DISABLED)
        self.canvas.delete("all")
        if mode == "camera":
            self.btn_capture.config(state=NORMAL)
            self.btn_upload.config(state=DISABLED)
        else:
            self.btn_capture.config(state=DISABLED)
            self.btn_upload.config(state=NORMAL)
            self.canvas.create_text(140, 105, text="(Choose file...)", fill="#999")

    def _poll(self):
        if self._mode.get() == "camera" and self._captured is None:
            frame = None
            try:
                frame = self._last_frame_supplier()
            except Exception:
                pass
            if frame is not None:
                self._render_bgr(frame)
            else:
                self.canvas.delete("all")
                self.canvas.create_text(140, 105, text="(Camera preview)", fill="#999")
        if self.winfo_exists():
            self.after(80, self._poll)

    def _render_bgr(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = min(280 / w, 210 / h)
        nw, nh = int(w * scale), int(h * scale)
        img = Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR)
        self._preview_tk = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(140, 105, image=self._preview_tk)

    def _render_pil(self, im: Image.Image):
        w, h = im.size
        scale = min(280 / w, 210 / h)
        nw, nh = int(w * scale), int(h * scale)
        img2 = im.resize((nw, nh), Image.BILINEAR)
        self._preview_tk = ImageTk.PhotoImage(img2)
        self.canvas.delete("all")
        self.canvas.create_image(140, 105, image=self._preview_tk)

    def _crop_face(self, rgb: Any) -> Image.Image:
        if not _HAS_MTCNN:
            return Image.fromarray(rgb)
        try:
            mtcnn = _MTCNN_pkg()
            res = mtcnn.detect_faces(rgb)
            if not res:
                return Image.fromarray(rgb)
            r = max(res, key=lambda x: float(x.get("confidence", 0.0)))
            x, y, w, h = r.get("box", [0, 0, 0, 0])
            H, W = rgb.shape[:2]
            pad = int(0.12 * max(w, h))
            xa, ya = max(0, x - pad), max(0, y - pad)
            xb, yb = min(W, x + w + pad), min(H, y + h + pad)
            crop = rgb[ya:yb, xa:xb]
            if crop.size == 0:
                return Image.fromarray(rgb)
            return Image.fromarray(crop)
        except Exception:
            return Image.fromarray(rgb)

    def _capture(self):
        if self._mode.get() != "camera":
            return
        frame = None
        try:
            frame = self._last_frame_supplier()
        except Exception:
            pass
        if frame is None:
            messagebox.showwarning("Camera", "Không có khung hình.")
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._captured = self._crop_face(rgb).convert("RGB")
        self._render_pil(self._captured)
        self.btn_retake.config(state=NORMAL)
        self.btn_ok.config(state=NORMAL)

    def _upload(self):
        fp = filedialog.askopenfilename(
            title="Chọn ảnh",
            filetypes=[("Images", "*.jpg;*.jpeg;*.png;*.bmp;*.webp"), ("All files", "*.*")]
        )
        if not fp:
            return
        try:
            bgr = cv2.imread(fp)
            if bgr is None:
                raise ValueError("Không đọc được ảnh.")
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        except Exception:
            messagebox.showwarning("Ảnh", "Ảnh không hợp lệ.")
            return
        self._captured = self._crop_face(rgb).convert("RGB")
        self._render_pil(self._captured)
        self.btn_retake.config(state=NORMAL)
        self.btn_ok.config(state=NORMAL)

    def _retake(self):
        self._captured = None
        self._switch_source()

    def _ok(self):
        if self._captured is None:
            return
        self.result = (True, self._captured)
        self.destroy()

    def _cancel(self):
        self.result = (False, None)
        self.destroy()
