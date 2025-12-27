# from __future__ import annotations

# import csv
# import calendar
# from datetime import date, datetime, timedelta, time as dtime

# import ttkbootstrap as tb
# from ttkbootstrap.constants import *
# from tkinter import filedialog, messagebox

# from db.attendance_dal import (
#     list_logs_by_date_with_flag,
#     get_monthly_employee_summary,
#     get_employee_dates
# )

# from tabs.attendance.widget.monthly_donut import MonthlyDonutChart


# # ==========================================================
# # RAM overlay cho logs ngoài giờ (không lưu DB)
# # ==========================================================
# _NOTIN_TTL_SEC = 600
# _NOTIN: list[dict] = []


# def push_not_in_shift(employee_id: int, full_name: str, ts: datetime | None = None):
#     ts = ts or datetime.now()
#     _NOTIN.append({
#         "log_id": "RAM",
#         "employee_id": employee_id,
#         "student_id": "",
#         "full_name": full_name,
#         "detected_at": ts,
#         "in_shift": 0
#     })


# def _purge_expired():
#     cutoff = datetime.now() - timedelta(seconds=_NOTIN_TTL_SEC)
#     _NOTIN[:] = [
#         r for r in _NOTIN
#         if isinstance(r.get("detected_at"), datetime)
#         and r["detected_at"] >= cutoff
#     ]


# # ==========================================================
# # Attendance Logs Tab
# # ==========================================================
# class AttendanceLogs(tb.Frame):
#     SHIFT_END = dtime(17, 0, 0)
#     AUTO_REFRESH_MS = 1200  # 1.2s

#     def __init__(self, parent):
#         super().__init__(parent)

#         self._cache_db = []
#         self._view_rows = []
#         self._only_in_shift = tb.BooleanVar(value=False)
#         self._search_q = tb.StringVar()
#         self._use_day = tb.BooleanVar(value=False)

#         # 🔥 AUTO REFRESH
#         self._auto_refresh = tb.BooleanVar(value=True)
#         self._after_id = None

#         self._build_ui()
#         self._load_default()
#         self._start_auto_refresh()

#     # ================= UI =================
#     def _build_ui(self):
#         top = tb.Frame(self)
#         top.pack(fill=X, pady=6)

#         # ----- Month / Year -----
#         self.cb_month = tb.Combobox(
#             top, width=6,
#             values=[f"{i:02d}" for i in range(1, 13)],
#             state="readonly"
#         )
#         self.cb_year = tb.Combobox(
#             top, width=8,
#             values=[str(y) for y in range(2022, date.today().year + 1)],
#             state="readonly"
#         )

#         tb.Label(top, text="Month").pack(side=LEFT)
#         self.cb_month.pack(side=LEFT, padx=4)
#         tb.Label(top, text="Year").pack(side=LEFT)
#         self.cb_year.pack(side=LEFT, padx=4)

#         # ----- Optional Day -----
#         tb.Checkbutton(
#             top, text="Filter by day",
#             variable=self._use_day,
#             command=self._toggle_day
#         ).pack(side=LEFT, padx=8)

#         self.cb_day = tb.Combobox(top, width=4, state=DISABLED)
#         self.cb_day.pack(side=LEFT)

#         # ----- Controls -----
#         tb.Button(
#             top, text="Refresh",
#             bootstyle=SUCCESS,
#             command=self._query
#         ).pack(side=LEFT, padx=8)

#         tb.Checkbutton(
#             top, text="Only in-shift",
#             variable=self._only_in_shift,
#             command=self._fill_tree
#         ).pack(side=LEFT, padx=10)

#         # 🔥 Auto refresh toggle
#         tb.Checkbutton(
#             top, text="Auto refresh",
#             variable=self._auto_refresh,
#             command=self._on_toggle_auto_refresh
#         ).pack(side=LEFT, padx=10)

#         # ----- Search -----
#         tb.Label(top, text="Search:").pack(side=LEFT)
#         ent = tb.Entry(top, textvariable=self._search_q, width=22)
#         ent.pack(side=LEFT, padx=4)
#         ent.bind("<KeyRelease>", lambda e: self._on_search_changed())

#         # ===== Monthly Donut =====
#         self.donut = MonthlyDonutChart(
#             self,
#             colors={
#                 "bg_secondary": "#161B22",
#                 "bg_tertiary": "#21262D",
#                 "text_primary": "#F0F6FC",
#                 "text_secondary": "#8B949E",
#                 "border": "#30363D",
#                 "accent_secondary": "#238636",
#                 "accent_warning": "#D29922",
#                 "accent_danger": "#F85149",
#             }
#         )
#         self.donut.pack(fill=X, padx=10, pady=6)

