# tabs/attendance/roster.py
from __future__ import annotations
import csv
from datetime import date, datetime, time as dtime
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog, messagebox

from db.attendance_dal import count_day, get_day_rosters_inout


class AttendanceRoster(tb.Frame):
    """
    By Day (Roster) realtime: Present / Absent / Late (>08:00)
    - Auto refresh mỗi 1s theo ngày đang chọn.
    - Chặn chọn ngày tương lai (soft clamp).
    - Chặn Export nếu là NGÀY HIỆN TẠI và trước 17:00 (nút sẽ bị disable).
    """
    AUTO_REFRESH_MS = 1000
    SHIFT_END = dtime(17, 0, 0)

    def __init__(self, parent):
        super().__init__(parent)
        self._present = []
        self._absent = []
        self._late = []
        self._auto_id = None
        self._auto_running = False
        self._warned_future = False
        self._build_ui()
        self._load_default()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build_ui(self):
        top = tb.Frame(self); top.pack(fill=X, pady=6)
        tb.Label(top, text="Day:", foreground="#EAEAEA").pack(side=LEFT, padx=(0,6))
        self.dp_day = tb.DateEntry(top, bootstyle=INFO, dateformat="%Y-%m-%d")
        self.dp_day.pack(side=LEFT, padx=(0,10))
        self.dp_day.bind("<<DateEntrySelected>>", lambda e: (self._clamp_future(), self._update_export_state(), self._query()))
        tb.Button(top, text="Refresh", bootstyle=SUCCESS, command=self._on_manual_refresh).pack(side=LEFT, padx=6)

        self.lbl_counts = tb.Label(self, text="", anchor="w", bootstyle=SECONDARY, foreground="#CFCFCF")
        self.lbl_counts.pack(fill=X, padx=8, pady=(0,6))

        nb = tb.Notebook(self); nb.pack(fill=BOTH, expand=YES, padx=8, pady=6)

        frm_p = tb.Frame(nb); nb.add(frm_p, text="Present (07:00–17:00)")
        self.tree_present = self._make_tree(frm_p, ["employee_id","student_id","full_name","check_in","check_out"])

        frm_a = tb.Frame(nb); nb.add(frm_a, text="Absent")
        self.tree_absent = self._make_tree(frm_a, ["employee_id","student_id","full_name"])

        frm_l = tb.Frame(nb); nb.add(frm_l, text="Late arrivals (>08:00)")
        self.tree_late = self._make_tree(frm_l, ["employee_id","student_id","full_name","check_in","check_out"])

        bot = tb.Frame(self); bot.pack(fill=X, padx=8, pady=(0,8))
        self.btn_export_p = tb.Button(bot, text="Export Present CSV", bootstyle=WARNING,
                                      command=lambda: self._export_csv(self._present, "present.csv"))
        self.btn_export_a = tb.Button(bot, text="Export Absent CSV", bootstyle=WARNING,
                                      command=lambda: self._export_csv(self._absent, "absent.csv"))
        self.btn_export_l = tb.Button(bot, text="Export Late CSV", bootstyle=WARNING,
                                      command=lambda: self._export_csv(self._late, "late.csv"))
        self.btn_export_p.pack(side=LEFT, padx=4)
        self.btn_export_a.pack(side=LEFT, padx=4)
        self.btn_export_l.pack(side=LEFT, padx=4)

    def start_auto(self):
        if self._auto_running:
            return
        self._auto_running = True
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

    def _make_tree(self, parent, cols):
        tree = tb.Treeview(
            parent, columns=cols, show="headings",
            selectmode="browse", bootstyle=INFO
        )

        # Map anchor theo kiểu dữ liệu cột (để header trùng với cell)
        def _col_anchor(c: str):
            c_low = (c or "").lower()
            if c_low in ("employee_id", "student_id", "log_id"):
                return E
            if "id" in c_low:
                return E
            # check_in/check_out/checkin cũng để trái cho dễ đọc timestamp
            return W

        for c in cols:
            anc = _col_anchor(c)
            tree.heading(c, text=c, anchor=anc)     # ✅ header align
            tree.column(c, width=120 if "id" in c else 180, anchor=anc, stretch=True)  # ✅ cell align

        tree.pack(fill=BOTH, expand=YES, padx=6, pady=6)
        tree.tag_configure("even", background="#151515", foreground="#EAEAEA")
        tree.tag_configure("odd",  background="#1f1f1f", foreground="#EAEAEA")
        return tree


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
        d = self.dp_day.get_date()
        if isinstance(d, datetime): d = d.date()
        now = datetime.now()
        allow = (d < date.today()) or (d == date.today() and now.time() >= self.SHIFT_END)
        state = NORMAL if allow else DISABLED
        try:
            self.btn_export_p.configure(state=state)
            self.btn_export_a.configure(state=state)
            self.btn_export_l.configure(state=state)
        except Exception:
            pass

    def _on_manual_refresh(self):
        self._clamp_future()
        self._update_export_state()
        self._query()

    def _query(self):
        if not self.winfo_exists():
            return
        self._clamp_future()
        try:
            d = self.dp_day.get_date()
            if isinstance(d, datetime): d = d.date()

            packs = get_day_rosters_inout(d)
            self._present = packs.get("present", [])
            self._absent  = packs.get("absent",  [])
            self._late    = packs.get("late",    [])

            summary = count_day(d)
        except Exception as e:
            self._debug_exc("ROSTER:dal_query", e)
            return

        self._fill_tree(self.tree_present, self._present)
        self._fill_tree(self.tree_absent,  self._absent)

        # sort late by check_in string (đã format sẵn)
        self._late.sort(key=lambda r: (r.get("check_in") or ""))
        self._fill_tree(self.tree_late, self._late)

        self.lbl_counts.configure(
            text=f"{d.isoformat()} | total_active: {summary['total_active']}  "
                 f"present: {summary['present']}  absent: {summary['absent']}  late: {len(self._late)}"
        )

    def _fill_tree(self, tree, rows):
        for i in tree.get_children():
            tree.delete(i)
        cols = tree["columns"]
        for i, r in enumerate(rows):
            vals = [r.get(c, "") for c in cols]
            tree.insert("", END, values=vals, tags=("odd" if i % 2 else "even",))

    def _can_export_selected_day(self) -> bool:
        d = self.dp_day.get_date()
        if isinstance(d, datetime): d = d.date()
        if d == date.today() and datetime.now().time() < self.SHIFT_END:
            messagebox.showinfo("Export", "Chỉ được export roster của ngày hiện tại sau 17:00 (tan ca).")
            return False
        return True

    def _export_csv(self, rows, default_name):
        if not self._can_export_selected_day():
            return
        if not rows:
            messagebox.showinfo("Export", "Không có dữ liệu để xuất.")
            return
        path = filedialog.asksaveasfilename(
            title="Export CSV", defaultextension=".csv",
            initialfile=default_name, filetypes=[("CSV files","*.csv")]
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                cols = list(rows[0].keys())
                w.writerow(cols)
                for r in rows:
                    w.writerow([r.get(c, "") for c in cols])
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

    def _debug_exc(self, where: str, e: Exception):
        print(f"[{where}] {type(e).__name__}: {e!r}")
        try:
            self.lbl_counts.configure(text=f"⚠ {where}: {type(e).__name__}: {e}")
        except Exception:
            pass

    def refresh(self):
        self._on_manual_refresh()
