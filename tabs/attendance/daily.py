# tabs/attendance/daily.py
from __future__ import annotations
import csv
from datetime import date, datetime, timedelta, time as dtime
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog, messagebox
from db.attendance_dal import get_daily_stack_plus  # có cột 'late'

class AttendanceDaily(tb.Frame):
    """
    Daily summary realtime: total_active | present | late | absent trong khoảng ngày.
    - Auto refresh mỗi 1s.
    - Chặn chọn ngày tương lai (soft clamp về hôm nay).
    - Chặn export nếu range chứa hôm nay và trước 17:00.
    - Màu sắc tối ưu cho theme 'darkly'.
    """
    AUTO_REFRESH_MS = 1000  # 1s
    SHIFT_END = dtime(17, 0, 0)

    def __init__(self, parent):
        super().__init__(parent)
        self._rows_cache = []
        self._auto_id = None
        self._auto_running = False
        self._warned_future = False  # tránh spam cảnh báo
        self._build_ui()
        self._load_default()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.bind("<<ShowFrame>>", lambda e: self._update_export_state(), add="+")  # nếu có dùng event show tab

    @staticmethod
    def _to_date(obj):
        if obj is None:
            return None
        if isinstance(obj, datetime):
            return obj.date()
        if isinstance(obj, date):
            return obj
        try:
            return datetime.strptime(str(obj), "%Y-%m-%d").date()
        except Exception:
            return None

    def _build_ui(self):
        top = tb.Frame(self); top.pack(fill=X, pady=6)

        tb.Label(top, text="From:", foreground="#EAEAEA").pack(side=LEFT, padx=(0,6))
        self.dp_from = tb.DateEntry(top, bootstyle=INFO, dateformat="%Y-%m-%d")
        self.dp_from.pack(side=LEFT, padx=(0,10))

        tb.Label(top, text="To:", foreground="#EAEAEA").pack(side=LEFT, padx=(0,6))
        self.dp_to = tb.DateEntry(top, bootstyle=INFO, dateformat="%Y-%m-%d")
        self.dp_to.pack(side=LEFT, padx=(0,10))

        tb.Button(top, text="Refresh", bootstyle=SUCCESS, command=self._on_manual_refresh).pack(side=LEFT, padx=6)
        # giữ ref để enable/disable theo rule export
        self.btn_export = tb.Button(top, text="Export CSV", bootstyle=WARNING, command=self._export_csv)
        self.btn_export.pack(side=LEFT, padx=6)

        self.lbl_sum = tb.Label(self, text="", anchor="w", bootstyle=SECONDARY, foreground="#CFCFCF")
        self.lbl_sum.pack(fill=X, padx=8, pady=(0,6))

        cols = ("day", "total_active", "present", "late", "absent")
        self.tree = tb.Treeview(self, columns=cols, show="headings", selectmode="browse", bootstyle=INFO)
        for c in cols:
            self.tree.heading(c, text=c, anchor =W)
        self.tree.column("day", width=130, anchor=W)
        self.tree.column("total_active", width=110, anchor=W)
        self.tree.column("present", width=90, anchor=W)
        self.tree.column("late", width=80, anchor=W)
        self.tree.column("absent", width=90, anchor=W)
        self.tree.pack(fill=BOTH, expand=YES, padx=8, pady=6)

        self.tree.tag_configure("even", background="#151515", foreground="#EAEAEA")
        self.tree.tag_configure("odd",  background="#1f1f1f", foreground="#EAEAEA")

        # đổi ngày -> clamp & refresh & update export state
        self.dp_from.bind("<<DateEntrySelected>>",
                          lambda e: (self._clamp_future(), self._clamp_range(),
                                     self._update_export_state(), self._query()))
        self.dp_to.bind("<<DateEntrySelected>>",
                        lambda e: (self._clamp_future(), self._clamp_range(),
                                   self._update_export_state(), self._query()))

    def _clamp_range(self):
        d1 = self._to_date(self.dp_from.get_date())
        d2 = self._to_date(self.dp_to.get_date())
        if d1 and d2 and d1 > d2:
            self.dp_to.set_date(d1)

    def _clamp_future(self):
        """Nếu user chọn ngày tương lai -> tự kéo về hôm nay (soft clamp)."""
        today = date.today()
        changed = False
        d1 = self._to_date(self.dp_from.get_date())
        d2 = self._to_date(self.dp_to.get_date())
        if d1 and d1 > today:
            self.dp_from.set_date(today); changed = True
        if d2 and d2 > today:
            self.dp_to.set_date(today); changed = True
        if changed and not self._warned_future:
            # cảnh báo nhẹ, chỉ 1 lần trên mỗi lần vượt
            try:
                messagebox.showinfo("Date limit", "Không thể chọn ngày trong tương lai. Đã đặt về hôm nay.")
            except Exception:
                pass
            self._warned_future = True
        if not changed:
            self._warned_future = False  # reset khi người dùng trở lại range hợp lệ

    def _load_default(self):
        today = date.today()
        frm = today - timedelta(days=6)
        self.dp_from.set_date(frm)
        self.dp_to.set_date(today)
        self._query()
        self._update_export_state()

    def _update_export_state(self):
        """
        Disable Export nếu range có chứa 'hôm nay' và giờ hiện tại < 17:00.
        (Tránh xuất dữ liệu đang còn biến động)
        """
        d1 = self._to_date(self.dp_from.get_date())
        d2 = self._to_date(self.dp_to.get_date())
        if not (d1 and d2):
            state = DISABLED
        else:
            now = datetime.now()
            include_today = (d1 <= date.today() <= d2)
            allow = (not include_today) or (include_today and now.time() >= self.SHIFT_END)
            state = NORMAL if allow else DISABLED
        try:
            self.btn_export.configure(state=state)
        except Exception:
            pass

    def _on_manual_refresh(self):
        self._clamp_future()
        self._clamp_range()
        self._update_export_state()
        self._query()

    def _query(self):
        if not self.winfo_exists():
            return
        # đảm bảo không có ngày tương lai lọt vào khi auto tick
        self._clamp_future()
        d1 = self._to_date(self.dp_from.get_date())
        d2 = self._to_date(self.dp_to.get_date())
        if not d1 or not d2:
            return
        if d2 < d1:
            d1, d2 = d2, d1

        try:
            rows = get_daily_stack_plus(d1, d2)
            self._rows_cache = rows
        except Exception as e:
            self._debug_exc("DAILY:get_daily_stack_plus", e)
            return

        for i in self.tree.get_children():
            self.tree.delete(i)

        total_active = present = absent = late = 0
        for i, r in enumerate(rows):
            day = r.get("day")
            ta  = int(r.get("total_active", 0))
            pr  = int(r.get("present", 0))
            lt  = int(r.get("late", 0))
            ab  = int(r.get("absent", 0))
            self.tree.insert("", END, values=(day, ta, pr, lt, ab),
                             tags=("odd" if i % 2 else "even",))
            total_active += ta; present += pr; late += lt; absent += ab

        self.lbl_sum.configure(
            text=f"Days: {len(rows)}   Σ total_active: {total_active}   "
                 f"Σ present: {present}   Σ late: {late}   Σ absent: {absent}"
        )

    def _export_csv(self):
        if not self._rows_cache:
            messagebox.showinfo("Export", "Không có dữ liệu để xuất.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Daily Summary", defaultextension=".csv",
            filetypes=[("CSV files","*.csv")]
        )
        if not path: return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["day", "total_active", "present", "late", "absent"])
                for r in self._rows_cache:
                    w.writerow([
                        r.get("day"),
                        r.get("total_active", 0),
                        r.get("present", 0),
                        r.get("late", 0),
                        r.get("absent", 0),
                    ])
            messagebox.showinfo("Export", f"Đã xuất: {path}")
        except Exception as e:
            messagebox.showerror("Export", str(e))

    def _schedule_auto(self):
        if not self._auto_running or not self.winfo_exists():
            return
        if self._auto_id:
            try:
                self.after_cancel(self._auto_id)
            except Exception:
                pass
        self._auto_id = self.after(self.AUTO_REFRESH_MS, self._auto_tick)


    def start_auto(self):
        if self._auto_running:
            return
        self._auto_running = True
        # chạy ngay để user thấy dữ liệu liền khi mở tab
        try:
            self._query()
            self._update_export_state()
        except Exception:
            pass
        self._schedule_auto()

    def stop_auto(self):
        self._auto_running = False
        if self._auto_id:
            try:
                self.after_cancel(self._auto_id)
            except Exception:
                pass
            self._auto_id = None

    def _auto_tick(self):
        if not self._auto_running or not self.winfo_exists():
            return
        try:
            self._query()
            self._update_export_state()
        finally:
            self._schedule_auto()

    def _on_destroy(self, *_):
        # stop auto + cancel after
        self._auto_running = False
        if self._auto_id:
            try:
                self.after_cancel(self._auto_id)
            except Exception:
                pass
            self._auto_id = None

    def _debug_exc(self, where: str, e: Exception):
        # In ra console (cmd / terminal)
        print(f"[{where}] {type(e).__name__}: {e!r}")
        # hiện nhẹ lên UI (1 lần) để bạn thấy ngay
        try:
            self.lbl_sum.configure(text=f"⚠ {where}: {type(e).__name__}: {e}")
        except Exception:
            pass

    def refresh(self):
        self._on_manual_refresh()