#         # ===== Table =====
#         cols = ("log_id", "employee_id", "student_id", "full_name", "detected_at", "in_shift")
#         self.tree = tb.Treeview(self, columns=cols, show="headings")

#         for c in cols:
#             self.tree.heading(c, text=c)
#             self.tree.column(c, width=120 if "id" in c else 200)

#         self.tree.pack(fill=BOTH, expand=YES, padx=8, pady=6)

#         self.tree.tag_configure("in", background="#1f2a1f", foreground="#EAEAEA")
#         self.tree.tag_configure("out", background="#2a1f1f", foreground="#EAEAEA")
        
#         self.cb_month.bind("<<ComboboxSelected>>", lambda e: self._on_month_year_changed())
#         self.cb_year.bind("<<ComboboxSelected>>", lambda e: self._on_month_year_changed())

#     # ================= Logic =================
#     def _load_default(self):
#         today = date.today()
#         self.cb_month.set(f"{today.month:02d}")
#         self.cb_year.set(str(today.year))
#         self._toggle_day()
#         self._query()

#     def _toggle_day(self):
#         if self._use_day.get():
#             self._on_month_year_changed()
#         else:
#             self.cb_day.configure(state=DISABLED)

#     def _on_search_changed(self):
#         self._fill_tree()
#         self._update_monthly_summary()

#     # ================= AUTO REFRESH =================
#     def _start_auto_refresh(self):
#         if self._auto_refresh.get():
#             self._query()
#             self._after_id = self.after(self.AUTO_REFRESH_MS, self._start_auto_refresh)

#     def _stop_auto_refresh(self):
#         if self._after_id:
#             self.after_cancel(self._after_id)
#             self._after_id = None

#     def _on_toggle_auto_refresh(self):
#         if self._auto_refresh.get():
#             self._start_auto_refresh()
#         else:
#             self._stop_auto_refresh()

#     # ================= DB QUERY =================
#     def _query(self):
#         self._cache_db.clear()
#         self._view_rows.clear()
#         _purge_expired()

#         year = int(self.cb_year.get())
#         month = int(self.cb_month.get())

#         # --- luôn load FULL tháng cho cache ---
#         for day in range(1, calendar.monthrange(year, month)[1] + 1):
#             d = date(year, month, day)
#             self._cache_db.extend(list_logs_by_date_with_flag(d))

#         # --- apply filter by day CHỈ cho table ---
#         if self._use_day.get():
#             sel_day = int(self.cb_day.get())
#             self._view_rows = [
#                 r for r in self._cache_db
#                 if isinstance(r.get("detected_at"), datetime)
#                 and r["detected_at"].date() == date(year, month, sel_day)
#             ]
#         else:
#             self._view_rows = list(self._cache_db)

#         self._fill_tree()
#         self._update_monthly_summary()

#     def _fill_tree(self):
#         self.tree.delete(*self.tree.get_children())

#         for r in self._view_rows:
#             if self._only_in_shift.get() and int(r.get("in_shift", 0)) != 1:
#                 continue
#             if not self._match_search(r):
#                 continue

#             t = r.get("detected_at")
#             if isinstance(t, datetime):
#                 t = t.replace(microsecond=0)

#             tag = "in" if int(r.get("in_shift", 0)) == 1 else "out"

#             self.tree.insert(
#                 "",
#                 END,
#                 values=(
#                     r.get("log_id"),
#                     r.get("employee_id"),
#                     r.get("student_id", ""),
#                     r.get("full_name"),
#                     t,
#                     "Yes" if tag == "in" else "No"
#                 ),
#                 tags=(tag,)
#             )

#     def _match_search(self, r):
#         q = self._search_q.get().lower().strip()
#         if not q:
#             return True
#         return (
#             q in str(r.get("employee_id", "")).lower()
#             or q in str(r.get("student_id", "")).lower()
#             or q in str(r.get("full_name", "")).lower()
#         )

#     def _on_month_year_changed(self):
#         if not self._use_day.get():
#             return

#         y = int(self.cb_year.get())
#         m = int(self.cb_month.get())
#         days = calendar.monthrange(y, m)[1]

#         self.cb_day.configure(
#             state="readonly",
#             values=[str(i) for i in range(1, days + 1)]
#         )
#         self.cb_day.set("1")

#     # ================= Monthly Summary =================
#     def _update_monthly_summary(self):
#         rows = [r for r in self._cache_db if self._match_search(r)]
#         emp_ids = {r.get("employee_id") for r in rows if r.get("employee_id")}

#         if len(emp_ids) != 1:
#             self.donut.set_data(0, 0, 0, total_days=0)
#             return

#         emp_id = emp_ids.pop()
#         year = int(self.cb_year.get())
#         month = int(self.cb_month.get())

