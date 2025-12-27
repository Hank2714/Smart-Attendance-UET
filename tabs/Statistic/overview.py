# tabs/statistic_tab.py
# ─────────────────────────────────────────────────────────────────────────────
# Statistic tab: Pure Tk Canvas 100% Stacked Area (Present / Late / Absent)
# - Axis labels (Y: Percent (%), X: Day) with vertical Y label
# - Legend in a dedicated top bar (not overlapped by the chart)
# - Hover tooltip + vertical crosshair
# - KPIs:
#     * Employees, Faces: DB counts
#     * Logs Today: realtime (DB only; ignores in-RAM "not-in-shift")
#     * Logs (Month): uses selected Year/Month; realtime if current month, else static
# - Input guard: warns if Year/Month is in the future
# - Chart auto-refresh every 1s when viewing current month (DB mode)
# ─────────────────────────────────────────────────────────────────────────────
import os
import math
import calendar
from datetime import date, datetime

import tkinter as tk
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import messagebox

# DB
from db.db_conn import fetch_one, execute as db_execute
from db.attendance_dal import (
    count_employees, count_faces, count_logs_on_date, count_logs_in_month,
    get_daily_stack_plus,
)

# Reuse the StatCard UI (same as Home tab) for KPI counters
try:
    # When running from project root (common)
    from tabs.home.ui.widgets import StatCard
except Exception:
    try:
        # When running as a package
        from ..home.ui.widgets import StatCard
    except Exception:
        StatCard = None


APP_BASE    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIFT_START = "06:00"
SHIFT_END   = "18:00"


# ─────────────────────────── Helpers
def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


