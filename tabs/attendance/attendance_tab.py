# # tabs/attendance/attendance_tab.py
# import ttkbootstrap as tb
# from ttkbootstrap.constants import *
# from .daily import AttendanceDaily
# from .roster import AttendanceRoster
# from .logs import AttendanceLogs

# class AttendanceTab(tb.Frame):
#     """
#     Wrapper: chứa 3 sub-tabs
#       - Daily Summary: thống kê theo ngày (range) + cột Late
#       - By Day (Roster): danh sách Present/Absent/Late của 1 ngày
#       - Logs (Check-in/out): log theo ngày, realtime, khóa export trước 17:00
#     """
#     def __init__(self, parent):
#         super().__init__(parent)
#         self._build_ui()

#     def _build_ui(self):
#         nb = tb.Notebook(self)
#         nb.pack(fill=BOTH, expand=YES, padx=8, pady=8)

#         self.daily_tab  = AttendanceDaily(nb)
#         self.roster_tab = AttendanceRoster(nb)
#         self.logs_tab   = AttendanceLogs(nb)

#         nb.add(self.daily_tab,  text="Daily Summary")
#         nb.add(self.roster_tab, text="By Day (Roster)")
#         nb.add(self.logs_tab,   text="Logs (Check-in/out)")

#     def refresh(self):
#         # ép đồng bộ cả 3 tab
#         for t in (self.daily_tab, self.roster_tab, self.logs_tab):
#             try:
#                 t.refresh()
#             except Exception:
#                 pass

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from .daily import AttendanceDaily
from .roster import AttendanceRoster
from .logs import AttendanceLogs

class AttendanceTab(tb.Frame):
    """
    Wrapper: chứa 3 sub-tabs
    Chỉ cho tab ĐANG ACTIVE auto refresh. Tab bị ẩn sẽ stop auto để tránh nghẹt UI.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        self.nb = tb.Notebook(self)
        self.nb.pack(fill=BOTH, expand=YES, padx=8, pady=8)

        self.daily_tab  = AttendanceDaily(self.nb)
        self.roster_tab = AttendanceRoster(self.nb)
        self.logs_tab   = AttendanceLogs(self.nb)

        self.nb.add(self.daily_tab,  text="Daily Summary")
        self.nb.add(self.roster_tab, text="By Day (Roster)")
        self.nb.add(self.logs_tab,   text="Logs (Check-in/out)")

        # Khi đổi sub-tab: chỉ chạy auto ở tab đang chọn
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

        # Kick lần đầu
        self.after_idle(self._on_tab_changed)

    def _on_tab_changed(self, *_):
        current = self.nb.nametowidget(self.nb.select())

        for t in (self.daily_tab, self.roster_tab, self.logs_tab):
            try:
                if t is current:
                    if hasattr(t, "start_auto"):
                        t.start_auto()
                else:
                    if hasattr(t, "stop_auto"):
                        t.stop_auto()
            except Exception:
                pass

    def refresh(self):
        # chỉ refresh tab đang xem (đỡ nặng)
        try:
            current = self.nb.nametowidget(self.nb.select())
            current.refresh()
        except Exception:
            pass