#         # --- 1. Lấy summary present / late / absent (GIỮ NGUYÊN DAL) ---
#         s = get_monthly_employee_summary(emp_id, year, month)
#         present = s.get("present", 0)
#         late = s.get("late", 0)
#         absent = s.get("absent", 0)

#         # --- 2. Lấy hire_date / end_date ---
#         emp = get_employee_dates(emp_id)
#         hire_date = emp.get("hire_date")
#         end_date = emp.get("end_date")

#         if not hire_date:
#             self.donut.set_data(present, late, absent, total_days=0)
#             return

#         # --- 3. Tính khoảng thời gian hợp lệ ---
#         from datetime import date
#         import calendar

#         month_start = date(year, month, 1)
#         month_end = date(year, month, calendar.monthrange(year, month)[1])

#         today = date.today()
#         data_end = today if (year == today.year and month == today.month) else month_end

#         effective_start = max(hire_date, month_start)
#         effective_end = min(data_end, end_date) if end_date else data_end

#         if effective_end < effective_start:
#             total_days = 0
#         else:
#             total_days = (effective_end - effective_start).days + 1

#         # --- 4. Truyền xuống donut ---
#         self.donut.set_data(
#             present=present,
#             late=late,
#             absent=absent,
#             total_days=total_days
#         )


# tabs/attendance/logs.py
from __future__ import annotations
import csv
from datetime import date, datetime, timedelta, time as dtime
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog, messagebox
from db.attendance_dal import list_logs_by_date_with_flag

# ===== RAM overlay cho logs ngoài giờ (không lưu DB) =====
_NOTIN_TTL_SEC = 600  # 10 phút
_NOTIN: list[dict] = []

def push_not_in_shift(employee_id: int, full_name: str, ts: datetime|None=None):
    ts = ts or datetime.now()
    _NOTIN.append({
        "log_id": "RAM",
        "employee_id": employee_id,
        "full_name": full_name,
        "detected_at": ts,
        "in_shift": 0
    })
    _purge_expired()

def _purge_expired():
    cutoff = datetime.now() - timedelta(seconds=_NOTIN_TTL_SEC)
    keep = []
    for r in _NOTIN:
        t = r.get("detected_at")
        if isinstance(t, str):
            try: t = datetime.fromisoformat(t)
            except Exception: t = None
        if t and t >= cutoff:
            keep.append(r)
    _NOTIN[:] = keep

