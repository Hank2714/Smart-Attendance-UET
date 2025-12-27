from __future__ import annotations

import csv
import calendar
from datetime import date
import os
import tkinter as tk
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog
from PIL import Image, ImageTk

from db.attendance_dal import (
    list_employees,
    get_monthly_employee_summary,
    get_employee_dates,
    get_face,
)

from tabs.Statistic.widget.monthly_donut import MonthlyDonutChart


# =========================================================
# Mini stat widget (colored)
# =========================================================
class MiniStat(tk.Frame):
    def __init__(self, parent, title: str, color: str):
        super().__init__(parent, bg="#161B22", highlightthickness=1, highlightbackground="#30363D")
        self._color = color

        # layout giống “card” như ảnh thứ 2, nhưng giữ màu chữ
        self.lbl_title = tk.Label(
            self,
            text=title,
            font=("Segoe UI", 10, "bold"),
            fg=color,
            bg="#161B22",
            anchor="w"
        )
        self.lbl_title.pack(fill="x", padx=14, pady=(12, 0))

        self.lbl_value = tk.Label(
            self,
            text="0",
            font=("Segoe UI", 28, "bold"),
            fg=color,
            bg="#161B22",
            anchor="w"
        )
        self.lbl_value.pack(fill="x", padx=14, pady=(6, 14))

    def set(self, v: int):
        self.lbl_value.configure(text=str(v))


