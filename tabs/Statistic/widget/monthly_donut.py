from __future__ import annotations

import tkinter as tk
import math
from dataclasses import dataclass


@dataclass
class DataPoint:
    label: str
    value: int
    color: str


class MonthlyDonutChart(tk.Canvas):
    MAX_OFFSET = 12
    ANIM_STEP = 2
    GLOW_WIDTH = 4

    def __init__(self, parent, width=260, height=260, colors: dict | None = None):
        super().__init__(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            bg=colors.get("bg_secondary") if colors else "#1e1e1e",
            cursor="hand2"
        )

        self.width = width
        self.height = height
        self.center = (width // 2, height // 2)

        self.outer_radius = min(width, height) // 2 - 10
        self.inner_radius = int(self.outer_radius * 0.6)

        self.colors = colors or {
            "bg_secondary": "#161B22",
            "bg_tertiary": "#21262D",
            "text_primary": "#F0F6FC",
            "text_secondary": "#8B949E",
            "accent_secondary": "#238636",  # Present
            "accent_warning": "#D29922",    # Late
            "accent_danger": "#F85149",     # Absent
        }

        # Data
        self.data_points: list[DataPoint] = []
        self.total_days: int = 0           # nghiệp vụ (hiển thị)
        self.total_value: int = 0          # present + late + absent

        # Render state
        self.slices: list[dict] = []       # mỗi slice có key riêng
        self.offsets: dict[str, float] = {}
        self.hover_key: str | None = None

        # Tooltip & interaction
        self.tooltip: tk.Toplevel | None = None
        self.on_slice_click = None

        self.bind("<Motion>", self._on_mouse_move)
        self.bind("<Leave>", lambda e: self._on_leave())
        self.bind("<Button-1>", self._on_click)

    # ==================================================
    # PUBLIC API
    # ==================================================

    def set_data(self, present=0, late=0, absent=0, total_days: int | None = None):
        self.data_points = [
            DataPoint("Present", present, self.colors["accent_secondary"]),
            DataPoint("Late", late, self.colors["accent_warning"]),
            DataPoint("Absent", absent, self.colors["accent_danger"]),
        ]

        self.total_value = present + late + absent
        self.total_days = total_days if total_days is not None else self.total_value

        self._compute()
        self._draw()

    # ==================================================
    # COMPUTE (KEY-BASED, NO INDEX BUG)
    # ==================================================

    def _compute(self):
        self.slices.clear()
        self.offsets.clear()

        if self.total_value <= 0:
            return

        angle_cursor = 0.0

        for dp in self.data_points:
            if dp.value <= 0:
                continue

            extent = dp.value / self.total_value * 360.0
            self.slices.append({
                "key": dp.label,   # 🔑 key cố định
                "dp": dp,
                "a0": angle_cursor,
                "a1": angle_cursor + extent
            })
            angle_cursor += extent

        self.offsets = {s["key"]: 0.0 for s in self.slices}

    # ==================================================
    # DRAW
    # ==================================================

    def _draw(self):
        self.delete("all")
        cx, cy = self.center

        if self.total_value <= 0:
            self.create_text(
                cx, cy,
                text="No data",
                fill=self.colors["text_secondary"]
            )
            return

        for s in self.slices:
            dp = s["dp"]
            key = s["key"]
            offset = self.offsets.get(key, 0)

            mid_angle = (s["a0"] + s["a1"]) / 2
            rad = math.radians(90 - mid_angle)
            ox = math.cos(rad) * offset
            oy = -math.sin(rad) * offset

            start = 90 - s["a0"]
            extent = -(s["a1"] - s["a0"])

            # ✅ Tkinter đôi khi không vẽ nếu extent đúng -360 (1 slice duy nhất)
            if abs(extent) >= 360:
                # vẽ full vòng ngoài
                self.create_oval(
                    cx - self.outer_radius + ox,
                    cy - self.outer_radius + oy,
                    cx + self.outer_radius + ox,
                    cy + self.outer_radius + oy,
                    fill=dp.color,
                    outline=""
                )
                continue

            if self.hover_key == key:
                self.create_arc(
                    cx - self.outer_radius + ox,
                    cy - self.outer_radius + oy,
                    cx + self.outer_radius + ox,
                    cy + self.outer_radius + oy,
                    start=start,
                    extent=extent,
                    outline=dp.color,
                    width=self.GLOW_WIDTH,
                    style=tk.ARC
                )

            self.create_arc(
                cx - self.outer_radius + ox,
                cy - self.outer_radius + oy,
                cx + self.outer_radius + ox,
                cy + self.outer_radius + oy,
                start=start,
                extent=extent,
                fill=dp.color,
                outline=""
            )
            
        # donut hole
        self.create_oval(
            cx - self.inner_radius,
            cy - self.inner_radius,
            cx + self.inner_radius,
            cy + self.inner_radius,
            fill=self.colors["bg_secondary"],
            outline=""
        )

        # center text
        self.create_text(
            cx, cy - 6,
            text="Monthly Attendance",
            fill=self.colors["text_secondary"],
            font=("Segoe UI", 10)
        )
        self.create_text(
            cx, cy + 12,
            text=f"{self.total_days} days",
            fill=self.colors["text_primary"],
            font=("Segoe UI", 14, "bold")
        )

    # ==================================================
    # INTERACTION
    # ==================================================

    def _on_mouse_move(self, event):
        key = self._detect_slice(event.x, event.y)
        if key != self.hover_key:
            self.hover_key = key
            self._animate()

        if key:
            self._show_slice_tooltip(event.x, event.y, key)
        else:
            self._hide_tooltip()

    def _on_leave(self):
        self.hover_key = None
        self._animate()
        self._hide_tooltip()

    def _on_click(self, event):
        key = self._detect_slice(event.x, event.y)
        if key and callable(self.on_slice_click):
            self.on_slice_click(key)

    # ==================================================
    # ANIMATION
    # ==================================================

    def _animate(self):
        changed = False
        for k in self.offsets:
            target = self.MAX_OFFSET if k == self.hover_key else 0
            if self.offsets[k] < target:
                self.offsets[k] = min(self.offsets[k] + self.ANIM_STEP, target)
                changed = True
            elif self.offsets[k] > target:
                self.offsets[k] = max(self.offsets[k] - self.ANIM_STEP, target)
                changed = True

        self._draw()
        if changed:
            self.after(16, self._animate)

    # ==================================================
    # HIT TEST (100% ACCURATE)
    # ==================================================

    def _detect_slice(self, x, y):
        cx, cy = self.center

        dx = x - cx
        dy = cy - y   # 🔥 ĐẢO TRỤC Y Ở ĐÂY

        dist = math.hypot(dx, dy)
        if not (self.inner_radius < dist < self.outer_radius + self.MAX_OFFSET):
            return None

        # 0° ở đỉnh, tăng theo chiều kim đồng hồ
        angle = (math.degrees(math.atan2(dx, dy)) + 360) % 360

        for s in self.slices:
            if s["a0"] <= angle < s["a1"]:
                return s["key"]
        return None

    # ==================================================
    # TOOLTIP
    # ==================================================

    def _show_slice_tooltip(self, x, y, key: str):
        s = next(s for s in self.slices if s["key"] == key)
        dp = s["dp"]
        pct = (dp.value / self.total_value * 100) if self.total_value else 0

        if not self.tooltip:
            self.tooltip = tk.Toplevel(self)
            self.tooltip.overrideredirect(True)
            self.tooltip.configure(bg=self.colors["bg_tertiary"])

            self.tt_label = tk.Label(
                self.tooltip,
                fg=self.colors["text_primary"],
                bg=self.colors["bg_tertiary"],
                font=("Segoe UI", 11),
                padx=10,
                pady=6,
                justify="left"
            )
            self.tt_label.pack()

        self.tt_label.config(
            text=f"{dp.label}\n{dp.value} days ({pct:.1f}%)"
        )

        self.tooltip.geometry(
            f"+{self.winfo_rootx() + x + 15}+{self.winfo_rooty() + y + 15}"
        )

    def _hide_tooltip(self):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None