class AttendanceLogs(tb.Frame):
    """Hiển thị chi tiết log trong 1 ngày, có cờ in_shift (07:00–17:00)."""
    AUTO_REFRESH_MS = 1000
    SHIFT_START = dtime(7, 0, 0)
    SHIFT_END   = dtime(17, 0, 0)

    def __init__(self, parent):
        super().__init__(parent)
        self._cache_db = []
        self._auto_id = None
        self._auto_running = False
        self._only_in_shift = tb.BooleanVar(value=False)
        self._warned_future = False
        self._build_ui()
        self._load_default()
        self._schedule_auto()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build_ui(self):
        top = tb.Frame(self); top.pack(fill=X, pady=6)

        tb.Label(top, text="Day:", foreground="#EAEAEA").pack(side=LEFT, padx=(0,6))
        self.dp_day = tb.DateEntry(top, bootstyle=INFO, dateformat="%Y-%m-%d")
        self.dp_day.pack(side=LEFT, padx=(0,10))
        self.dp_day.bind("<<DateEntrySelected>>", lambda e: (self._clamp_future(), self._update_export_state(), self._query()))

        tb.Button(top, text="Refresh", bootstyle=SUCCESS, command=self._on_manual_refresh).pack(side=LEFT, padx=6)
        # giữ ref để enable/disable
        self.btn_export = tb.Button(top, text="Export CSV", bootstyle=WARNING, command=self._export)
        self.btn_export.pack(side=LEFT, padx=6)

        tb.Checkbutton(top, text="Only in-shift (07:00–17:00)",
                       variable=self._only_in_shift, command=self._fill_tree).pack(side=LEFT, padx=12)

        cols = ("log_id", "employee_id", "full_name", "detected_at", "in_shift")
        self.tree = tb.Treeview(self, columns=cols, show="headings", selectmode="browse", bootstyle=INFO)
        for c in cols:
            self.tree.heading(c, text=c, anchor = W)
        self.tree.column("log_id", width=90, anchor=W)
        self.tree.column("employee_id", width=110, anchor=W)
        self.tree.column("full_name", width=220, anchor=W, stretch=True)
        self.tree.column("detected_at", width=180, anchor=W)
        self.tree.column("in_shift", width=90, anchor=W)
        self.tree.pack(fill=BOTH, expand=YES, padx=8, pady=6)

        self.tree.tag_configure("in",  background="#1f2a1f", foreground="#EAEAEA")
        self.tree.tag_configure("out", background="#2a1f1f", foreground="#EAEAEA")

    def start_auto(self):
        if self._auto_running:
            return
        self._auto_running = True
        self._schedule_auto()

    def stop_auto(self):
        self._auto_running = False
        if self._auto_id:
            try:
                self.after_cancel(self._auto_id)
            except Exception:
                pass
            self._auto_id = None

    def _load_default(self):
        d = date.today()
        self.dp_day.set_date(d)
        self._query()
        self._update_export_state()

    def _clamp_future(self):
        today = date.today()
        d = self.dp_day.get_date()
        if isinstance(d, datetime): d = d.date()
        if d and d > today:
            self.dp_day.set_date(today)
            if not self._warned_future:
                try:
                    messagebox.showinfo("Date limit", "Không thể chọn ngày trong tương lai. Đã đặt về hôm nay.")
                except Exception:
                    pass
                self._warned_future = True
        else:
            self._warned_future = False

    def _update_export_state(self):
        """Enable/Disable Export: cho phép nếu < hôm nay, hoặc == hôm nay và >= 17:00."""
        d = self.dp_day.get_date()
        if isinstance(d, datetime): d = d.date()
        now = datetime.now()
        allow = (d < date.today()) or (d == date.today() and now.time() >= self.SHIFT_END)
        try:
            self.btn_export.configure(state=(NORMAL if allow else DISABLED))
        except Exception:
            pass

    def _on_manual_refresh(self):
        self._clamp_future()
        self._update_export_state()
        self._query()

    def _query(self):
        if not self.winfo_exists():
            return
        # chặn ngày tương lai khi auto tick
        self._clamp_future()
        d = self.dp_day.get_date()
        if isinstance(d, datetime): d = d.date()
        try:
            self._cache_db = list_logs_by_date_with_flag(d)
        except Exception:
            self._cache_db = []
        _purge_expired()
        self._fill_tree()

    def _merge_rows(self):
        d = self.dp_day.get_date()
        if isinstance(d, datetime): d = d.date()
        ram = []
        for r in _NOTIN:
            t = r.get("detected_at")
            tt = None
            if isinstance(t, datetime): tt = t
            else:
                try: tt = datetime.fromisoformat(str(t))
                except Exception: tt = None
            if tt and tt.date() == d:
                ram.append(r)
        return list(self._cache_db) + ram

    def _fill_tree(self):
        rows = self._merge_rows()
        only_in = self._only_in_shift.get()
        for i in self.tree.get_children():
            self.tree.delete(i)

        def _key(r):
            t = r.get("detected_at")
            if isinstance(t, datetime): return t
            try: return datetime.fromisoformat(str(t))
            except Exception: return datetime.min
        rows.sort(key=_key)

        for i, r in enumerate(rows):
            in_shift = int(r.get("in_shift", 0)) == 1
            if only_in and not in_shift:
                continue
            tag = "in" if in_shift else "out"
            tshow = r.get("detected_at")
            if isinstance(tshow, datetime): tshow = tshow.replace(microsecond=0)
            self.tree.insert("", END,
                             values=(r.get("log_id"), r.get("employee_id"),
                                     r.get("full_name"), str(tshow),
                                     "Yes" if in_shift else "No"),
                             tags=(tag,))

    def _export(self):
        d = self.dp_day.get_date()
        if isinstance(d, datetime): d = d.date()
        now = datetime.now()
        if d == date.today() and now.time() < self.SHIFT_END:
            messagebox.showinfo("Export", "Chỉ được export log của ngày hiện tại sau 17:00 (tan ca).")
            return

        rows = self._merge_rows()
        if not rows:
            messagebox.showinfo("Export", "Không có dữ liệu log."); return

        path = filedialog.asksaveasfilename(title="Export Logs",
                                            defaultextension=".csv",
                                            filetypes=[("CSV files","*.csv")])
        if not path: return
        try:
            only_in = self._only_in_shift.get()
            out = [r for r in rows if (not only_in or int(r.get("in_shift", 0)) == 1)]
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["log_id","employee_id","full_name","detected_at","in_shift"])
                for r in out:
                    tshow = r.get("detected_at")
                    if isinstance(tshow, datetime): tshow = tshow.replace(microsecond=0)
                    w.writerow([r.get("log_id"), r.get("employee_id"),
                                r.get("full_name"), tshow,
                                1 if int(r.get("in_shift", 0))==1 else 0])
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

    def _auto_tick(self):
        if not self._auto_running or not self.winfo_exists():
            return
        try:
            self._query()
            self._update_export_state()
        finally:
            self._schedule_auto()

    def _on_destroy(self, *_):
        self._auto_running = False
        if self._auto_id:
            try:
                self.after_cancel(self._auto_id)
            except Exception:
                pass
            self._auto_id = None

    def refresh(self):
        self._on_manual_refresh()