# ─────────────────────────── Canvas Chart
class AreaChartPanel(tk.Canvas):
    def __init__(self, parent, colors):
        super().__init__(parent, bg=colors["bg_secondary"], highlightthickness=0)
        self.colors = colors

        # data
        self.days = []
        self.present = []   # ON-TIME counts (present - late)
        self.late = []
        self.absent = []
        self.totals = []
        self.title = "Daily Attendance — 100% Stacked Area"

        # layout
        self.margins = {"left": 90, "right": 60, "top": 110, "bottom": 90}

        # hover
        self._hover_x = None
        self._last_hover_idx = None

        # animation
        self.anim_progress = 1.0
        self.anim_running = False
        self.anim_step = 0.07  # progress per tick
        self.anim_ms = 16

        # binds
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", lambda e: self.redraw())

        # legend colors
        self.col_present = "#58A6FF"   # blue
        self.col_late    = "#D29922"   # yellow
        self.col_absent  = "#F85149"   # red
        self.border_line = self.colors["text_primary"]

    # Public API
    def set_series(self, days, present, late, absent, totals, title=None, *, animate: bool = True):
        """
        Set/Update data.
        - animate=True: run grow animation (useful on first render / month change)
        - animate=False: update instantly (useful for auto-refresh)
        """
        new_days = list(days or [])
        same_x = (new_days == getattr(self, "days", []))

        self.days = new_days
        self.present = list(present or [])
        self.late = list(late or [])
        self.absent = list(absent or [])
        self.totals = list(totals or [])
        if title:
            self.title = title

        # Nếu chỉ refresh dữ liệu trong cùng tháng (X axis không đổi) thì KHÔNG chạy animation lại.
        if animate and (not same_x):
            self._start_anim()
        else:
            self.anim_running = False
            self.anim_progress = 1.0
            self.redraw()

    # Animation
    def _start_anim(self):
        self.anim_progress = 0.0
        self.anim_running = True
        self._animate()

    def _animate(self):
        if not self.anim_running:
            return
        self.anim_progress += self.anim_step
        if self.anim_progress >= 1.0:
            self.anim_progress = 1.0
            self.anim_running = False
        self.redraw()
        if self.anim_running:
            self.after(self.anim_ms, self._animate)

    # Hover
    def _on_motion(self, e):
        self._hover_x = e.x
        self.redraw()

    def _on_leave(self, e):
        self._hover_x = None
        self._last_hover_idx = None
        self.redraw()

    def _nearest_day_index(self, x, xs):
        if not xs:
            return None
        # find nearest by abs diff
        best_i = 0
        best_d = 1e18
        for i, xx in enumerate(xs):
            d = abs(x - xx)
            if d < best_d:
                best_d = d
                best_i = i
        return best_i

    def _draw_tooltip(self, idx, x, y, w, h, xs, y0):
        if idx is None or idx < 0 or idx >= len(self.days):
            return

        self._last_hover_idx = idx

        # compute stacked % at idx
        t = max(self.totals[idx], 1)
        pv = max(self.present[idx], 0)
        lv = max(self.late[idx], 0)
        av = max(self.absent[idx], 0)
        p_pct = pv / t * 100.0
        l_pct = lv / t * 100.0
        a_pct = av / t * 100.0

        # Tooltip (concise)
        lines = [
            (f"Day {self.days[idx]:02d}", self.colors["text_primary"]),
            (f"Present: {pv}  ({p_pct:.1f}%)", self.col_present),
            (f"Late:    {lv}  ({l_pct:.1f}%)", self.col_late),
            (f"Absent:  {av}  ({a_pct:.1f}%)", self.col_absent),
            (f"Total:   {t}", self.colors["text_secondary"]),
        ]

        # tooltip box size
        pad = 10
        line_h = 18
        box_w = 240
        box_h = pad * 2 + line_h * len(lines)

        # position near cursor line
        tx = xs[idx] + 14
        ty = y + 14
        if tx + box_w > x + w:
            tx = xs[idx] - box_w - 14
        if ty + box_h > y + h:
            ty = y + h - box_h - 6
        tx = _clamp(tx, x + 6, x + w - box_w - 6)
        ty = _clamp(ty, y + 6, y + h - box_h - 6)

        # bg + border
        self.create_rectangle(
            tx, ty, tx + box_w, ty + box_h,
            fill=self.colors["bg_primary"], outline=self.colors["border"], width=2
        )

        # text
        yy = ty + pad
        for txt, col in lines:
            self.create_text(tx + pad, yy, text=txt, fill=col, anchor="nw", font=("Segoe UI", 10))
            yy += line_h

    # Main redraw
    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        m = self.margins
        x = m["left"]
        y = m["top"]
        cw = max(10, w - m["left"] - m["right"])
        ch = max(10, h - m["top"] - m["bottom"])

        # title
        self.create_text(
            x + cw / 2, 26,
            text=self.title, fill=self.colors["text_primary"],
            font=("Segoe UI", 12, "bold")
        )
        self.create_text(
            x + cw / 2, 48,
            text="Hover to inspect day details", fill=self.colors["text_secondary"],
            font=("Segoe UI", 9)
        )

        # plot bg
        self.create_rectangle(x, y, x + cw, y + ch, fill=self.colors["bg_secondary"], outline=self.colors["border"], width=2)

        # if no data
        n = len(self.days)
        if n <= 0:
            self.create_text(x + cw/2, y + ch/2, text="No data", fill=self.colors["text_secondary"], font=("Segoe UI", 12))
            return

        # compute x positions
        if n == 1:
            xs = [x + cw / 2]
        else:
            xs = [x + (i * cw / (n - 1)) for i in range(n)]

        # compute stacked % arrays
        p_pct = []
        l_pct = []
        a_pct = []
        for i in range(n):
            t = max(self.totals[i], 1)
            p = max(self.present[i], 0)
            L = max(self.late[i], 0)
            a = max(self.absent[i], 0)
            p_pct.append(p / t * 100.0)
            l_pct.append(L / t * 100.0)
            a_pct.append(a / t * 100.0)

        # apply animation factor (grow up)
        k = _clamp(self.anim_progress, 0.0, 1.0)
        p_pct = [v * k for v in p_pct]
        l_pct = [v * k for v in l_pct]
        a_pct = [v * k for v in a_pct]

        # y mapping (0..100)
        def y_of(pct):
            return y + ch - (pct / 100.0) * ch

        # stacked boundaries (✅ Present ở đáy)
        # present: 0 -> p
        # late:    p -> p+L
        # absent:  p+L -> p+L+a
        y_pres_top = [y_of(p_pct[i]) for i in range(n)]
        y_late_top = [y_of(p_pct[i] + l_pct[i]) for i in range(n)]
        y_abs_top  = [y_of(p_pct[i] + l_pct[i] + a_pct[i]) for i in range(n)]  # should end at 100*k

        # gridlines + Y labels (0..100)
        for pct in (0, 25, 50, 75, 100):
            yy = y_of(pct)
            self.create_line(x, yy, x + cw, yy, fill=self.colors["border"], width=1)
            self.create_text(x - 14, yy, text=f"{pct}%", fill=self.colors["text_secondary"], anchor="e", font=("Segoe UI", 9))

        # axes
        self.create_line(x, y, x, y + ch, fill=self.colors["text_primary"], width=2)
        self.create_line(x, y + ch, x + cw, y + ch, fill=self.colors["text_primary"], width=2)

        # Y axis label (vertical)
        self.create_text(28, y + ch/2, text="Percent (%)", fill=self.colors["text_secondary"], angle=90, font=("Segoe UI", 10))

        # X labels (day)
        step = 1
        if n > 31:
            step = max(1, n // 10)
        elif n > 15:
            step = 2
        elif n > 10:
            step = 1

        for i in range(0, n, step):
            self.create_text(xs[i], y + ch + 18, text=f"{self.days[i]:02d}", fill=self.colors["text_secondary"], font=("Segoe UI", 9))
        # last label
        if (n - 1) % step != 0:
            self.create_text(xs[-1], y + ch + 18, text=f"{self.days[-1]:02d}", fill=self.colors["text_secondary"], font=("Segoe UI", 9))

        # X axis label
        self.create_text(x + cw / 2, y + ch + 44, text="Day", fill=self.colors["text_secondary"], font=("Segoe UI", 10))

        # --- Filled stacked areas (polygons) ---
        # Present area: baseline -> present top
        poly_pres = [(xs[0], y + ch)] + list(zip(xs, y_pres_top)) + [(xs[-1], y + ch)]
        self.create_polygon(poly_pres, fill=self.col_present, outline="", stipple="gray25")

        # Late area: present top -> late top
        poly_late = list(zip(xs, y_pres_top)) + list(zip(xs[::-1], y_late_top[::-1]))
        self.create_polygon(poly_late, fill=self.col_late, outline="", stipple="gray25")

        # Absent area: late top -> absent top
        poly_abs = list(zip(xs, y_late_top)) + list(zip(xs[::-1], y_abs_top[::-1]))
        self.create_polygon(poly_abs, fill=self.col_absent, outline="", stipple="gray25")

        # Borders (đúng thứ tự đáy -> đỉnh)
        self._stroke_path(xs, y_pres_top, self.col_present, 2)
        self._stroke_path(xs, y_late_top, self.col_late, 2)
        self._stroke_path(xs, y_abs_top,  self.col_absent, 2)

        # Legend (top-right inside margins area)
        lx = x + cw - 10
        ly = y - 70
        items = [("Present", self.col_present), ("Late", self.col_late), ("Absent", self.col_absent)]
        for label, col in items:
            self.create_rectangle(lx - 96, ly, lx - 84, ly + 12, fill=col, outline="")
            self.create_text(lx - 78, ly + 6, text=label, fill=self.colors["text_primary"], anchor="w", font=("Segoe UI", 9))
            ly += 18

        # hover crosshair + tooltip
        if self._hover_x is not None:
            idx = self._nearest_day_index(self._hover_x, xs)
            if idx is not None:
                xx = xs[idx]
                self.create_line(xx, y, xx, y + ch, fill=self.colors["hover"], width=2)
                # tooltip near top of present boundary
                #self._draw_tooltip(idx, x, y, cw, ch, xs, y_pres_top[idx])
                self._draw_tooltip(idx, x, y, cw, ch, xs, y_abs_top[idx])

    def _stroke_path(self, xs, ys, color, width=2):
        if not xs or not ys or len(xs) != len(ys):
            return
        for i in range(len(xs) - 1):
            self.create_line(xs[i], ys[i], xs[i+1], ys[i+1], fill=color, width=width, smooth=True)

    @staticmethod
    def _alpha(hex_color, a=1.0):
        return hex_color


# ─────────────────────────── Statistic Tab (UI + data wiring)
class StatisticOverview(tb.Frame):
    AUTO_REFRESH_MS = 1000       # KPI refresh tick
    CHART_REFRESH_MS = 1000      # Chart auto refresh tick (only for current month)

    def __init__(self, parent, do_global_refresh=None):
        super().__init__(parent)

        # Color theme
        self.colors = {
            "bg_primary":     "#0D1117",
            "bg_secondary":   "#161B22",
            "accent_primary": "#58A6FF",
            "accent_secondary":"#238636",
            "accent_danger":  "#F85149",
            "accent_warning": "#D29922",
            "text_primary":   "#F0F6FC",
            "text_secondary": "#8B949E",
            "border":         "#30363D",
            "hover":          "#262C36",
        }

        self._do_global_refresh = do_global_refresh
        self.configure(padding=8)
        self._rows_cache = []
        self._auto_id = None
        self._chart_auto_id = None

        self._build_ui()
        self.refresh_kpis()
        self._schedule_auto_kpi()
        self._schedule_auto_chart()

        # lần đầu: tải DB theo month đang chọn (mặc định là current)
        self._refresh_from_db()

        self.bind("<<Destroy>>", self._on_destroy, add="+")

    # UI layout
    def _build_ui(self):
        # KPIs
        row1 = tb.Frame(self); row1.pack(fill=X, padx=6, pady=(4, 10))
        if StatCard is not None:
            # 4 cards đều style Card.TFrame trong widgets.py
            self.kpi_emp = StatCard(row1, title="Employees", value="0", bootstyle="success")
            self.kpi_emp.pack(side=LEFT, padx=6, fill=X, expand=YES)

            self.kpi_faces = StatCard(row1, title="Faces", value="0", bootstyle="success")
            self.kpi_faces.pack(side=LEFT, padx=6, fill=X, expand=YES)

            self.kpi_today = StatCard(row1, title="Logs Today", value="0", bootstyle="success")
            self.kpi_today.pack(side=LEFT, padx=6, fill=X, expand=YES)

            self.kpi_mont = StatCard(row1, title="Logs (Month)", value="0", bootstyle="success")
            self.kpi_mont.pack(side=LEFT, padx=6, fill=X, expand=YES)
        else:
            # fallback (giữ cách cũ nếu import lỗi)
            self.kpi_emp   = self._kpi(row1, "Employees",   "0")
            self.kpi_faces = self._kpi(row1, "Faces",       "0")
            self.kpi_today = self._kpi(row1, "Logs Today",  "0")
            self.kpi_mont  = self._kpi(row1, "Logs (Month)","0")

        
        # controls
        row2 = tb.Frame(self); row2.pack(fill=X, padx=6, pady=(0, 8))
        tb.Label(row2, text="Year:").pack(side=LEFT, padx=(0,6))
        self.ent_year = tb.Entry(row2, width=6)
        self.ent_year.insert(0, str(date.today().year)); self.ent_year.pack(side=LEFT)

        tb.Label(row2, text="Month:").pack(side=LEFT, padx=(12,6))
        self.ent_month = tb.Entry(row2, width=4)
        self.ent_month.insert(0, str(date.today().month)); self.ent_month.pack(side=LEFT)

        tb.Button(row2, text="Refresh (DB)", bootstyle=PRIMARY, command=self._refresh_from_db)\
          .pack(side=LEFT, padx=10)

        # chart box
        box = tb.Labelframe(self, text=f"Daily Attendance — 100% Stacked Area  [{SHIFT_START}–{SHIFT_END}]")
        box.pack(fill=BOTH, expand=YES, padx=6, pady=(0,8))
        inner = tk.Frame(box, bg=self.colors["bg_secondary"],
                         highlightbackground=self.colors["border"], highlightthickness=2)
        inner.pack(fill=BOTH, expand=YES, padx=6, pady=6)

        self.chart = AreaChartPanel(inner, self.colors)
        self.chart.pack(fill=BOTH, expand=YES)

        # bottom tools
        row3 = tb.Frame(self); row3.pack(fill=X, padx=6, pady=(0, 6))
        tb.Button(row3, text="Test DB", bootstyle=SECONDARY, command=self._test_db).pack(side=LEFT)
        tb.Button(row3, text="Truncate All", bootstyle=DANGER, command=self._truncate_all).pack(side=LEFT, padx=8)

    def _kpi(self, parent, title, value):
        card = tb.Frame(parent, padding=12, bootstyle="secondary")
        card.pack(side=LEFT, padx=6, fill=X, expand=YES)
        tb.Label(card, text=title, font=("Segoe UI", 10), foreground=self.colors["text_secondary"]).pack(anchor=tk.W)
        lbl = tb.Label(card, text=value, font=("Segoe UI", 18, "bold"), foreground=self.colors["text_primary"])
        lbl.pack(anchor=tk.W, pady=(4,0))
        return lbl

    # Input parse + guards
    def _selected_year_month(self):
        try:
            y = int(self.ent_year.get().strip())
            m = int(self.ent_month.get().strip())
            if y < 2000 or y > 2100: return None, None
            if m < 1 or m > 12: return None, None
            return y, m
        except Exception:
            return None, None

    def _is_future_ym(self, y, m):
        today = date.today()
        return (y, m) > (today.year, today.month)

    def _is_current_ym(self, y, m):
        today = date.today()
        return (y, m) == (today.year, today.month)

    def refresh_kpis(self):
        def _set(kpi_widget, value: int):
            # StatCard có set_value(), còn Label thì configure(text=...)
            if hasattr(kpi_widget, "set_value"):
                kpi_widget.set_value(value)
            else:
                kpi_widget.configure(text=str(value))

        # Employees = ACTIVE
        try:
            _set(self.kpi_emp, int(count_employees(active_only=True) or 0))
        except Exception:
            pass

        try:
            _set(self.kpi_faces, int(count_faces() or 0))
        except Exception:
            pass

        try:
            today = date.today()
            _set(self.kpi_today, int(count_logs_on_date(today) or 0))
        except Exception:
            pass

        try:
            y, m = self._selected_year_month()
            _set(self.kpi_mont, int(count_logs_in_month(y, m) or 0))
        except Exception:
            pass



    def _schedule_auto_kpi(self):
        if self._auto_id:
            try: self.after_cancel(self._auto_id)
            except Exception: pass
        self._auto_id = self.after(self.AUTO_REFRESH_MS, self._auto_tick_kpi)

    def _auto_tick_kpi(self):
        try:
            self.refresh_kpis()
        finally:
            self._schedule_auto_kpi()

    # Chart auto-refresh (only current month)
    def _schedule_auto_chart(self):
        if self._chart_auto_id:
            try: self.after_cancel(self._chart_auto_id)
            except Exception: pass
        self._chart_auto_id = self.after(self.CHART_REFRESH_MS, self._auto_tick_chart)

    def _auto_tick_chart(self):
        try:
            y, m = self._selected_year_month()
            if not y:
                return
            if self._is_current_ym(y, m):
                try:
                    first_day = date(y, m, 1)
                    last_day  = date(y, m, calendar.monthrange(y, m)[1])
                    rows = get_daily_stack_plus(first_day, last_day)
                except Exception as e:
                    messagebox.showerror("DB", f"Lỗi lấy dữ liệu chart: {e}")
                    return
                self._rows_cache = rows or []
                # auto refresh: update data only (NO re-animate)
                self._render_from_rows()
        finally:
            self._schedule_auto_chart()

    # Data flows (chart)
    def _refresh_from_db(self):
        y, m = self._selected_year_month()
        if not y:
            messagebox.showerror("Input", "Year/Month không hợp lệ")
            return
        if self._is_future_ym(y, m):
            messagebox.showwarning("Future Month", "Không thể xem dữ liệu tương lai. Hãy chọn tháng hiện tại hoặc quá khứ.")
            return

        try:
            first_day = date(y, m, 1)
            last_day  = date(y, m, calendar.monthrange(y, m)[1])
            rows = get_daily_stack_plus(first_day, last_day)
        except Exception as e:
            messagebox.showerror("DB", f"Lỗi lấy dữ liệu chart: {e}")
            return

        # Render (manual refresh can animate on month change)
        days, present, late, absent, totals = [], [], [], [], []
        today = date.today()

        for r in (rows or []):
            d = r.get("day")
            if not isinstance(d, date):
                continue

            # ❌ bỏ ngày tương lai
            if d > today:
                continue

            total_active = int(r.get("total_active") or 0)
            if total_active <= 0:
                continue

            p = int(r.get("present") or 0)
            L = int(r.get("late") or 0)
            a = int(r.get("absent") or 0)

            on_time = max(p - L, 0)
            t = on_time + L + a

            days.append(d.day)
            present.append(on_time)
            late.append(L)
            absent.append(a)
            totals.append(t)

        title = f"Present / Late / Absent per day ({y}-{m:02d})  [{SHIFT_START}–{SHIFT_END}]"
        # animate=True default; set_series() sẽ tự không animate lại nếu X không đổi
        self.chart.set_series(days, present, late, absent, totals, title=title)

    def _render_from_rows(self):
        days, present, late, absent, totals = [], [], [], [], []
        today = date.today()
        for r in self._rows_cache:
            d = r.get("day")
            if not isinstance(d, date):
                continue

            # ❌ bỏ ngày tương lai
            if d > today:
                continue

            p = int(r.get("present") or 0)
            L = int(r.get("late") or 0)
            a = int(r.get("absent") or 0)
            on_time = max(p - L, 0)
            t = on_time + L + a

            total_active = int(r.get("total_active") or 0)
            if total_active <= 0:
                continue
            
            days.append(d.day)
            present.append(on_time)
            late.append(L)
            absent.append(a)
            totals.append(t)

        y, m = self._selected_year_month()
        title = f"Present / Late / Absent per day ({y}-{m:02d})  [{SHIFT_START}–{SHIFT_END}]"
        # IMPORTANT: auto refresh => NO animation restart
        self.chart.set_series(days, present, late, absent, totals, title=title, animate=False)

    # Misc ops
    def _test_db(self):
        try:
            row = fetch_one("SELECT DATABASE() AS db")
            messagebox.showinfo("DB OK", f"Connected to: {row['db']}")
        except Exception as e:
            messagebox.showerror("DB Error", str(e))

    def _truncate_all(self):
        if not messagebox.askyesno(
            "Confirm",
            "Clear ALL data?\n- TRUNCATE attendance_logs, faces\n- DELETE employees (reset ID)\n- Xoá ảnh trong data/faces"
        ):
            return
        try:
            db_execute("SET FOREIGN_KEY_CHECKS=0")
            db_execute("TRUNCATE TABLE attendance_logs")
            db_execute("TRUNCATE TABLE faces")
            db_execute("DELETE FROM employees")
            db_execute("ALTER TABLE employees AUTO_INCREMENT = 1")
            db_execute("SET FOREIGN_KEY_CHECKS=1")
            messagebox.showinfo("Done", "Data cleared.")
            # refresh UI
            self.refresh_kpis()
            self._refresh_from_db()
        except Exception as e:
            try:
                db_execute("SET FOREIGN_KEY_CHECKS=1")
            except Exception:
                pass
            messagebox.showerror("Error", str(e))

    def _on_destroy(self, event=None):
        try:
            if self._auto_id:
                self.after_cancel(self._auto_id)
                self._auto_id = None
        except Exception:
            pass
        try:
            if self._chart_auto_id:
                self.after_cancel(self._chart_auto_id)
                self._chart_auto_id = None
        except Exception:
            pass
