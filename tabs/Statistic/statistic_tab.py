# tabs/statistic/statistic_tab.py
from __future__ import annotations

import ttkbootstrap as tb
from ttkbootstrap.constants import *


class StatisticTab(tb.Frame):
    """
    Statistic main tab
    - Acts as a container (Notebook)
    - Holds sub-tabs: Overview, Monthly Summary, ...
    """

    def __init__(self, parent):
        super().__init__(parent, padding=8)
        self._build_ui()

    def _build_ui(self):
        nb = tb.Notebook(self)
        nb.pack(fill=BOTH, expand=YES)

        # ===== Sub-tabs =====
        from .overview import StatisticOverview
        from .monthly_summary import MonthlySummaryTab

        self.tab_overview = StatisticOverview(nb)
        nb.add(self.tab_overview, text="Overview")

        self.tab_monthly = MonthlySummaryTab(nb)
        nb.add(self.tab_monthly, text="Monthly Summary")
