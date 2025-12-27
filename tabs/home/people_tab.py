# tabs/home/people_tab.py
from __future__ import annotations
import os, sys, csv, shutil, hashlib, subprocess, re, time
from typing import Optional, List, Dict, Any
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog, messagebox
import tkinter as tk
from PIL import Image, ImageTk
import cv2
import threading
from datetime import datetime, time as dtime
from queue import Queue


# ---- DB layer ----
from db.db_conn import execute as db_execute, fetch_one, fetch_all
from db.attendance_dal import (
    add_employee, list_employees, deactivate_employee, delete_face_row,
    get_face, upsert_face, search_employees, insert_attendance_log
)

# ---- Hardware Layer ----
from hardware.uart_daemon import UARTDaemon

# ---- UI pieces ----
from ..base import PlaceholderMixin
from .ui.widgets import StatCard
from .ui.dialogs import CreateEmployeeDialog, ChangeFaceDialog

# ---- Services ----
from .services.camera_daemon import CameraDaemon
from .services.recog_daemon import RecognitionDaemon

# ---- DND optional ----
try:
    from tkinterdnd2 import DND_FILES
    DND_ENABLED = True
except Exception:
    DND_ENABLED = False

# --- Bóc tách Unicode ---
import unicodedata

def _ascii_no_diacritics(s: str) -> str:
    if not s:
        return s
    s_norm = unicodedata.normalize("NFKD", s)
    s_ascii = "".join(ch for ch in s_norm if not unicodedata.combining(ch))
    s_ascii = s_ascii.replace("đ", "d").replace("Đ", "D")
    return " ".join(s_ascii.split())

APP_BASE  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FACES_DIR = os.path.join(APP_BASE, "data", "faces")
os.makedirs(FACES_DIR, exist_ok=True)

try:
    from ..attendance.logs import push_not_in_shift
except Exception:
    push_not_in_shift = None

# --- Khung giờ làm ---
_SHIFT_START = dtime(7, 0, 0)
_SHIFT_END = dtime(17,0,0)

_ROW_HEIGHT = 26