# =========================================================
# Monthly Summary Tab
# =========================================================
class MonthlySummaryTab(tb.Frame):
    def __init__(self, parent):
        super().__init__(parent)

        self._rows: list[dict] = []
        self._photo_ref = None

        self._build_ui()
        self._load_default()

    # =====================================================
    # UI
    # =====================================================
    def _build_ui(self):
        # ---------- Controls ----------
        top = tb.Frame(self)
        top.pack(fill=X, pady=6)

        tb.Label(top, text="Year").pack(side=LEFT)
        self.cb_year = tb.Combobox(
            top,
            width=8,
            values=[str(y) for y in range(2022, date.today().year + 1)],
            state="readonly",
        )
        self.cb_year.pack(side=LEFT, padx=4)

        tb.Label(top, text="Month").pack(side=LEFT, padx=(10, 0))
        self.cb_month = tb.Combobox(
            top,
            width=6,
            values=[f"{m:02d}" for m in range(1, 13)],
            state="readonly",
        )
        self.cb_month.pack(side=LEFT, padx=4)

        tb.Button(top, text="Refresh", bootstyle=PRIMARY, command=self._refresh)\
            .pack(side=LEFT, padx=10)

        tb.Button(top, text="Export CSV", bootstyle=WARNING, command=self._export_csv)\
            .pack(side=LEFT, padx=4)

        tb.Label(top, text="Search:").pack(side=LEFT, padx=(20, 4))
        self.ent_search = tb.Entry(top, width=24)
        self.ent_search.pack(side=LEFT)
        self.ent_search.bind("<KeyRelease>", lambda e: self._fill_tree())

        # ---------- Body ----------
        body = tb.Frame(self)
        body.pack(fill=BOTH, expand=YES, padx=6, pady=6)

        self.frm_left = tb.Labelframe(body, text="Preview Info", width=260)
        self.frm_left.pack(side=LEFT, fill=Y, padx=(0, 8))
        self.frm_left.pack_propagate(False)

        self.frm_center = tb.Frame(body)
        self.frm_center.pack(side=LEFT, fill=BOTH, expand=YES)

        self.frm_right = tb.Frame(body, width=280)
        self.frm_right.pack(side=LEFT, fill=Y, padx=(8, 0))
        self.frm_right.pack_propagate(False)

        self._build_preview()
        self._build_tree()
        self._build_donut_panel()

    # =====================================================
    # Preview
    # =====================================================
    def _build_preview(self):
        f = self.frm_left

        # --- fixed size image holder ---
        img_box = tb.Frame(f, width=180, height=180, bootstyle="secondary")
        img_box.pack(pady=10)
        img_box.pack_propagate(False)  # 🔒 FIXED SIZE

        self.lbl_img = tk.Label(
            img_box,
            text="No image",
            bg="#21262D",
            fg="#8B949E",
            anchor="center"
        )
        self.lbl_img.pack(fill=BOTH, expand=YES)

        self._photo_ref = None
        self._pv = {}

        def row(label):
            r = tb.Frame(f)
            r.pack(fill=X, pady=6, padx=6)

            tb.Label(
                r,
                text=label,
                width=10,
                font=("Segoe UI", 10, "bold"),
                foreground="#8B949E"
            ).pack(side=LEFT)

            v = tb.Label(
                r,
                text="-",
                font=("Segoe UI", 11),
                foreground="#F0F6FC"
            )
            v.pack(side=LEFT, fill=X, expand=YES)
            return v

        self._pv["employee_id"] = row("Emp ID")
        self._pv["student_id"]  = row("Student ID")
        self._pv["full_name"]   = row("Full name")
        self._pv["email"]       = row("Email")
        self._pv["phone"]       = row("Phone")
        self._pv["hire_date"]   = row("Hire date")

    # =====================================================
    # Treeview
    # =====================================================
    def _build_tree(self):
        cols = (
            "employee_id",
            "student_id",
            "full_name",
            "present",
            "late",
            "absent",
            "total_days",
        )

        # ==============================
        # STYLE RIÊNG cho Monthly Treeview
        # ==============================
        style = tb.Style()

        style.configure(
            "Monthly.Treeview",
            font=("Segoe UI", 12),
            rowheight=32,
            background="#2B2B2B",
            fieldbackground="#2B2B2B",
            foreground="#F0F6FC",
        )

        style.configure(
            "Monthly.Treeview.Heading",
            font=("Segoe UI", 12, "bold"),
            background="#1F6FEB",   # 🔵 nền xanh
            foreground="#FFFFFF",   # ⚪ chữ trắng
            relief="flat",
        )

        # 👇 bắt buộc: map để Tkinter KHÔNG tự đổi màu khi hover/click
        style.map(
            "Monthly.Treeview.Heading",
            background=[("active", "#1F6FEB"), ("pressed", "#1F6FEB")],
            foreground=[("active", "#FFFFFF"), ("pressed", "#FFFFFF")],
        )

        self.tree = tb.Treeview(
            self.frm_center,
            columns=cols,
            show="headings",
            style="Monthly.Treeview",   # ⚠️ style riêng
            bootstyle=INFO,
        )

        widths = {
            "employee_id": 90,
            "student_id": 120,
            "full_name": 360,
            "present": 80,
            "late": 80,
            "absent": 90,
            "total_days": 100,
        }

        for c in cols:
            self.tree.heading(
                c,
                text=c.replace("_", " ").title(),
                anchor=W if c == "full_name" else CENTER
            )
            self.tree.column(
                c,
                width=widths[c],
                minwidth=60,
                anchor=W if c == "full_name" else CENTER,
                stretch=(c == "full_name"),
            )

        self.tree.pack(fill=BOTH, expand=YES)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    # =====================================================
    # Donut + stats
    # =====================================================
    def _build_donut_panel(self):
        self.donut = MonthlyDonutChart(
            self.frm_right,
            width=240,
            height=240,
            colors={
                "bg_secondary": "#161B22",
                "bg_tertiary": "#21262D",
                "text_primary": "#F0F6FC",
                "text_secondary": "#8B949E",
                "accent_secondary": "#238636",  # Present
                "accent_warning": "#D29922",    # Late
                "accent_danger": "#F85149",     # Absent
            },
        )
        self.donut.pack(pady=8)

        # ✅ text dưới donut: to hơn + màu trắng
        self.lbl_emp = tb.Label(
            self.frm_right,
            text="Select an employee",
            justify=CENTER,
            foreground="#F0F6FC",
            font=("Segoe UI", 11, "bold"),
        )
        self.lbl_emp.pack(pady=6)

        # ✅ MiniStat: màu chữ đúng theo màu donut
        self.stat_present = MiniStat(self.frm_right, "Present", "#238636")
        self.stat_late    = MiniStat(self.frm_right, "Late", "#D29922")
        self.stat_absent  = MiniStat(self.frm_right, "Absent", "#F85149")
        self.stat_total   = MiniStat(self.frm_right, "Total days", "#F0F6FC")  # (không có slice nên để trắng)

        for w in (self.stat_present, self.stat_late, self.stat_absent, self.stat_total):
            w.pack(fill=X, padx=10, pady=4)

    # =====================================================
    # Data
    # =====================================================
    def _load_default(self):
        today = date.today()
        self.cb_year.set(str(today.year))
        self.cb_month.set(f"{today.month:02d}")
        self._refresh()

    def _refresh(self):
        self._rows.clear()
        self.tree.delete(*self.tree.get_children())
        self._clear_preview()

        y, m = self._get_year_month()
        if not y:
            return

        # ✅ ONLY ACTIVE EMPLOYEES
        for e in list_employees(active_only=True):
            s = get_monthly_employee_summary(e["employee_id"], y, m)

            # ✅ total days đúng theo hire_date/end_date (và không tính ngày tương lai nếu là tháng hiện tại)
            total = self._calc_effective_days(e, y, m)

            # ✅ Absent phải <= total_days
            absent = max(total - s["present"] - s["late"], 0)

            row = dict(e)
            row.update(s)
            row["absent"] = absent
            row["total_days"] = total
            self._rows.append(row)

        self._fill_tree()

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        q = self.ent_search.get().lower().strip()

        for r in self._rows:
            if q and q not in str(r.get("student_id","")).lower() \
               and q not in str(r.get("full_name","")).lower():
                continue

            self.tree.insert(
                "",
                END,
                values=(
                    r["employee_id"],
                    r.get("student_id",""),
                    r.get("full_name",""),
                    r["present"],
                    r["late"],
                    r["absent"],
                    r["total_days"],
                ),
            )

    # =====================================================
    # Selection
    # =====================================================
    def _on_select(self, *_):
        sel = self.tree.selection()
        if not sel:
            return

        emp_id = int(self.tree.item(sel[0], "values")[0])
        emp = next((r for r in self._rows if r["employee_id"] == emp_id), None)
        if not emp:
            return

        self._update_preview(emp)

        y, m = self._get_year_month()
        total_days = self._calc_effective_days(emp, y, m)

        # ✅ clamp absent theo total_days để donut + stats không bị lệch
        present = int(emp.get("present", 0))
        late    = int(emp.get("late", 0))
        absent  = max(total_days - present - late, 0)

        self.donut.set_data(present, late, absent, total_days=total_days)

        self.stat_present.set(present)
        self.stat_late.set(late)
        self.stat_absent.set(absent)
        self.stat_total.set(total_days)

        self.lbl_emp.configure(
            text=f"Employee ID: {emp_id}\n{emp.get('full_name','')}"
        )

    # =====================================================
    # Preview helpers
    # =====================================================
    def _clear_preview(self):
        self.lbl_img.configure(text="No image", image="")
        for v in self._pv.values():
            v.configure(text="-")

        self.donut.set_data(0, 0, 0, total_days=0)
        self.stat_present.set(0)
        self.stat_late.set(0)
        self.stat_absent.set(0)
        self.stat_total.set(0)

    def _update_preview(self, emp: dict):
        for k in self._pv:
            self._pv[k].configure(text=str(emp.get(k, "")))

        face = get_face(emp["employee_id"])
        if face and os.path.exists(face["image_path"]):
            try:
                im = Image.open(face["image_path"]).convert("RGB")

                # Resize + crop vuông để fit khung preview
                target = 180
                w, h = im.size
                scale = target / min(w, h)
                nw, nh = int(w * scale), int(h * scale)
                im = im.resize((nw, nh), Image.LANCZOS)

                left = (nw - target) // 2
                top = (nh - target) // 2
                im = im.crop((left, top, left + target, top + target))

                img = ImageTk.PhotoImage(im)
                self._photo_ref = img  # giữ reference
                self.lbl_img.configure(image=img, text="")
            except Exception:
                self.lbl_img.configure(image="", text="Invalid image")
        else:
            self.lbl_img.configure(image="", text="No image")

    # =====================================================
    # Utils
    # =====================================================
    def _get_year_month(self):
        try:
            return int(self.cb_year.get()), int(self.cb_month.get())
        except Exception:
            return None, None

    @staticmethod
    def _calc_effective_days(emp: dict, y: int, m: int) -> int:
        hire = emp.get("hire_date")
        end  = emp.get("end_date")
        if not hire:
            return 0

        ms = date(y, m, 1)
        me = date(y, m, calendar.monthrange(y, m)[1])
        today = date.today()
        if (y, m) == (today.year, today.month):
            me = min(me, today)

        start = max(hire, ms)
        stop  = min(end, me) if end else me
        return max((stop - start).days + 1, 0)

    def _export_csv(self):
        if not self._rows:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files","*.csv")],
        )
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                ["employee_id","student_id","full_name","present","late","absent","total_days"]
            )
            for r in self._rows:
                w.writerow([
                    r["employee_id"],
                    r.get("student_id",""),
                    r.get("full_name",""),
                    r["present"],
                    r["late"],
                    r["absent"],
                    r["total_days"],
                ])
