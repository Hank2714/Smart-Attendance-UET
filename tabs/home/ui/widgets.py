# # ui/widgets.py
# from __future__ import annotations
# import ttkbootstrap as tb

# class StatCard(tb.Frame):
#     """Ô thống kê đơn giản."""
#     def __init__(self, parent, title="Title", value="0", bootstyle="secondary"):
#         super().__init__(parent, padding=(12, 10))
#         self['style'] = 'Card.TFrame'
#         self.title_lbl = tb.Label(self, text=title, font=('Segoe UI', 11, 'bold'))
#         self.value_lbl = tb.Label(self, text=str(value),
#                                    font=('Segoe UI', 28, 'bold'),
#                                    bootstyle=bootstyle)
#         self.title_lbl.pack(anchor='w')
#         self.value_lbl.pack(anchor='w', pady=(2, 0))

#     def set_value(self, v):
#         self.value_lbl.configure(text=str(v))

# ui/widgets.py
from __future__ import annotations
import ttkbootstrap as tb

class StatCard(tb.Frame):
    """Ô thống kê đơn giản (có viền + màu số theo bootstyle)."""
    def __init__(
        self,
        parent,
        title="Title",
        value="0",
        bootstyle="secondary",
        outline=True,
        outline_width=1,
    ):
        super().__init__(parent, padding=(12, 10))
        self['style'] = 'Card.TFrame'

        # ✅ Viền cho từng ô
        if outline:
            # ttk Frame có borderwidth/relief (đa số theme sẽ thấy rõ)
            self.configure(borderwidth=outline_width, relief="solid")

        # Title
        self.title_lbl = tb.Label(
            self,
            text=title,
            font=('Segoe UI', 11, 'bold')
        )

        # ✅ Value: màu theo bootstyle của từng card
        self.value_lbl = tb.Label(
            self,
            text=str(value),
            font=('Segoe UI', 28, 'bold'),
            bootstyle=bootstyle
        )

        self.title_lbl.pack(anchor='w')
        self.value_lbl.pack(anchor='w', pady=(2, 0))

    def set_value(self, v):
        self.value_lbl.configure(text=str(v))

    def set_style(self, bootstyle: str):
        """Đổi màu số runtime nếu cần."""
        self.value_lbl.configure(bootstyle=bootstyle)
