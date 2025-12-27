# tabs/home/services/camera_daemon.py
from __future__ import annotations
import sys, time, threading
from typing import Optional, Callable
import cv2

class CameraDaemon(threading.Thread):
    """
    Đọc camera ở background thread và đẩy frame cho UI.
    - grab() liên tục để xả buffer (tránh lag tăng dần)
    - retrieve() theo nhịp target_fps
    - on_status chỉ gọi khi text thay đổi
    """
    def __init__(
        self,
        cam_index: int,
        on_frame: Callable,                 # on_frame(frame_bgr)
        on_status: Callable[[str], None],   # on_status(text)
        target_fps: int = 30,
        width: int = 640,
        height: int = 480,
        prefer_mjpg: bool = True,
        auto_reconnect: bool = True,
    ):
        super().__init__(daemon=True)
        self.cam_index = cam_index
        self.on_frame = on_frame
        self.on_status = on_status
        self.target_fps = max(5, int(target_fps))
        self.width = int(width)
        self.height = int(height)
        self.prefer_mjpg = bool(prefer_mjpg)
        self.auto_reconnect = bool(auto_reconnect)

        # ❗ KHÔNG dùng tên _stop (đè lên internal của Thread)
        self._stop_event = threading.Event()

        self._cap: Optional[cv2.VideoCapture] = None
        self._last_status: Optional[str] = None

    def stop(self):
        self._stop_event.set()

    def _set_status(self, s: str):
        if s == self._last_status:
            return
        self._last_status = s
        try:
            self.on_status(s)
        except Exception:
            pass

    def _open_cap(self) -> Optional[cv2.VideoCapture]:
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else 0
        cap = cv2.VideoCapture(self.cam_index, backend)
        if not cap or not cap.isOpened():
            return None

        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        # Nhiều camera ổn hơn nếu set FOURCC sớm
        if self.prefer_mjpg:
            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            except Exception:
                pass

        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        except Exception:
            pass

        try:
            cap.set(cv2.CAP_PROP_FPS, float(self.target_fps))
        except Exception:
            pass

        return cap

    def run(self):
        interval = 1.0 / float(self.target_fps)
        next_emit = time.perf_counter()

        while not self._stop_event.is_set():
            try:
                if self._cap is None or not self._cap.isOpened():
                    self._cap = self._open_cap()
                    if self._cap is None:
                        self._set_status("Camera: cannot open")
                        time.sleep(0.5)
                        continue
                    self._set_status("Camera: streaming…")
                    next_emit = time.perf_counter()

                ok = self._cap.grab()  # xả buffer liên tục
                if not ok:
                    self._set_status("Camera: no frame")
                    if self.auto_reconnect:
                        try:
                            self._cap.release()
                        except Exception:
                            pass
                        self._cap = None
                    time.sleep(0.2)
                    continue

                now = time.perf_counter()
                if now < next_emit:
                    # đừng busy-wait quá sát
                    time.sleep(max(0.001, min(0.01, next_emit - now)))
                    continue

                ok, frame = self._cap.retrieve()
                if not ok or frame is None:
                    self._set_status("Camera: no frame")
                    time.sleep(0.05)
                    continue

                # resync nếu trễ quá nhiều
                if now - next_emit > 5 * interval:
                    next_emit = now + interval
                else:
                    next_emit += interval

                try:
                    # NOTE: nếu UI giữ reference lâu, có thể cân nhắc frame.copy()
                    self.on_frame(frame)
                except Exception:
                    pass

            except Exception as e:
                self._set_status(f"Camera error: {e}")
                if self.auto_reconnect:
                    try:
                        if self._cap is not None:
                            self._cap.release()
                    except Exception:
                        pass
                    self._cap = None
                time.sleep(0.2)

        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._set_status("Camera: stopped")
