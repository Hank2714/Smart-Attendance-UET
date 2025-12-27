import ttkbootstrap as tb

class PlaceholderMixin:
    def _attach_placeholder(self, entry, text: str, color="#6F7D85"):
        # tạo style placeholder một lần
        if not hasattr(self, "_ph_style_ready"):
            style = tb.Style()
            style.configure("Placeholder.TEntry", foreground=color)
            self._ph_style_ready = True

        entry._ph_text = text
        entry._ph_is_on = False
        entry._ph_old_style = entry.cget("style") or "TEntry"

        def _show():
            # chỉ hiển thị khi ô đang rỗng
            if not entry.get():
                entry._ph_is_on = True
                entry.configure(style="Placeholder.TEntry")
                entry.insert(0, text)

        def _hide():
            # chỉ ẩn khi đang là placeholder
            if getattr(entry, "_ph_is_on", False):
                entry.delete(0, "end")
                entry._ph_is_on = False
                entry.configure(style=entry._ph_old_style)

        # đặt ban đầu và bind sự kiện
        _show()
        entry.bind("<FocusIn>",  lambda e: _hide(), add="+")
        entry.bind("<FocusOut>", lambda e: _show(), add="+")
        entry.bind("<KeyPress>", lambda e: _hide() if getattr(entry, "_ph_is_on", False) else None, add="+")