class PeopleTab(tb.Frame, PlaceholderMixin):
    def __init__(self, parent, camera_index: int = 0):
        super().__init__(parent)
        self._started_once = False
        self._preview_small_imgtk = None
        self._sort_state = {}
        self._search_after_id = None
        self._initial_form = {}
        self._working_now = False
        self._saving_now = False
        self._empty_iid = None
        self._import_guide_suppress = False

        # recognition gate
        self._scan_active = False
        self._scan_deadline = 0.0

        self._scan_timeout_id = None
        self._cooldown_until = 0.0

        self._scan_token = 0
        self._scan_result = None

        self._await_hw_ready = False

        self._scan_recognized = False
        self._scan_committed = False

        # camera/recog state
        self._last_frame_bgr = None
        self._last_frame_rgb_rs = None
        self._cam_status_var = tb.StringVar(value="Camera: starting…")
        self._viz: Optional[Dict[str, Any]] = None

        # ---- Warning banner (camera / UART) ----
        self._warn_var = tb.StringVar(value="")
        self._warn_cam = False
        self._warn_uart = False

        # canvas image cache (double-buffer)
        self._cam_img_item = None
        self._cam_tk: Optional[ImageTk.PhotoImage] = None
        self._last_draw_ts = 0.0
        # --- UI draw tick
        self._ui_target_fps = 15.0
        self._ui_draw_period_ms = max(10, int(1000.0 / float(self._ui_target_fps)))
        self._ui_draw_job = None
        self._pending_draw = False  # gộp nhiều on_frame vào 1 lần draw

        # --- Coalese status/viz updates
        self._cam_status_after_id = None
        self._cam_status_pending = None

        self._recog_status_after_id = None
        self._recog_status_pending = None

        self._viz_after_id = None
        self._viz_pending = None

        self._frame_seq = 0 #tăng mỗi khi nhận frame mới
        self._draw_seq = 0 #seq gần nhất đã vẽ

        #Tạo queue cho 1 frame duy nhất
        self._frame_queue = Queue(maxsize=1)

        #camera Health Timer
        self._last_frame_ts = 0.0
        self._cam_fail_count = 0
        self.after(1200, self._camera_health_watchdog)

        # style
        style = tb.Style()
        style.configure("Treeview", rowheight=_ROW_HEIGHT)
        style.configure('Card.TFrame', background=style.colors.bg)

        # build UI
        self._build_ui()

        # fill data
        self.refresh()

        # cleanup khi app đóng
        self.bind("<Destroy>", self._on_destroy, add="+")

        #camera_index
        self._camera_index = camera_index

    def _start_services_once(self, camera_index: int | None = None):
        if getattr(self, "_services_started", False):
            return
        self._services_started = True

        # nếu muốn giữ camera_index từ __init__
        if camera_index is None:
            camera_index = getattr(self, "_camera_index", 0)

        # start camera daemon
        self._cam_daemon = CameraDaemon(
            camera_index,
            on_frame=self._on_camera_frame,
            on_status=lambda s: self._set_cam_status(s),
            target_fps=30, width=640, height=480
        )
        self._cam_daemon.start()

        # start recognition daemon
        self._recog_daemon = RecognitionDaemon(
            last_frame_supplier=self._get_last_frame,
            lib_supplier=self._build_face_library,
            on_status=self._on_recog_status_guarded,
            on_hit=lambda eid, sid, name: self._on_recognized(eid, sid, name),
            on_visual=self._set_viz,
            period_sec=1.0, threshold=0.40, conf_min=0.90, min_size_px=80
        )
        self._recog_daemon.start()

        # start uart
        self._uart = UARTDaemon(
            on_person_detected=self._on_sensor_trigger,
            on_ready=self._on_hw_ready,
            debug=True
        )
        self._uart.start()

        # ✅ Poll UART connection (đúng nghĩa “cắm ATmega / mở COM được”)
        if not hasattr(self, "_uart_poll_job") or self._uart_poll_job is None:
            self._uart_poll_job = self.after(800, self._poll_uart_connection)

        # start ui draw tick AFTER tab is ready
        if self._ui_draw_job is None:
            self._ui_draw_job = self.after(self._ui_draw_period_ms, self._ui_draw_tick)


    def on_tab_selected(self):
            # Start services ONLY once
            if not getattr(self, "_started_once", False):
                self._started_once = True
                # start sau khi UI thật sự rảnh
                self.after_idle(self._start_services_once)

            # đảm bảo draw loop luôn sống
            if getattr(self, "_ui_draw_job", None) is None:
                self._ui_draw_job = self.after(self._ui_draw_period_ms, self._ui_draw_tick)

    def on_tab_deselected(self):
        # Dừng draw loop khi rời tab để khỏi backlog
        if getattr(self, "_ui_draw_job", None):
            try:
                self.after_cancel(self._ui_draw_job)
            except Exception:
                pass
            self._ui_draw_job = None



    # ---------- Public ----------
    def refresh(self, select_eid: int | None = None):
        self._refresh_employees(select_eid=select_eid)

    # ---------- UI ----------
    def _build_ui(self):
        top = tb.Frame(self); top.pack(fill=X, padx=8, pady=8)

        tb.Label(top, text="Search:").pack(side=LEFT, padx=(0, 6))
        self.ent_search = tb.Entry(top, width=28)
        self.ent_search.pack(side=LEFT, padx=(0, 8))
        self._attach_placeholder(self.ent_search, "Tìm theo student_id hoặc tên…")

        tb.Label(top, text="Status:").pack(side=LEFT, padx=(6, 6))
        self.cmb_status = tb.Combobox(
            top, width=10, state="readonly",
            values=["Active", "Inactive", "All"]
        )
        self.cmb_status.set("Active")
        self.cmb_status.pack(side=LEFT, padx=(0, 12))
        self.cmb_status.bind("<<ComboboxSelected>>", lambda e: self._refresh_employees())

        tb.Button(top, text="Import CSV", bootstyle=INFO, command=self._import_csv).pack(side=LEFT, padx=4)
        tb.Button(top, text="Export CSV", bootstyle=WARNING, command=self._export_employees).pack(side=LEFT, padx=4)

        # Search behavior
        self.ent_search.bind("<Return>", lambda e: self._search())
        self.ent_search.bind("<KeyRelease>", self._on_search_key)

        grid = tb.Frame(self); grid.pack(fill=BOTH, expand=YES, padx=8, pady=8)
        grid.columnconfigure(0, weight=3, uniform='col')
        grid.columnconfigure(1, weight=2, uniform='col')
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=0)

        # LEFT: list
        left = tb.Labelframe(grid, text="Employees"); left.grid(row=0, column=0, sticky="nsew", padx=(0,8))
        cols = ("employee_id", "student_id", "full_name", "email", "phone", "face", "status")
        self.tree = tb.Treeview(left, columns=cols, show="headings", selectmode="browse", displaycolumns=cols)

        for c in cols:
            head_anchor = E if c == 'student_id' else W
            self.tree.heading(c, text=c, anchor=head_anchor, command=lambda cc=c: self._sort_by(cc))
        self.tree.column("employee_id", width=80, anchor=E, stretch=False)
        self.tree.column("student_id",  width=100, anchor=E, stretch=False)
        self.tree.column("full_name",   width=220, anchor=W, stretch=True)
        self.tree.column("email",       width=220, anchor=W, stretch=True)
        self.tree.column("phone",       width=100, anchor=W, stretch=False)
        self.tree.column("face",        width=45,  anchor=CENTER, stretch=False)
        self.tree.column("status",      width=65,  anchor=CENTER, stretch=False)

        self.tree.tag_configure("odd", background="#1f1f1f")
        self.tree.tag_configure("even", background="#151515")
        self.tree.tag_configure("inactive", foreground="#9aa0a6", font=("", 9, "italic"))

        vsb = tb.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self._ctx = tb.Menu(self, tearoff=0)
        self._ctx.add_command(label="Copy employee_id", command=self._ctx_copy_id)
        self._ctx.add_command(label="Copy student_id", command=lambda: self._ctx_copy_col(1))
        self._ctx.add_command(label="Copy email",       command=lambda: self._ctx_copy_col(3))
        self._ctx.add_separator()
        self._ctx.add_command(label="Open image folder", command=self._open_image_folder)
        self._ctx.add_command(label="Remove face",       command=self._on_remove_face_only)
        self._ctx.add_separator()
        self._ctx.add_command(label="Deactivate (soft)", command=self._deactivate_selected_emp)
        self.tree.bind("<Button-3>", self._popup_ctx)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self._load_selected())

        stats_row = tb.Frame(grid)
        stats_row.grid(row=1, column=0, sticky="ew", padx=(0,8))
        stats_row.columnconfigure(0, weight=1)
        stats_row.columnconfigure(1, weight=1)
        self.card_active   = StatCard(stats_row, title="Active",   value="0", bootstyle="success")
        self.card_inactive = StatCard(stats_row, title="Inactive", value="0", bootstyle="secondary")
        self.card_active.grid(row=0, column=0, sticky="ew", padx=(0,6), pady=(6,0))
        self.card_inactive.grid(row=0, column=1, sticky="ew", padx=(6,0), pady=(6,0))

        # RIGHT: camera + preview
        right = tb.Frame(grid); right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.columnconfigure(0, weight=1)

        # 3 rows: (warning+camera pick), (live cam), (preview)
        right.rowconfigure(0, weight=0)
        right.rowconfigure(1, weight=1, minsize=320)
        right.rowconfigure(2, weight=0)

        # ---- Top-right: Warning banner + Camera picker (aligned right) ----
        topr = tb.Frame(right)
        topr.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        topr.columnconfigure(0, weight=1)

        self.lbl_warn = tb.Label(
            topr,
            textvariable=self._warn_var,
            bootstyle="danger",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            padding=(8, 4)
        )
        self.lbl_warn.grid(row=0, column=0, sticky="ew")
        self.lbl_warn.grid_remove()  # mặc định ẩn

        cam_pick = tb.Frame(topr)
        cam_pick.grid(row=1, column=0, sticky="e", pady=(6, 0))

        tb.Label(cam_pick, text="Camera:").pack(side=LEFT, padx=(0, 6))
        self.cmb_camera = tb.Combobox(cam_pick, width=14, state="readonly", values=[])
        self.cmb_camera.pack(side=LEFT)

        tb.Button(
            cam_pick, text="↻", width=3, bootstyle=SECONDARY,
            command=self._populate_camera_combo
        ).pack(side=LEFT, padx=(6, 0))

        def _on_cam_pick(_=None):
            txt = self.cmb_camera.get().strip()
            try:
                idx = int(txt.split()[-1])
            except Exception:
                return
            self._switch_camera(idx)

        self.cmb_camera.bind("<<ComboboxSelected>>", _on_cam_pick)

        # init list
        self._populate_camera_combo()

        # ---- Live Camera + Recognition Status ----
        cam = tb.Labelframe(right, text="Live Camera + Recognition Status")
        cam.grid(row=1, column=0, sticky="nsew", padx=(0,0))

        cam.columnconfigure(0, weight = 1)
        cam.rowconfigure(0, weight = 1)
        cam.rowconfigure(1, weight = 0)

        self.cam_canvas = tb.Canvas(cam, width=360, height=270, highlightthickness=0, bd=0)
        self.cam_canvas.pack(fill=BOTH, expand=YES, padx=8, pady=6)

        self._canvas_wh = (360, 270)
        self.cam_canvas.bind("<Configure>", self._on_cam_canvas_configure, add="+")

        self.lbl_cam_status = tb.Label(
            cam, textvariable=self._cam_status_var,
            font=("Segoe UI", 10, "bold"),
            foreground="#CCCCCC",
            anchor="w",
            wraplength = 10000,
            justify='left',
            padding = (0, 0)
        )
        self.lbl_cam_status.pack(fill=X, padx=10, pady=(0,8))

        # ---- Preview ----
        preview = tb.Labelframe(right, text="Preview Info (compact)")
        preview.grid(row=2, column=0, sticky="ew", pady=(8,0))
        preview.columnconfigure(1, weight=1)
        preview.columnconfigure(3, weight=1)

        # ===== Fixed-size face preview box (không cho phình layout) =====
        self.face_box = tb.Frame(preview, width=110, height=110)
        self.face_box.grid(row=0, column=0, rowspan=3, sticky="w", padx=(8, 12), pady=(6, 6))
        self.face_box.grid_propagate(False)  # QUAN TRỌNG: chặn widget con làm phình khung

        self.lbl_face_small = tk.Label(self.face_box, text="(No face)", anchor="center")
        self.lbl_face_small.place(relx=0.5, rely=0.5, anchor="center")  # luôn nằm giữa box

        if DND_ENABLED:
            try:
                self.lbl_face_small.drop_target_register("DND_Files")
                self.lbl_face_small.dnd_bind("<<Drop>>", self._on_drop_image)
            except Exception:
                pass

        self.lbl_face_small.bind("<Double-1>", lambda e: self._open_image_file())

        self.lbl_face_info = tb.Label(preview, text="", bootstyle=SECONDARY)
        self.lbl_face_info.grid(row=3, column=0, sticky="w", padx=(8, 12), pady=(0, 8))


        tb.Label(preview, text="Employee ID").grid(row=0, column=1, sticky=E, padx=6, pady=4)
        self.ent_empid = tb.Entry(preview, state="readonly", width=12)
        self.ent_empid.grid(row=0, column=2, sticky=W, padx=6, pady=4)

        tb.Label(preview, text="Full name").grid(row=1, column=1, sticky=E, padx=6, pady=4)
        self.ent_name = tb.Entry(preview); self.ent_name.grid(row=1, column=2, sticky=EW, padx=6, pady=4)

        tb.Label(preview, text="Phone").grid(row=2, column=1, sticky=E, padx=6, pady=4)
        self.ent_phone = tb.Entry(preview, width=18); self.ent_phone.grid(row=2, column=2, sticky=W, padx=6, pady=4)

        tb.Label(preview, text="Hire date").grid(row=3, column=1, sticky=E, padx=6, pady=4)
        self.ent_hire_date = tb.Entry(preview, state="readonly", width=18)
        self.ent_hire_date.grid(row=3, column=2, sticky=W, padx=6, pady=4)

        tb.Label(preview, text="Student ID").grid(row=0, column=3, sticky=E, padx=6, pady=4)
        self.ent_sid = tb.Entry(preview, width=18); self.ent_sid.grid(row=0, column=4, sticky=W, padx=6, pady=4)

        tb.Label(preview, text="Email").grid(row=1, column=3, sticky=E, padx=6, pady=4)
        self.ent_mail = tb.Entry(preview); self.ent_mail.grid(row=1, column=4, sticky=EW, padx=6, pady=4)

        tb.Label(preview, text="Status").grid(row=2, column=3, sticky=E, padx=6, pady=4)
        self.var_status = tb.StringVar(value="Active")
        self.cmb_status_edit = tb.Combobox(preview, textvariable=self.var_status, state="readonly",
                                   width=10, values=["Active", "Inactive"])
        self.cmb_status_edit.grid(row=2, column=4, sticky=W, padx=6, pady=4)
        self.cmb_status_edit.bind("<<ComboboxSelected>>",
                                  lambda e: self._update_dirty_state())

        act = tb.Frame(preview); act.grid(row=4, column=0, columnspan=5, sticky="ew", padx=8, pady=(0, 8))
        self.btn_create = tb.Button(act, text="Create", bootstyle=SUCCESS, command=self._open_create_dialog)
        self.btn_change = tb.Button(act, text="Change/Upload Face", bootstyle=INFO, command=self._change_face)
        self.btn_save   = tb.Button(act, text="Save changes", bootstyle=PRIMARY,
                                    command=self._save_change, state=DISABLED)
        self.btn_create.pack(side=LEFT, padx=4)
        self.btn_change.pack(side=LEFT, padx=4)
        self.btn_save.pack(side=LEFT, padx=4)

        for ent in (self.ent_sid, self.ent_name, self.ent_mail, self.ent_phone):
            ent.bind("<KeyRelease>", lambda e: self._update_dirty_state(), add="+")
            ent.bind("<FocusOut>",  lambda e: self._update_dirty_state(), add="+")
        self.bind_all("<Control-s>", lambda e: self._save_change())
        for ent in (self.ent_sid, self.ent_name, self.ent_mail, self.ent_phone):
            ent.bind("<Return>", lambda e: self._save_change())
        self.bind_all("<Escape>", lambda e: self._form_clear())

    #Set Camera to Use
    def _enumerate_camera_indices(self, max_probe: int = 8):
        indices = []
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else 0

        for i in range(max_probe):
            cap = None
            try:
                cap = cv2.VideoCapture(i, backend)
                if cap is not None and cap.isOpened():
                    indices.append(i)
            except Exception:
                pass
            finally:
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
        return indices

    def _populate_camera_combo(self):
        cams = self._enumerate_camera_indices(max_probe=8)
        self._cam_indices = cams

        if not cams:
            # No camera found
            try:
                self.cmb_camera.configure(values=[])
                self.cmb_camera.set("(No camera)")
                self.cmb_camera.configure(state="disabled")
            except Exception:
                pass
            self._warn_cam = True
            self._update_warning()
            return

        # Has cameras
        try:
            self.cmb_camera.configure(state="readonly")
        except Exception:
            pass

        values = [f"Camera {i}" for i in cams]
        self.cmb_camera.configure(values=values)

        cur = getattr(self, "_camera_index", 0)
        if cur not in cams:
            cur = cams[0]
        self._camera_index = cur
        self.cmb_camera.set(f"Camera {cur}")

        # clear camera warning
        self._warn_cam = False
        self._update_warning()

    def _update_warning(self):
        msgs = []
        if getattr(self, "_warn_cam", False):
            msgs.append("⚠ Không tìm thấy Camera. Hãy cắm webcam hoặc kiểm tra quyền truy cập.")
        if getattr(self, "_warn_uart", False):
            msgs.append("⚠ Không tìm thấy ATmega (UART). Cảm biến không kích hoạt scan tự động.")

        text = "  |  ".join(msgs)
        try:
            self._warn_var.set(text)
            if text:
                self.lbl_warn.grid()
            else:
                self.lbl_warn.grid_remove()
        except Exception:
            pass

    def _poll_uart_connection(self):
        """
        Cảnh báo UART dựa trên trạng thái serial OPEN/CLOSED
        (không dựa vào RD/on_ready vì RD là handshake cuối chu kỳ).
        """
        try:
            ud = getattr(self, "_uart", None)
            ser = getattr(ud, "ser", None) if ud else None
            is_open = bool(ser and getattr(ser, "is_open", False))

            self._warn_uart = (not is_open)
            self._update_warning()
        except Exception:
            self._warn_uart = True
            self._update_warning()
        finally:
            # chạy lại định kỳ
            try:
                self._uart_poll_job = self.after(800, self._poll_uart_connection)
            except Exception:
                self._uart_poll_job = None


    def _switch_camera(self, new_index: int):
        """Đổi camera runtime: stop daemon cũ -> start daemon mới."""
        self._camera_index = int(new_index)

        # nếu service chưa start thì chỉ lưu index (khi start sẽ dùng)
        if not getattr(self, "_services_started", False):
            return

        # stop old
        try:
            cd = getattr(self, "_cam_daemon", None)
            if cd is not None:
                cd.stop()
                cd.join(timeout=1.0)
        except Exception:
            pass
        self._cam_daemon = None

        # clear frame để tránh hiển thị frame cũ
        try:
            self._last_frame_bgr = None
            while not self._frame_queue.empty():
                self._frame_queue.get_nowait()
        except Exception:
            pass

        # start new
        try:
            self._cam_daemon = CameraDaemon(
                self._camera_index,
                on_frame=self._on_camera_frame,
                on_status=lambda s: self._set_cam_status(s),
                target_fps=30, width=640, height=480
            )
            self._cam_daemon.start()
        except Exception as e:
            self._set_cam_status(f"Camera: switch failed ({e})")
            self._warn_cam = True
            self._update_warning()


    # ---------- Recognition status (THREAD-SAFE) ----------
    def _set_cam_status(self, text: str):
        try:
            txt = (text or "").replace("\r", " ").replace("\n", " ").strip()
            if len(txt) > 160:
                txt = txt[:157] + "..."
            self._cam_status_pending = txt
        except Exception:
            pass

    #Hàm chặn status
    def _on_recog_status_guarded(self, msg: str, mode: str = "idle"):
        """
        🔥 CHỈ cho RecognitionDaemon update UI
        khi đang trong phiên scan hợp lệ
        """
        if not self._scan_active:
            return

        if time.time() > self._scan_deadline:
            return
        if self._scan_result is not None:
            return
        self._update_recog_status(msg, mode)


    def _update_recog_status(self, text: str, mode: str = "info"):
        try:
            txt = (text or "").replace("\r", " ").replace("\n", " ").strip()
            if len(txt) > 160:
                txt = txt[:157] + "..."
            key = (txt, mode)
            if getattr(self, "_last_recog_status_key", None) == key:
                return
            self._last_recog_status_key = key
            self._recog_status_pending = key
        except Exception:
            pass

    def _on_recognized(self, eid: int, sid: int, name: str):
        if not self._scan_active:
            return
        if getattr(self, "_scan_recognized", False):
            return
        self._scan_recognized = True
        if self._scan_result is not None:
            return

        # chốt kết quả
        self._scan_result = "success"
        self._scan_active = False
        self._scan_deadline = 0.0
        self._await_hw_ready = True

        def _ui():
            # hủy timeout FAIL
            if self._scan_timeout_id is not None:
                try:
                    self.after_cancel(self._scan_timeout_id)
                except Exception:
                    pass
                self._scan_timeout_id = None

            # ✅ success xong cũng pause recog (đợi sensor lần sau)
            try:
                if getattr(self, "_recog_daemon", None):
                    self._recog_daemon.pause()
            except Exception:
                pass

            self._viz = None

            clean = _ascii_no_diacritics(name)
            self._update_recog_status(f"✅ Recognized: {sid} — {clean}", "ok")

            # chọn đúng dòng + show ảnh
            try:
                self._select_employee_in_tree(eid)
                self._show_face_small()
            except Exception:
                pass

            # ===== LOG DB (background) =====
            now_t = datetime.now().time()
            in_shift = (_SHIFT_START <= now_t <= _SHIFT_END)

            def _bg_log():
                try:
                    if in_shift:
                        insert_attendance_log(eid)  # ✅ ghi DB để Logs tab fetch được
                    else:
                        if callable(push_not_in_shift):
                            push_not_in_shift(eid, f"{sid} — {name}")
                except Exception as e:
                    print(f"[DB_LOG_ERROR] eid={eid} sid={sid} name={name} err={e!r}")
                    try:
                        self.after(0, lambda: self._update_recog_status(f"⚠ DB log failed: {e}", "warn"))
                    except Exception:
                        pass

            threading.Thread(target=_bg_log, daemon=True).start()
            # ===============================

            # gửi UART success
            try:
                if self._uart:
                    self._uart.send_success(sid, resend=True)
                    self._scan_committed = True
            except Exception:
                pass

        self.after(0, _ui)


    def _set_viz(self, viz):
        """
        Có thể bị gọi từ recog thread -> chỉ set dữ liệu (không đụng Tk).
        """
        try:
            self._viz = viz
            self._viz_ts = time.time()
        except Exception:
            pass

    def _on_camera_frame(self, frame_bgr):
        self._last_frame_ts = time.time()
        self._cam_fail_count = 0

        # ✅ IMPORTANT: luôn cập nhật last_frame cho recognition
        # (không phụ thuộc tab có "viewable" hay không)
        try:
            if not hasattr(self, "_frame_lock"):
                self._frame_lock = threading.Lock()
            with self._frame_lock:
                self._last_frame_bgr = frame_bgr
                self._frame_seq += 1
        except Exception:
            pass

        # UI vẫn dùng queue (maxsize=1) để vẽ khi tab đang mở
        try:
            if self._frame_queue.full():
                try:
                    self._frame_queue.get_nowait()
                except Exception:
                    pass
            self._frame_queue.put_nowait(frame_bgr)
        except Exception:
            pass

    def _camera_health_watchdog(self):
        try:
            now = time.time()

            # quá 2.5s không có frame -> coi như camera chết tạm
            if self._last_frame_ts and (now - self._last_frame_ts > 2.5):
                self._cam_fail_count += 1
            else:
                self._cam_fail_count = 0

            # fail liên tiếp 3 lần thì fallback
            if self._cam_fail_count >= 3:
                self._cam_fail_count = 0

                cams = self._enumerate_camera_indices(max_probe=8)

                # --- Không còn cam nào ---
                if not cams:
                    self._warn_cam = True
                    self._update_warning()
                    try:
                        self.cmb_camera.configure(values=[])
                        self.cmb_camera.set("(No camera)")
                        self.cmb_camera.configure(state="disabled")
                    except Exception:
                        pass
                    return

                # --- Có cam: update list combobox trước ---
                try:
                    values = [f"Camera {i}" for i in cams]
                    self.cmb_camera.configure(values=values, state="readonly")
                except Exception:
                    pass

                cur = getattr(self, "_camera_index", None)

                # nếu camera hiện tại không còn trong list -> chọn cam khác
                if cur not in cams:
                    pick = cams[0]
                else:
                    # đang có trong list nhưng bị "chết" -> ưu tiên nhảy sang cam khác nếu có
                    pick = next((i for i in cams if i != cur), cur)

                # set combobox cho khớp
                try:
                    self.cmb_camera.set(f"Camera {pick}")
                except Exception:
                    pass

                # clear warning camera vì đã tìm được cam
                self._warn_cam = False
                self._update_warning()

                # switch nếu cần
                if pick != cur:
                    self._switch_camera(pick)

        except Exception:
            pass
        finally:
            self.after(1200, self._camera_health_watchdog)


    def _on_cam_canvas_configure(self, event):
        try:
            w = max(1, int(event.width))
            h = max(1, int(event.height))
            if (w, h) != getattr(self, "_canvas_wh", (0, 0)):
                self._canvas_wh = (w, h)
                # nếu size đổi thì buộc tạo lại PhotoImage đúng size
                self._cam_tk = None
                self._cam_tk_size = None
                # item vẫn giữ cũng được, nhưng reset cho sạch
                # self._cam_img_item = None
        except Exception:
            pass

    def _post_to_ui(self, fn):
        """
        Thread-safe: worker threads (camera/recog/uart) nhờ Tk thread chạy fn().
        Thêm giới hạn để tránh backlog làm UI lag/đứng.
        """
        try:
            if not hasattr(self, "_ui_task_q"):
                self._ui_task_q = []
                self._ui_task_lock = threading.Lock()

            with self._ui_task_lock:
                self._ui_task_q.append(fn)
                # giới hạn queue, quá thì drop bớt
                if len(self._ui_task_q) > 200:
                    del self._ui_task_q[:50]
        except Exception:
            pass

    def _draw_latest_frame(self):
        if not self.winfo_exists():
            return

        # snapshot rgb_resized + seq
        rgb = None
        seq = 0
        try:
            lock = getattr(self, "_frame_lock", None)
            if lock is None:
                rgb = getattr(self, "_last_frame_rgb_rs", None)
                seq = getattr(self, "_frame_seq", 0)
            else:
                with lock:
                    rgb = getattr(self, "_last_frame_rgb_rs", None)
                    seq = getattr(self, "_frame_seq", 0)
        except Exception:
            return

        if rgb is None:
            return

        # chỉ vẽ khi có frame mới
        if seq == getattr(self, "_draw_seq", -1):
            return

        # throttle theo target fps (UI)
        now = time.perf_counter()
        last_ts = getattr(self, "_last_draw_ts", 0.0)
        target = max(1, int(getattr(self, "_ui_target_fps", 15)))
        if (now - last_ts) < (1.0 / float(target)):
            return

        self._last_draw_ts = now
        self._draw_seq = seq

        try:
            cw, ch = getattr(self, "_canvas_wh", (0, 0))
            if cw <= 1 or ch <= 1:
                return

            # copy nhẹ (canvas-size) chỉ khi cần overlay
            draw_rgb = rgb
            viz = getattr(self, "_viz", None)
            viz_ts = getattr(self, "_viz_ts", 0.0)

            if viz is not None and (time.time() - viz_ts) <= 0.8:
                # vẽ trên ảnh đã resize (ít pixel hơn nhiều)
                draw_rgb = rgb.copy()

                # scale bbox từ frame gốc 640x480 -> canvas (cw,ch)
                # nếu camera daemon đổi size, lấy từ frame gốc hiện tại
                src_w = 640
                src_h = 480
                try:
                    # ưu tiên lấy từ frame gốc nếu có
                    lock = getattr(self, "_frame_lock", None)
                    if lock is None:
                        fb = getattr(self, "_last_frame_bgr", None)
                    else:
                        with lock:
                            fb = getattr(self, "_last_frame_bgr", None)
                    if fb is not None:
                        src_h, src_w = fb.shape[:2]
                except Exception:
                    pass

                sx = cw / float(src_w)
                sy = ch / float(src_h)

                x = y = w = h = None
                try:
                    if "bbox" in viz:
                        x, y, w, h = viz.get("bbox")
                    elif "box" in viz:
                        b = viz.get("box")
                        if b and len(b) == 4:
                            x1, y1, x2, y2 = b
                            if x2 > x1 and y2 > y1:
                                x, y, w, h = int(x1), int(y1), int(x2 - x1), int(y2 - y1)
                            else:
                                x, y, w, h = int(x1), int(y1), int(x2), int(y2)
                except Exception:
                    x = y = w = h = None

                if x is not None and y is not None and w is not None and h is not None:
                    try:
                        # scale to canvas
                        x = int(x * sx)
                        y = int(y * sy)
                        w = int(w * sx)
                        h = int(h * sy)

                        # màu viz đang BGR -> đổi sang RGB
                        bgr = viz.get("color", (0, 255, 0))
                        color = (int(bgr[2]), int(bgr[1]), int(bgr[0]))  # RGB

                        # dùng cv2 trên RGB: cần chuyển sang BGR? (cv2 dùng BGR)
                        # trick: vẽ bằng cv2 trên array RGB nhưng color là (R,G,B) -> sẽ bị hiểu như BGR.
                        # => đổi lại thành BGR để cv2 vẽ đúng trên mảng RGB
                        cv2_color = (color[2], color[1], color[0])

                        cv2.rectangle(draw_rgb, (x, y), (x + w, y + h), cv2_color, 2)
                        label = viz.get("label", "")
                        if label:
                            cv2.putText(draw_rgb, str(label), (x, max(0, y - 8)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, cv2_color, 2)
                    except Exception:
                        pass

            pil = Image.fromarray(draw_rgb)

            need_new = (
                getattr(self, "_cam_tk", None) is None or
                getattr(self, "_cam_tk_size", None) != (cw, ch)
            )

            if need_new:
                self._cam_tk = ImageTk.PhotoImage(pil)
                self._cam_tk_size = (cw, ch)
                if getattr(self, "_cam_img_item", None) is None:
                    self._cam_img_item = self.cam_canvas.create_image(0, 0, image=self._cam_tk, anchor="nw")
                else:
                    self.cam_canvas.itemconfig(self._cam_img_item, image=self._cam_tk)
            else:
                try:
                    self._cam_tk.paste(pil)
                except Exception:
                    self._cam_tk = ImageTk.PhotoImage(pil)
                    self._cam_tk_size = (cw, ch)
                    if getattr(self, "_cam_img_item", None) is None:
                        self._cam_img_item = self.cam_canvas.create_image(0, 0, image=self._cam_tk, anchor="nw")
                    else:
                        self.cam_canvas.itemconfig(self._cam_img_item, image=self._cam_tk)

        except Exception:
            return



    def _get_last_frame(self):
        if not self._scan_active:
            return None
        if time.time() > self._scan_deadline:
            # Không tự tắt scan ở đây — chờ timeout gửi FAIL hoặc chờ RD reset
            return None
        return self._last_frame_bgr

    def _get_last_frame_for_dialog(self):
        """Supplier cho dialog (Create / Change Face).
        Không gate theo scan => luôn có camera preview."""
        return self._last_frame_bgr

    # ---------- Helpers / Data ----------
    def _get_selected_id(self):
        it = self.tree.focus()
        return int(self.tree.item(it)["values"][0]) if it else None

    def _get_focused_eid(self):
        it = self.tree.focus()
        if not it or self._is_placeholder_iid(it):
            return None
        vals = self.tree.item(it).get("values", [])
        if not vals: return None
        v = vals[0]
        return int(v) if str(v).isdigit() else None

    def _status_mode(self):
        val = (self.cmb_status.get() or "Active").lower()
        if val.startswith("inact"): return "inactive"
        if val.startswith("all"):   return "all"
        return "active"

    def _insert_row(self, r, index):
        tags = ("even" if index % 2 == 0 else "odd",)
        if str(r.get("active", 1)) == "0":
            tags = tags + ("inactive",)
        try:
            fr = get_face(r["employee_id"])
            face_flag = "✓" if (fr and fr.get("image_path")) else ""
        except Exception:
            face_flag = ""
        status_val = "1" if str(r.get("active", 1)) != "0" else "0"

        self.tree.insert(
            "", END,
            values=(r.get("employee_id"), r.get("student_id"), r.get("full_name"),
                    r.get("email"), r.get("phone"), face_flag, status_val),
            tags=tags
        )

    def _sort_by(self, col):
        items = []
        for iid in self.tree.get_children(""):
            vals = self.tree.item(iid, "values")
            items.append((iid, vals))

        cols_order = ("employee_id", "student_id", "full_name", "email", "phone", "face", "status")
        idx = cols_order.index(col)
        asc = self._sort_state.get(col, True)

        def _key(p):
            v = p[1][idx] if idx < len(p[1]) else ""
            if col in ("employee_id", "student_id"):
                try:
                    return int(v)
                except:
                    return -1 if asc else 10**18
            if col == "status":
                return 1 if str(v) == "1" else 0
            if col == "face":
                return 0 if v == "✓" else 1
            return (v or "").lower()

        items.sort(key=_key, reverse=not asc)
        for i, (iid, _) in enumerate(items):
            self.tree.move(iid, "", i)

        for c in self.tree["columns"]:
            txt = c + (" ▲" if (c == col and asc) else (" ▼" if (c == col and not asc) else ""))
            head_anchor = E if c == 'student_id' else W
            self.tree.heading(c, text=txt, anchor = head_anchor, command=lambda cc=c: self._sort_by(cc))

        self._sort_state[col] = not asc

    def _update_buttons_state(self):
        pass

    def _update_status(self, total=None, shown=None, active_count=None):
        if total is None or active_count is None:
            all_rows = list_employees(active_only=False)
            total = len(all_rows)
            active_count = len([r for r in all_rows if str(r.get("active", 1)) != "0"])
        if shown is None:
            kids = list(self.tree.get_children())
            shown = len(kids) - (1 if (self._empty_iid and self._empty_iid in kids) else 0)
            if shown < 0: shown = 0

        inactive = total - active_count
        try:
            self.card_active.set_value(active_count)
            self.card_inactive.set_value(inactive)
        except Exception:
            pass

    def _on_search_key(self, _=None):
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(300, self._search)

    def _snapshot_form(self):
        self._initial_form = {
            "sid": self.ent_sid.get().strip(),
            "name": self.ent_name.get().strip(),
            "email": self.ent_mail.get().strip(),
            "phone": self.ent_phone.get().strip(),
            "eid": self.ent_empid.get().strip(),
            "status": self.var_status.get().strip(),
        }
        self._update_dirty_state()

    def _update_dirty_state(self):
        cur = {
            "sid": self.ent_sid.get().strip(),
            "name": self.ent_name.get().strip(),
            "email": self.ent_mail.get().strip(),
            "phone": self.ent_phone.get().strip(),
            "eid": self.ent_empid.get().strip(),
            "status": self.var_status.get().strip(),
        }
        dirty = (cur != self._initial_form) and (cur["eid"].isdigit() or self.tree.focus())
        self.btn_save.config(state=(NORMAL if dirty else DISABLED))

    def _form_clear(self):
        self.ent_empid.config(state=NORMAL); self.ent_empid.delete(0, END); self.ent_empid.config(state="readonly")
        for ent in (self.ent_sid, self.ent_name, self.ent_mail, self.ent_phone):
            ent.delete(0, END)

        if hasattr(self, "ent_hire_date"):
            self.ent_hire_date.config(state = NORMAL)
            self.ent_hire_date.delete(0, END)
            self.ent_hire_date.config(state = "readonly")

        self.var_status.set("")
        self.tree.selection_remove(*self.tree.selection())
        self._snapshot_form()
        self._update_buttons_state()

    def _is_placeholder_iid(self, iid: str | None) -> bool:
        return bool(iid) and iid == self._empty_iid

    def _on_tree_select(self, _=None):
        iid = self.tree.focus()
        if self._is_placeholder_iid(iid):
            self.tree.selection_remove(iid)
            self.tree.focus("")
            self._show_face_small()
            self._form_clear()
            return
        self._show_face_small()
        self._load_selected()

    def _status_val_to_digit(self) -> str:
        v = (self.var_status.get() or "").lower()
        if v == "active": return "1"
        if v == "inactive": return "0"
        return ""

    # ---------- Data ops ----------
    def _refresh_employees(self, select_eid: int | None = None):
        mode = self._status_mode()
        if mode == "active":
            rows = list_employees(active_only=True)
        elif mode == "inactive":
            rows = [r for r in list_employees(active_only=False) if "active" in r and str(r["active"]) == "0"]
        else:
            rows = list_employees(active_only=False)

        for i in self.tree.get_children(): self.tree.delete(i)
        self._empty_iid = None

        if not rows:
            self._empty_iid = self.tree.insert(
                "", END,
                values=("", "", "Chưa có nhân viên — dùng Create hoặc Import CSV", "", "", "", ""),
                tags=("inactive",)
            )
        else:
            for idx, r in enumerate(rows):
                self._insert_row(r, idx)

        if select_eid is not None and not self._empty_iid:
            for iid in self.tree.get_children(""):
                vals = self.tree.item(iid, "values")
                if str(vals[0]) == str(select_eid):
                    self.tree.selection_set(iid); self.tree.focus(iid); self.tree.see(iid)
                    break

        self._show_face_small()
        self._update_buttons_state()
        self._update_status()
        if self.tree.focus() and not self._is_placeholder_iid(self.tree.focus()):
            self._load_selected()
        else:
            self._form_clear()

    def _search(self):
        q = self.ent_search.get().strip()
        if getattr(self.ent_search, "_ph_is_on", False): q = ""
        mode = self._status_mode()

        if q:
            try:
                rows = search_employees(q, status=mode)  # type: ignore
            except TypeError:
                rows = search_employees(q)
                if mode != "all":
                    want_active = (mode == "active")
                    rows = [r for r in rows if "active" in r and (str(r["active"]) != "0") == want_active]
        else:
            self._refresh_employees(); return

        for i in self.tree.get_children(): self.tree.delete(i)
        self._empty_iid = None

        if not rows:
            self._empty_iid = self.tree.insert("", END,
                values=("", "", "Không tìm thấy kết quả", "", "", "", ""), tags=("inactive",))
        else:
            for idx, r in enumerate(rows): self._insert_row(r, idx)

        self._update_buttons_state()
        self._update_status()

    # def _load_selected(self):
    #     it = self.tree.focus()
    #     if not it: return
    #     vals = self.tree.item(it).get("values", [])
    #     eid, sid, name, mail, phone = (list(vals) + [None] * 5)[:5]

    #     self.ent_empid.config(state=NORMAL); self.ent_empid.delete(0, END)
    #     self.ent_empid.insert(0, "" if eid is None else str(eid))
    #     self.ent_empid.config(state="readonly")

    #     self.ent_sid.delete(0, END);   self.ent_sid.insert(0, "" if sid   is None else str(sid))
    #     self.ent_name.delete(0, END);  self.ent_name.insert(0, "" if name is None else str(name))
    #     self.ent_mail.delete(0, END);  self.ent_mail.insert(0, "" if mail is None else str(mail))
    #     self.ent_phone.delete(0, END); self.ent_phone.insert(0, "" if phone is None else str(phone))

    #     try:
    #         if eid is not None:
    #             row = fetch_one("SELECT active FROM employees WHERE employee_id=%s", (eid,))
    #             digit = "1" if row and row.get("active") is None else str(row["active"])
    #             txt = "Active" if digit == "1" else "Inactive"
    #             self.var_status.set(txt)
                
    #         else:
    #             self.var_status.set("")
    #     except Exception:
    #         self.var_status.set("")

    #     self._snapshot_form()
    #     self._update_buttons_state()

    def _load_selected(self):
        it = self.tree.focus()
        if not it:
            return

        vals = self.tree.item(it).get("values", [])
        eid, sid, name, mail, phone = (list(vals) + [None] * 5)[:5]

        # ===== fill basic fields =====
        self.ent_empid.config(state=NORMAL)
        self.ent_empid.delete(0, END)
        self.ent_empid.insert(0, "" if eid is None else str(eid))
        self.ent_empid.config(state="readonly")

        self.ent_sid.delete(0, END)
        self.ent_sid.insert(0, "" if sid is None else str(sid))

        self.ent_name.delete(0, END)
        self.ent_name.insert(0, "" if name is None else str(name))

        self.ent_mail.delete(0, END)
        self.ent_mail.insert(0, "" if mail is None else str(mail))

        self.ent_phone.delete(0, END)
        self.ent_phone.insert(0, "" if phone is None else str(phone))

        # ===== status + hire_date =====
        try:
            if eid is not None:
                row = fetch_one(
                    "SELECT active, hire_date FROM employees WHERE employee_id=%s",
                    (eid,)
                )

                # status
                if row:
                    active_val = row.get("active", 1)
                    digit = "1" if active_val is None else str(active_val)
                    self.var_status.set("Active" if digit == "1" else "Inactive")
                else:
                    self.var_status.set("")

                # hire_date (readonly entry)
                if hasattr(self, "ent_hire_date"):
                    hd = row.get("hire_date") if row else None
                    hd_txt = ""
                    try:
                        if hd is not None:
                            # pymysql thường trả datetime.date
                            hd_txt = hd.isoformat() if hasattr(hd, "isoformat") else str(hd)
                    except Exception:
                        hd_txt = ""

                    self.ent_hire_date.config(state=NORMAL)
                    self.ent_hire_date.delete(0, END)
                    self.ent_hire_date.insert(0, hd_txt)
                    self.ent_hire_date.config(state="readonly")

            else:
                self.var_status.set("")
                if hasattr(self, "ent_hire_date"):
                    self.ent_hire_date.config(state=NORMAL)
                    self.ent_hire_date.delete(0, END)
                    self.ent_hire_date.config(state="readonly")

        except Exception:
            self.var_status.set("")
            if hasattr(self, "ent_hire_date"):
                try:
                    self.ent_hire_date.config(state=NORMAL)
                    self.ent_hire_date.delete(0, END)
                    self.ent_hire_date.config(state="readonly")
                except Exception:
                    pass

        self._snapshot_form()
        self._update_buttons_state()


    _EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    _PHONE_RE = re.compile(r"^[+]?[\d\s\-()]{6,20}$")

    # ---------- Draw tick --------

    def _ui_draw_tick(self):
            # Nếu widget đã bị destroy thì dừng hẳn
            if not self.winfo_exists():
                self._ui_draw_job = None
                return

            try:
                # Nếu tab/window đang không viewable (mới map, alt-tab, minimize, đang splash...)
                # => ĐỪNG kill loop. Chỉ "pause" rồi tự thử lại.
                if not self.winfo_viewable():
                    return

                # 1) chạy UI tasks do worker post lên (CÓ GIỚI HẠN để khỏi nghẽn)
                tasks = []
                lock = getattr(self, "_ui_task_lock", None)
                if lock is not None and hasattr(self, "_ui_task_q"):
                    with lock:
                        if self._ui_task_q:
                            tasks = self._ui_task_q[:40]
                            del self._ui_task_q[:len(tasks)]

                for fn in tasks:
                    try:
                        fn()
                    except Exception:
                        pass

                # storage cho 2 dòng status
                if not hasattr(self, "_cam_status_text"):
                    self._cam_status_text = "Camera: starting…"
                if not hasattr(self, "_recog_status_text"):
                    self._recog_status_text = "Recognition ready — waiting for sensor…"
                if not hasattr(self, "_recog_status_mode"):
                    self._recog_status_mode = "idle"

                # 2) apply pending recog status
                pending = getattr(self, "_recog_status_pending", None)
                self._recog_status_pending = None
                if pending:
                    text, mode = pending
                    self._recog_status_text = text
                    self._recog_status_mode = mode

                # 3) apply pending camera status
                cam_txt = getattr(self, "_cam_status_pending", None)
                self._cam_status_pending = None
                if cam_txt:
                    self._cam_status_text = cam_txt

                # 4) render status (gộp 2 dòng vào 1 label)
                try:
                    combined = f"{self._cam_status_text}\n{self._recog_status_text}"
                    self._cam_status_var.set(combined)

                    mode = self._recog_status_mode
                    if mode == "warn":
                        self.lbl_cam_status.configure(foreground="#E0B000")
                    elif mode == "ok":
                        self.lbl_cam_status.configure(foreground="#00D26A")
                    elif mode == "none":
                        self.lbl_cam_status.configure(foreground="#AAAAAA")
                    else:
                        self.lbl_cam_status.configure(foreground="#EAEAEA")
                except Exception:
                    pass

                # 5) LẤY FRAME MỚI NHẤT (drain queue để không bao giờ backlog)
                latest = None
                try:
                    while True:
                        latest = self._frame_queue.get_nowait()
                except Exception:
                    pass

                if latest is not None:
                    if not hasattr(self, "_frame_lock"):
                        self._frame_lock = threading.Lock()

                    cw, ch = getattr(self, "_canvas_wh", (0, 0))
                    if cw > 1 and ch > 1:
                        try:
                            rs = cv2.resize(latest, (cw, ch), interpolation=cv2.INTER_AREA)
                            rgb = cv2.cvtColor(rs, cv2.COLOR_BGR2RGB)
                            with self._frame_lock:
                                self._last_frame_bgr = latest
                                self._last_frame_rgb_rs = rgb
                                self._frame_seq += 1
                        except Exception:
                            pass

                # 6) vẽ frame
                self._draw_latest_frame()

            finally:
                # ✅ QUAN TRỌNG: luôn schedule tiếp, kể cả khi vừa "không viewable"
                # Nếu không viewable, tick sẽ quay lại nhanh và tự recover.
                if self.winfo_exists():
                    # khi không viewable, giảm nhịp để đỡ tốn CPU
                    delay = self._ui_draw_period_ms if self.winfo_viewable() else 200
                    self._ui_draw_job = self.after(delay, self._ui_draw_tick)
                else:
                    self._ui_draw_job = None

    # ---------- Actions ----------

    def _open_create_dialog(self):
        # Dialog preview phải lấy frame "raw" (không phụ thuộc scan_active)
        dlg = CreateEmployeeDialog(self, self._get_last_frame_for_dialog)
        self.wait_window(dlg)
        ok, info, face_payload = dlg.result
        if not ok: return

        sid = info["student_id"]; name = info["full_name"]
        email = info.get("email"); phone = info.get("phone")
        hire_date = info.get("hire_date")

        if email and not self._EMAIL_RE.match(email):
            messagebox.showwarning("Email", "Định dạng email không hợp lệ."); return
        if phone and not self._PHONE_RE.match(phone):
            messagebox.showwarning("Phone", "Số điện thoại không hợp lệ."); return

        try:
            add_employee(sid, name, email, phone, hire_date=hire_date)
            ex = fetch_one("SELECT employee_id FROM employees WHERE student_id=%s", (sid,))
            new_eid = ex["employee_id"] if ex else None

            if new_eid and face_payload is not None:
                os.makedirs(FACES_DIR, exist_ok=True)
                sha1 = hashlib.sha1(face_payload.tobytes()).hexdigest()
                dst = os.path.join(FACES_DIR, f"{new_eid}_{sha1}.jpg")
                face_payload.save(dst, format="JPEG", quality=92)
                rel = os.path.relpath(dst, APP_BASE).replace("\\", "/")
                upsert_face(new_eid, rel)

            self._refresh_employees(select_eid=new_eid or None)
            try:
                from ttkbootstrap.toast import ToastNotification
                ToastNotification(title="Created", message=f"Tạo nhân viên #{new_eid}", duration=2000).show_toast()
            except Exception:
                messagebox.showinfo("OK", f"Created new employee{f' #{new_eid}' if new_eid else ''}")
        except Exception as e:
            msg = str(e)
            if "1062" in msg or "Duplicate" in msg:
                messagebox.showwarning("Trùng mã sinh viên", f"Student ID {sid} đã tồn tại.")
            else:
                messagebox.showerror("Error", msg)


    def _change_face(self):
        eid = self._get_selected_id()
        if not eid:
            messagebox.showwarning("Face", "Chọn một nhân viên trước."); return

        # Dialog preview phải lấy frame "raw" (không phụ thuộc scan_active)
        dlg = ChangeFaceDialog(self, self._get_last_frame_for_dialog)
        self.wait_window(dlg)
        ok, pil_img = dlg.result
        if not ok or pil_img is None: return

        try:
            old = get_face(eid)
            if old and old.get("image_path"):
                abs_old = os.path.join(APP_BASE, old["image_path"])
                if os.path.isfile(abs_old):
                    try: os.remove(abs_old)
                    except Exception: pass

            os.makedirs(FACES_DIR, exist_ok=True)
            sha1 = hashlib.sha1(pil_img.tobytes()).hexdigest()
            dst = os.path.join(FACES_DIR, f"{eid}_{sha1}.jpg")
            pil_img.save(dst, format="JPEG", quality=92)
            rel = os.path.relpath(dst, APP_BASE).replace("\\", "/")
            upsert_face(eid, rel)

            self._show_face_small()
            it = self.tree.focus()
            if it:
                vals = list(self.tree.item(it, "values"))
                if len(vals) >= 6:
                    vals[5] = "✓"
                    self.tree.item(it, values=vals)

            messagebox.showinfo("Face", "Đã cập nhật ảnh khuôn mặt.")
        except Exception as e:
            messagebox.showerror("Face", str(e))

    def _save_change(self):
        if getattr(self, "_saving_now", False): return
        self._saving_now = True
        try:
            eid_txt = self.ent_empid.get().strip()
            eid = int(eid_txt) if eid_txt.isdigit() else self._get_focused_eid()
            if not eid: return

            sid_raw = self.ent_sid.get().strip()
            name    = self.ent_name.get().strip()
            email   = (self.ent_mail.get().strip() or None)
            phone   = (self.ent_phone.get().strip() or None)

            if not sid_raw.isdigit():
                messagebox.showwarning("Dữ liệu không hợp lệ", "Student ID phải là số dương."); return
            if not name:
                messagebox.showwarning("Thiếu dữ liệu", "Vui lòng nhập Full name."); return
            if email and not self._EMAIL_RE.match(email):
                messagebox.showwarning("Email", "Định dạng email không hợp lệ."); return
            if phone and not self._PHONE_RE.match(phone):
                messagebox.showwarning("Phone", "Số điện thoại không hợp lệ."); return

            sid = int(sid_raw)
            db_execute(
                "UPDATE employees SET student_id=%s, full_name=%s, email=%s, phone=%s WHERE employee_id=%s",
                (sid, name, email, phone, eid)
            )

            desired_txt = (self.var_status.get() or "").lower()
            cur_row = fetch_one("SELECT active FROM employees WHERE employee_id=%s", (eid,))
            cur_active = 1 if not cur_row or cur_row.get("active") is None else int(cur_row["active"])

            if desired_txt == "inactive" and cur_active != 0:
                if messagebox.askyesno(
                    "Deactivate",
                    "Chuyển trạng thái sang Inactive?\n- Sẽ xoá ảnh khuôn mặt để không còn nhận diện."
                ):
                    try:
                        row = get_face(eid)
                        if row and row.get("image_path"):
                            abs_p = os.path.join(APP_BASE, row["image_path"])
                            if os.path.isfile(abs_p): os.remove(abs_p)
                        delete_face_row(eid)
                    except Exception:
                        pass
                    deactivate_employee(eid)   # set active=0 + end_date
            elif desired_txt == "active" and cur_active == 0:
                db_execute("UPDATE employees SET active=1, end_date=NULL WHERE employee_id=%s", (eid,))

            self._refresh_employees(select_eid=eid)
            try:
                from ttkbootstrap.toast import ToastNotification
                ToastNotification(title="Saved", message=f"Đã cập nhật #{eid}", duration=1600).show_toast()
            except Exception:
                messagebox.showinfo("OK", f"Updated employee #{eid}")
        except Exception as e:
            msg = str(e)
            if "1062" in msg or "Duplicate" in msg:
                messagebox.showwarning("Trùng mã sinh viên", f"Student ID {self.ent_sid.get().strip()} đã tồn tại.")
            else:
                messagebox.showerror("Error", msg)
        finally:
            self._saving_now = False

    def _deactivate_selected_emp(self):
        if getattr(self, "_working_now", False): return
        self._working_now = True
        try:
            eid = self._get_focused_eid()
            if not eid: return

            if not messagebox.askyesno(
                "Xác nhận",
                f"Ngừng kích hoạt employee #{eid}?\n"
                "- Sẽ đặt end_date = hôm nay (giữ logs)\n"
                "- Xoá ảnh khuôn mặt để không còn được nhận diện"
            ):
                return

            try:
                row = get_face(eid)
                if row and row.get("image_path"):
                    abs_p = os.path.join(APP_BASE, row["image_path"])
                    if os.path.isfile(abs_p): os.remove(abs_p)
                delete_face_row(eid)
            except Exception:
                pass

            deactivate_employee(eid)

            mode = self._status_mode()
            if mode == "active":
                self._refresh_employees()
                self.var_status.set("0")
                if messagebox.askyesno("Deactivated", "Đã chuyển sang Inactive. Xem ngay danh sách Inactive?"):
                    self.cmb_status.set("Inactive")
                    self._refresh_employees(select_eid=eid)
            else:
                self._refresh_employees(select_eid=eid)
                self.var_status.set("0")

            try:
                from ttkbootstrap.toast import ToastNotification
                ToastNotification(title="Deactivated", message=f"Employee #{eid} inactive", duration=1800).show_toast()
            except Exception:
                pass
        finally:
            self._working_now = False

    # ----- Face preview -----
    def _show_face_small(self):
        it = self.tree.focus()
        if not it or self._is_placeholder_iid(it):
            self.lbl_face_small.configure(image="", text="(No face)" + (" (Kéo-thả)" if DND_ENABLED else ""))
            self.lbl_face_info.configure(text="")
            self._preview_small_imgtk = None
            return

        try:
            eid = int(self.tree.item(it)["values"][0])
        except Exception:
            self.lbl_face_small.configure(image="", text="(No face)")
            self.lbl_face_info.configure(text="")
            self._preview_small_imgtk = None
            return

        row = get_face(eid)
        if not row or not row.get("image_path"):
            self.lbl_face_small.configure(image="", text="(No face)")
            self.lbl_face_info.configure(text="")
            self._preview_small_imgtk = None
            return

        p = os.path.join(APP_BASE, row["image_path"])
        if not os.path.exists(p):
            self.lbl_face_small.configure(image="", text=f"(Missing)\n{row['image_path']}")
            self.lbl_face_info.configure(text="")
            self._preview_small_imgtk = None
            return

        W, H = 110, 110  # phải khớp face_box
        try:
            with Image.open(p) as im:
                src_w, src_h = im.size
                im = im.convert("RGB")
                im.thumbnail((W, H), Image.LANCZOS)

                bg = Image.new("RGB", (W, H), (20, 20, 20))
                x = (W - im.size[0]) // 2
                y = (H - im.size[1]) // 2
                bg.paste(im, (x, y))

                imgtk = ImageTk.PhotoImage(bg)

            size_kb = os.path.getsize(p) / 1024.0
            self.lbl_face_info.configure(text=f"{src_w}×{src_h} px  •  {size_kb:.1f} KB")

            self._preview_small_imgtk = imgtk
            self.lbl_face_small.configure(image=self._preview_small_imgtk, text="")

        except Exception as e:
            self.lbl_face_small.configure(image="", text=f"(Error)\n{e}")
            self.lbl_face_info.configure(text="")
            self._preview_small_imgtk = None


    # ---- Import Guide ---
    def _show_import_guide(self) -> tuple[bool, bool]:
        dlg = tb.Toplevel(self)
        dlg.title("Hướng dẫn Import")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)

        dlg.update_idletasks()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        w, h = 460, 340
        dlg.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//3}")

        frm = tb.Frame(dlg, padding=14)
        frm.pack(fill=BOTH, expand=YES)

        tb.Label(frm, text="📘 Import CSV + Ảnh khuôn mặt",
                font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0,8))

        tb.Label(frm, text=(
            "• CSV cần cột: student_id, full_name (email, phone tuỳ chọn)\n"
            "• Ảnh đặt trong thư mục riêng, tên file chứa student_id\n"
            "  → ví dụ: 1001.jpg, 1001_face.png, 1002-abc.webp\n"
            "• Hỗ trợ: .jpg .jpeg .png .bmp .webp\n"
            "• Nếu có nhiều ảnh trùng cùng student_id ➜ bỏ qua ảnh\n"
            "• Nếu không có ảnh ➜ sẽ cảnh báo thiếu ảnh"
        ), justify="left", wraplength=420).pack(anchor="w", pady=(4, 6))

        tb.Separator(frm).pack(fill=X, pady=6)

        suppress_var = tb.BooleanVar(value=self._import_guide_suppress)
        tb.Checkbutton(frm, text="Đừng hiện lại hướng dẫn này", variable=suppress_var).pack(anchor="w", pady=(4, 8))

        act = tb.Frame(frm)
        act.pack(fill=X)
        proceed = {"value": False}

        def do_ok():
            proceed["value"] = True
            dlg.destroy()
        def do_cancel():
            dlg.destroy()

        tb.Button(act, text="Tiếp tục", bootstyle=SUCCESS, command=do_ok).pack(side=RIGHT, padx=4)
        tb.Button(act, text="Huỷ", bootstyle=SECONDARY, command=do_cancel).pack(side=RIGHT, padx=4)

        dlg.wait_window(dlg)
        return proceed["value"], bool(suppress_var.get())

    # ----- Import / Export -----
    def _import_csv(self):
        if not self._import_guide_suppress:
            proceed, suppress = self._show_import_guide()
            if not proceed:
                return
            self._import_guide_suppress = suppress

        upsert = tb.dialogs.Messagebox.yesno(
            "Nếu student_id đã tồn tại:\n\nYes = cập nhật (upsert)\nNO = bỏ qua (skip)",
            "Import mode", parent=self
        ) == "Yes"

        path = filedialog.askopenfilename(
            parent=self,
            title="Chọn file CSV (UTF-8, có header)",
            filetypes=[("CSV files", "*.csv")]
        )
        if not path:
            return

        img_dir = filedialog.askdirectory(parent=self, title="Chọn thư mục chứa ảnh (Cancel nếu không dùng ảnh)")
        if not img_dir:
            if not tb.dialogs.Messagebox.yesno(
                "Không chọn thư mục ảnh — tiếp tục import mà không gắn ảnh?",
                "Bỏ qua ảnh", parent=self
            ) == "Yes":
                return
            img_dir = None

        import csv, hashlib, shutil, glob, os, re
        from PIL import Image
        from datetime import datetime, date

        ok = skipped = face_ok = 0
        warn_ambiguous = 0
        warn_missing = 0
        errors = []

        def norm(s): 
            return (s or "").strip().lower()

        def parse_date(s: str):
            s = (s or "").strip()
            if not s:
                return None
            # chịu nhiều format phổ biến
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(s, fmt).date()
                except Exception:
                    pass
            return None

        def parse_active(s: str):
            s = (s or "").strip().lower()
            if s == "":
                return None
            if s in ("1", "true", "yes", "active"):
                return 1
            if s in ("0", "false", "no", "inactive"):
                return 0
            return None

        def is_resigned(end_date_val, active_val):
            """
            Rule: Nếu có end_date (không rỗng) HOẶC active=0 => resigned.
            => ép active=0 khi import.
            """
            if end_date_val is not None:
                return True
            if active_val is not None and int(active_val) == 0:
                return True
            return False

        # --- Build image index: sid -> best filepath ---
        img_index: dict[int, str] = {}
        if img_dir:
            try:
                exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
                all_files = []
                for ext in exts:
                    all_files += glob.glob(os.path.join(img_dir, f"**/*{ext}"), recursive=True)
                    all_files += glob.glob(os.path.join(img_dir, f"**/*{ext.upper()}"), recursive=True)

                leading_num = re.compile(r"^(\d+)")
                any_num = re.compile(r"(\d+)")

                best_for_sid: dict[int, tuple[int, str]] = {}  # sid -> (rank, filepath)

                for fp in all_files:
                    base = os.path.splitext(os.path.basename(fp))[0]
                    m_lead = leading_num.search(base)
                    m_any = any_num.search(base)

                    candidates: list[tuple[int, int]] = []
                    if m_lead:
                        sid_val = int(m_lead.group(1))
                        rank = 0 if base == str(sid_val) else 1
                        candidates.append((sid_val, rank))
                    elif m_any:
                        sid_val = int(m_any.group(1))
                        candidates.append((sid_val, 2))

                    for sid_val, rank in candidates:
                        prev = best_for_sid.get(sid_val)
                        if prev is None or rank < prev[0]:
                            best_for_sid[sid_val] = (rank, fp)

                for sid_val, (_rank, fp) in best_for_sid.items():
                    img_index[sid_val] = fp

            except Exception as e:
                messagebox.showwarning("Ảnh", f"Lỗi khi quét thư mục ảnh:\n{e}")
                img_dir = None

        # --- Đọc CSV và import ---
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                headers = {norm(h): h for h in (reader.fieldnames or [])}

                need = {"student_id", "full_name"}
                if not need.issubset(set(headers.keys())):
                    raise ValueError(f"Thiếu cột bắt buộc: {need - set(headers.keys())}")

                # optional columns
                has_email = "email" in headers
                has_phone = "phone" in headers
                has_hire  = "hire_date" in headers
                has_end   = "end_date" in headers
                has_act   = "active" in headers

                for i, row in enumerate(reader, start=2):
                    sid_raw = (row.get(headers["student_id"], "") or "").strip()
                    name    = (row.get(headers["full_name"], "") or "").strip()
                    email   = (row.get(headers["email"], "") or "").strip() if has_email else None
                    phone   = (row.get(headers["phone"], "") or "").strip() if has_phone else None

                    if not sid_raw or not name or not sid_raw.isdigit():
                        skipped += 1
                        continue

                    sid = int(sid_raw)

                    # parse optional fields
                    hire_date = parse_date(row.get(headers["hire_date"], "")) if has_hire else None
                    end_date  = parse_date(row.get(headers["end_date"], ""))  if has_end  else None
                    active_in = parse_active(row.get(headers["active"], ""))  if has_act  else None

                    # IMPORTANT: resigned => force inactive
                    resigned = is_resigned(end_date, active_in)
                    final_active = 0 if resigned else (1 if active_in is None else int(active_in))

                    # hire_date: nếu có cột hire_date mà trống/parse fail -> fallback today
                    if has_hire:
                        if hire_date is None:
                            hire_date = date.today()

                    ex = fetch_one("SELECT employee_id FROM employees WHERE student_id=%s", (sid,))
                    if ex:
                        if upsert:
                            # Update base fields
                            set_parts = ["full_name=%s", "email=%s", "phone=%s"]
                            params = [name or None, email or None, phone or None]

                            # Update optional columns nếu CSV có
                            if has_hire:
                                set_parts.append("hire_date=%s")
                                params.append(hire_date)
                            if has_end:
                                set_parts.append("end_date=%s")
                                params.append(end_date)  # None OK
                            if has_act or has_end:
                                # nếu có end_date -> ép inactive, nên vẫn update active
                                set_parts.append("active=%s")
                                params.append(final_active)

                            params.append(sid)
                            db_execute(
                                f"UPDATE employees SET {', '.join(set_parts)} WHERE student_id=%s",
                                tuple(params)
                            )
                            eid = ex["employee_id"]
                        else:
                            skipped += 1
                            continue
                    else:
                        # Insert
                        # Ưu tiên gọi add_employee nếu signature bạn đã mở rộng,
                        # nếu không thì fallback insert SQL cho chắc.
                        try:
                            if has_hire or has_end or has_act:
                                add_employee(
                                    sid, name,
                                    email or None, phone or None,
                                    (hire_date or date.today()),
                                    end_date,
                                    final_active
                                )
                            else:
                                # file CSV cũ
                                add_employee(sid, name, email or None, phone or None)
                        except TypeError:
                            # fallback SQL insert
                            db_execute(
                                "INSERT INTO employees (student_id, full_name, email, phone, hire_date, end_date, active) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                (
                                    sid, name,
                                    email or None, phone or None,
                                    (hire_date or date.today()),
                                    end_date,
                                    final_active
                                )
                            )

                        ex2 = fetch_one("SELECT employee_id FROM employees WHERE student_id=%s", (sid,))
                        eid = ex2["employee_id"] if ex2 else None

                    # Gắn ảnh nếu có (chỉ gắn nếu có file ảnh khớp; không phụ thuộc active)
                    if img_dir and eid:
                        src = img_index.get(sid)
                        if src and os.path.isfile(src):
                            try:
                                with Image.open(src) as im:
                                    im.verify()
                                with open(src, "rb") as fimg:
                                    sha1 = hashlib.sha1(fimg.read()).hexdigest()
                                ext = os.path.splitext(src)[1].lower() or ".jpg"
                                os.makedirs(FACES_DIR, exist_ok=True)
                                dst = os.path.join(FACES_DIR, f"{eid}_{sha1}{ext}")
                                shutil.copy2(src, dst)
                                rel = os.path.relpath(dst, APP_BASE).replace("\\", "/")
                                upsert_face(eid, rel)
                                face_ok += 1
                            except Exception as fe:
                                errors.append(f"Dòng {i}: {fe}")
                        else:
                            warn_missing += 1

                    ok += 1

        except Exception as e:
            messagebox.showerror("Import CSV", f"Lỗi đọc file:\n{e}")
            return

        self.refresh()

        # Tổng kết
        msg = [
            "✅ Import hoàn tất:",
            f"• Thành công: {ok}",
            f"• Bỏ qua: {skipped}",
            f"• Ảnh gắn OK: {face_ok}"
        ]
        if img_dir:
            if warn_ambiguous:
                msg.append(f"• Cảnh báo: {warn_ambiguous} student_id trùng ảnh (đã tự chọn 1).")
            if warn_missing:
                msg.append(f"• Thiếu ảnh: {warn_missing} dòng không có ảnh khớp.")
        if errors:
            msg.append("")
            msg.append("Chi tiết lỗi:")
            for e in errors[:10]:
                msg.append(f"- {e}")
            if len(errors) > 10:
                msg.append(f"... (+{len(errors)-10} lỗi khác)")

        messagebox.showinfo("Import CSV", "\n".join(msg))


    def _export_employees(self):
        path = filedialog.asksaveasfilename(
            title="Save employees as CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")]
        )
        if not path:
            return

        # NEW: hỏi export full hay theo filter hiện tại
        export_full = (tb.dialogs.Messagebox.yesno(
            "Bạn muốn export FULL danh sách nhân viên (Active + Inactive) không?\n\n"
            "Yes = FULL \n"
            "No  = Active Only",
            "Export mode",
            parent=self
        ) == "Yes")

        # lấy rows
        if export_full:
            rows = list_employees(active_only=False)  # full
        else:
            mode = self._status_mode()
            if mode == "active":
                rows = list_employees(active_only=True)
            elif mode == "inactive":
                rows = [r for r in list_employees(active_only=False) if "active" in r and str(r["active"]) == "0"]
            else:
                rows = list_employees(active_only=False)

        # bổ sung hire_date/end_date/active cho chắc (phòng list_employees chưa select 2 cột này)
        try:
            ids = [r.get("employee_id") for r in rows if r.get("employee_id") is not None]
            extra = {}
            if ids:
                placeholders = ",".join(["%s"] * len(ids))
                extra_rows = fetch_all(
                    f"SELECT employee_id, hire_date, end_date, active FROM employees WHERE employee_id IN ({placeholders})",
                    tuple(ids)
                )
                for er in (extra_rows or []):
                    extra[er["employee_id"]] = er

            for r in rows:
                eid = r.get("employee_id")
                if eid in extra:
                    r["hire_date"] = extra[eid].get("hire_date")
                    r["end_date"]  = extra[eid].get("end_date")
                    r["active"]    = extra[eid].get("active")
        except Exception:
            pass

        def fmt_date(d):
            if d is None:
                return ""
            try:
                return d.isoformat()
            except Exception:
                return str(d)

        # ghi CSV
        try:
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["employee_id", "student_id", "full_name", "email", "phone", "hire_date", "end_date", "active"])
                for r in rows:
                    w.writerow([
                        r.get("employee_id"),
                        r.get("student_id"),
                        r.get("full_name"),
                        r.get("email"),
                        r.get("phone"),
                        fmt_date(r.get("hire_date")),
                        fmt_date(r.get("end_date")),
                        r.get("active", 1),
                    ])

            messagebox.showinfo("Export", f"Đã xuất: {path}")
        except Exception as e:
            messagebox.showerror("Export", f"Lỗi ghi file:\n{e}")



    # ----- Handler khi sensor báo NG -----

    def _on_sensor_trigger(self):
        def _ui():
            if time.time() < getattr(self, "_cooldown_until", 0.0):
                return
            if getattr(self, "_await_hw_ready", False):
                return
            if self._scan_active:
                return

            # ✅ BẬT nhận diện theo cửa sổ thời gian = timeout scan
            timeout_sec = 15.0
            try:
                if getattr(self, "_recog_daemon", None):
                    self._recog_daemon.arm_new_session(window_sec=timeout_sec)
            except Exception:
                pass

            # tạo scan phiên mới
            self._scan_token += 1
            token = self._scan_token
            self._scan_result = None

            self._viz = None
            self._update_recog_status("📟 Scanning… (waiting for face)", "warn")

            self._scan_recognized = False
            self._scan_committed = False

            # cancel timeout cũ
            if self._scan_timeout_id is not None:
                try:
                    self.after_cancel(self._scan_timeout_id)
                except Exception:
                    pass
                self._scan_timeout_id = None

            self._scan_active = True
            self._scan_deadline = time.time() + timeout_sec

            def _timeout():
                self._scan_timeout_id = None
                if token != self._scan_token:
                    return
                # nếu đã có kết quả -> cấm fail
                if self._scan_result is not None:
                    return

                self._scan_result = "fail"
                self._await_hw_ready = True
                self._scan_active = False
                self._scan_deadline = 0.0
                self._viz = None

                # ✅ FAIL thì pause recog ngay (đợi sensor lần sau)
                try:
                    if getattr(self, "_recog_daemon", None):
                        self._recog_daemon.pause()
                except Exception:
                    pass

                self._update_recog_status("❌ User not found", "none")

                try:
                    if self._uart:
                        self._uart.send_fail(resend=True)
                except Exception:
                    pass

            self._scan_timeout_id = self.after(int(timeout_sec * 1000), _timeout)

        self.after(0, _ui)

    def _on_hw_ready(self):
        def _ui():
            # ✅ kết thúc phiên -> pause recog
            try:
                if getattr(self, "_recog_daemon", None):
                    self._recog_daemon.pause()
            except Exception:
                pass

            # vô hiệu hoá mọi timeout/scan cũ
            self._await_hw_ready = False
            self._scan_token += 1
            self._scan_result = None

            self._scan_recognized = False
            self._scan_committed = False

            if self._scan_timeout_id is not None:
                try:
                    self.after_cancel(self._scan_timeout_id)
                except Exception:
                    pass
                self._scan_timeout_id = None

            self._scan_active = False
            self._scan_deadline = 0.0
            self._viz = None

            self._cooldown_until = time.time() + 0.5

            self._update_recog_status("Recognition ready — waiting for sensor…", "idle")

        self.after(0, _ui)


    # ----- Context menu -----
    def _popup_ctx(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
        try:
            self._ctx.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx.grab_release()

    def _ctx_copy_id(self):
        it = self.tree.focus()
        if not it: return
        eid = str(self.tree.item(it)["values"][0])
        try:
            self.clipboard_clear(); self.clipboard_append(eid)
        except Exception:
            pass

    def _ctx_copy_col(self, idx: int):
        it = self.tree.focus()
        if not it: return
        vals = self.tree.item(it)["values"]
        if idx < len(vals):
            try:
                self.clipboard_clear(); self.clipboard_append(str(vals[idx] or ""))
            except Exception:
                pass

    # ----- Face file ops -----
    def _open_image_file(self):
        it = self.tree.focus()
        if not it: return
        eid = int(self.tree.item(it)["values"][0])
        row = get_face(eid)
        if not row: return
        p = os.path.join(APP_BASE, row["image_path"])
        if not os.path.exists(p): return
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception:
            pass

    def _open_image_folder(self):
        it = self.tree.focus()
        if not it:
            messagebox.showinfo("Ảnh", "Hãy chọn một nhân viên trước."); return
        eid = int(self.tree.item(it)["values"][0])
        row = get_face(eid)
        if not row:
            messagebox.showinfo("Ảnh", "Nhân viên này chưa có ảnh."); return

        p = os.path.join(APP_BASE, row["image_path"])
        folder = os.path.dirname(p)
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("Mở thư mục", f"Không thể mở thư mục:\n{e}")

    def _on_remove_face_only(self, *_):
        it = self.tree.focus()
        if not it:
            messagebox.showinfo("Ảnh", "Hãy chọn một nhân viên trước."); return

        eid = int(self.tree.item(it)["values"][0])
        row = get_face(eid)
        if not row:
            messagebox.showinfo("Ảnh", "Nhân viên này chưa có ảnh."); return

        if not messagebox.askyesno("Xóa ảnh", "Xoá ảnh khuôn mặt (không ảnh hưởng thông tin nhân viên)?"):
            return

        abs_path = os.path.join(APP_BASE, row["image_path"])
        try:
            if os.path.isfile(abs_path): os.remove(abs_path)
        except Exception:
            pass

        delete_face_row(eid)
        self._show_face_small()
        messagebox.showinfo("Ảnh", "Đã xoá ảnh.")
        it = self.tree.focus()
        if it:
            vals = list(self.tree.item(it, "values"))
            if len(vals) >= 6:
                vals[5] = ""; self.tree.item(it, values=vals)

    def _build_face_library(self) -> List[Dict[str, Any]]:  # SILENT
        """
        Build face library for recognition.

        Priority:
        1) From DB join faces (active employees only).
        2) Fallback: scan data/faces folder and infer mapping from file names.

        Accepted file patterns in data/faces:
        - "{eid}_anything.jpg"  (eid = employee_id)
        - "{student_id}_anything.jpg" (map to eid via DB)
        If cannot map, we STILL include the image with eid=-1 so that the
        daemon won't say 'No faces in database' (but on_hit won't fire).
        """
        import glob, os, re

        lib: List[Dict[str, Any]] = []
        used_abs: set[str] = set()

        def _push(eid: int, sid: int | None, name: str, abs_p: str):
            ap = os.path.abspath(abs_p)
            if not os.path.isfile(ap) or ap in used_abs:
                return
            lib.append({
                "eid": eid,
                "student_id": sid,
                "full_name": name or "",
                "img_abs": ap
            })
            used_abs.add(ap)

        # 1) DB-first
        try:
            rows = fetch_all(
                "SELECT e.employee_id AS eid, e.student_id, e.full_name, f.image_path "
                "FROM employees e JOIN faces f ON f.employee_id = e.employee_id "
                "WHERE COALESCE(e.active,1)=1"
            )
        except Exception:
            rows = []

        if rows:
            for r in rows:
                rel = r.get("image_path")
                if not rel:
                    continue
                abs_p = os.path.join(APP_BASE, rel)
                _push(int(r["eid"]), r.get("student_id"), r.get("full_name") or "", abs_p)

        # 2) Fallback: scan folder if DB empty (or you want to be extra safe)
        if not lib:
            faces_root = os.path.join(APP_BASE, "data", "faces")
            exts = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp")
            paths: list[str] = []
            for pat in exts:
                paths += glob.glob(os.path.join(faces_root, pat))

            # quick maps
            try:
                emp_rows = fetch_all(
                    "SELECT employee_id, student_id, full_name, COALESCE(active,1) AS active FROM employees"
                )
            except Exception:
                emp_rows = []

            sid_to_emp: dict[int, dict] = {}
            eid_to_emp: dict[int, dict] = {}
            for r in emp_rows or []:
                if str(r.get("active", "1")) != "0":
                    eid_to_emp[int(r["employee_id"])] = r
                    sid = r.get("student_id")
                    if sid is not None:
                        sid_to_emp[int(sid)] = r

            re_prefix_num = re.compile(r"^(\d+)[\-_]?.*", re.IGNORECASE)

            for fp in paths:
                fname = os.path.splitext(os.path.basename(fp))[0]
                m = re_prefix_num.match(fname)
                mapped = False
                if m:
                    num = m.group(1)
                    if num.isdigit():
                        val = int(num)
                        if val in eid_to_emp:
                            emp = eid_to_emp[val]
                            _push(val, emp.get("student_id"), emp.get("full_name") or "", fp)
                            mapped = True
                        elif val in sid_to_emp:
                            emp = sid_to_emp[val]
                            _push(int(emp["employee_id"]), emp.get("student_id"), emp.get("full_name") or "", fp)
                            mapped = True

                if not mapped:
                    # vẫn thêm để daemon không coi là DB=0; eid=-1 -> sẽ không on_hit
                    _push(-1, None, "", fp)

        # (no prints / logs here — silent)
        return lib



    def _select_employee_in_tree(self, eid: int):
        for iid in self.tree.get_children(""):
            vals = self.tree.item(iid, "values")
            if vals and str(vals[0]) == str(eid):
                self.tree.selection_set(iid); self.tree.focus(iid); self.tree.see(iid)
                self._load_selected()
                self._show_face_small()
                break

    # ----- DND -----
    def _on_drop_image(self, event):
        eid = self._get_selected_id()
        if not eid: return
        raw = event.data.strip().strip("{}")
        fp = raw.split("} {")[0] if "} {" in raw else raw
        self._save_face_from_path(eid, fp)

    def _save_face_from_path(self, eid, fp):
        if not os.path.exists(fp):
            messagebox.showwarning("Ảnh", "Đường dẫn ảnh không tồn tại."); return
        try:
            with Image.open(fp) as im: im.verify()
        except Exception:
            messagebox.showwarning("Ảnh", "Tập tin không phải ảnh hợp lệ."); return

        os.makedirs(FACES_DIR, exist_ok=True)

        old = get_face(eid)
        old_abs = os.path.join(APP_BASE, old["image_path"]) if (old and old.get("image_path")) else None

        with open(fp, "rb") as f:
            sha1 = hashlib.sha1(f.read()).hexdigest()
        ext = os.path.splitext(fp)[1].lower() or ".jpg"
        dst = os.path.join(FACES_DIR, f"{eid}_{sha1}{ext}")
        shutil.copy2(fp, dst)
        rel = os.path.relpath(dst, APP_BASE).replace("\\", "/")
        upsert_face(eid, rel)

        if old_abs and os.path.abspath(old_abs) != os.path.abspath(dst):
            try:
                if os.path.isfile(old_abs): os.remove(old_abs)
            except Exception:
                pass

        self._show_face_small()
        messagebox.showinfo("OK", f"Đã lưu: {rel}")

        it = self.tree.focus()
        if it:
            vals = list(self.tree.item(it, "values"))
            if len(vals) >= 6:
                vals[5] = "✓"
                self.tree.item(it, values=vals)

    # ----- Cleanup -----
    def _on_destroy(self, *_):
        # stop threads
        try:
            self._stop_recog()
        except Exception:
            pass
        try:
            self._stop_camera()
        except Exception:
            pass
        try:
            if getattr(self, "_uart", None):
                self._uart.stop()
        except Exception:
            pass

        # cancel UI jobs
        for attr in ("_ui_draw_job", "_scan_timeout_id", "_cam_status_after_id", "_recog_status_after_id", "_draw_after_id", "_uart_poll_job"):
            try:
                job = getattr(self, attr, None)
                if job:
                    self.after_cancel(job)
            except Exception:
                pass
            try:
                setattr(self, attr, None)
            except Exception:
                pass

        # clear ui task queue
        try:
            if hasattr(self, "_ui_task_lock") and hasattr(self, "_ui_task_q"):
                with self._ui_task_lock:
                    self._ui_task_q.clear()
        except Exception:
            pass

        self._is_running = False