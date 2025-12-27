# hardware/uart_daemon.py
from __future__ import annotations
import time
import threading
import serial
from serial.tools import list_ports
from typing import Callable, Optional

from .protocol import (
    AVR_PERSON_DETECTED,  # "NG"
    AVR_CHECK_READY,      # "CK"
    AVR_CONFIRM_OK,       # "CF" (optional)
    AVR_READY,            # "RD"
    PC_CHECK_SENSOR,
    PC_FACE_FAIL,
    pc_face_ok,
)

class UARTDaemon:
    """
    UART Daemon cho ATmega16 (FSM handshake CK)

    States:
        IDLE
          --(NG)--> WAIT_CK
          --(CK)--> RECOGNIZING
          --(send T/F)--> WAIT_RD
          --(RD)--> IDLE

    Key:
    - UI/Recog chỉ nên bắt đầu scan khi CK đã tới.
    - Nếu UI gọi send_success/fail trước CK => lưu pending, đợi CK tới sẽ gửi.
    """

    def __init__(
        self,
        on_person_detected: Callable[[], None],
        on_ready: Optional[Callable[[], None]] = None,
        on_ck: Optional[Callable[[], None]] = None,   # ✅ NEW: CK callback
        baudrate: int = 9600,
        auto_reconnect: bool = True,
        debug: bool = True,
    ):
        self.on_person_detected = on_person_detected
        self.on_ready = on_ready
        self.on_ck = on_ck
        self.baudrate = baudrate
        self.auto_reconnect = auto_reconnect
        self.debug = debug

        self.ser: Optional[serial.Serial] = None
        self.port_name: Optional[str] = None

        self._rx_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

        # IDLE | WAIT_CK | RECOGNIZING | WAIT_RD
        self.state = "IDLE"

        # debounce NG
        self._last_ng_ts = 0.0
        self._ng_debounce_sec = 0.15

        # last sent result (for resend in WAIT_RD)
        self._last_result: Optional[str] = None  # "F" or "Txxxx"

        # pending result (if UI finishes before CK)
        self._pending_result: Optional[str] = None

        self._lock = threading.Lock()

        # resend loop
        self._resend_thread: Optional[threading.Thread] = None
        self._resend_stop = threading.Event()
        self._resend_interval = 0.2
        self._resend_max_secs = 2.5

    def _log(self, *msg):
        # if self.debug:
        #     print("[UART]", *msg)
        return

    def _auto_detect_port(self) -> Optional[str]:
        for p in list_ports.comports():
            desc = (p.description or "").lower()
            if any(k in desc for k in ("ch340", "cp210", "uart")):
                return p.device
        return None

    def connect(self) -> bool:
        if self.ser and self.ser.is_open:
            return True

        port = self._auto_detect_port()
        if not port:
            self._log("No serial port found")
            return False

        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=self.baudrate,
                timeout=0.2,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
            )
            self.port_name = port
            time.sleep(2.0)  # allow MCU reset
            self._log(f"Connected to {port}")
            return True
        except Exception as e:
            self._log("Connect failed:", e)
            self.ser = None
            return False

    def start(self):
        if not self.connect():
            return False
        self._stop_evt.clear()
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()
        return True

    def stop(self):
        self._stop_evt.set()
        self._stop_resend_loop()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self._log("Stopped")

    def _rx_loop(self):
        self._log("RX thread started")

        while not self._stop_evt.is_set():
            if self.auto_reconnect and (not self.ser or not self.ser.is_open):
                self._log("Reconnecting...")
                self.connect()
                time.sleep(1.0)
                continue

            try:
                if self.ser and self.ser.in_waiting:
                    raw = self.ser.readline()
                    try:
                        line = raw.decode("utf-8", errors="ignore").strip()
                    except Exception:
                        continue
                    if not line:
                        continue
                    self._log("RX:", line)
                    self._handle_rx(line)

            except (serial.SerialException, OSError) as e:
                # ✅ QUAN TRỌNG: rút cáp/COM lỗi -> reset ser để auto_reconnect chạy
                self._log("RX serial error:", e)
                try:
                    if self.ser:
                        self.ser.close()
                except Exception:
                    pass
                self.ser = None
                self.port_name = None
                time.sleep(0.8)
                continue

            except Exception as e:
                # lỗi khác: vẫn giữ nhẹ nhàng nhưng cũng nên tránh loop quá nhanh
                self._log("RX error:", e)
                time.sleep(0.3)

            time.sleep(0.01)

        self._log("RX thread exit")

    def _handle_rx(self, msg: str):
        # --- NG: person detected ---
        if msg == AVR_PERSON_DETECTED:
            now = time.time()
            if (now - self._last_ng_ts) < self._ng_debounce_sec:
                self._log("NG debounced")
                return
            self._last_ng_ts = now

            with self._lock:
                if self.state == "IDLE":
                    self._log("Person detected -> WAIT_CK")
                    self.state = "WAIT_CK"
                    self._pending_result = None
                    self._last_result = None
                    self._stop_resend_loop()

                    try:
                        self.on_person_detected()
                    except Exception as e:
                        self._log("on_person_detected error:", e)
                else:
                    self._log("Ignored NG (state =", self.state, ")")
            return

        # --- CK: ATmega ready to receive result ---
        if msg == AVR_CHECK_READY:
            with self._lock:
                if self.state == "WAIT_CK":
                    self._log("ATmega CK -> RECOGNIZING")
                    self.state = "RECOGNIZING"

                    # ✅ báo UI: "bắt đầu scan"
                    if self.on_ck:
                        try:
                            self.on_ck()
                        except Exception as e:
                            self._log("on_ck error:", e)

                    # nếu UI đã có kết quả sớm, gửi ngay bây giờ
                    if self._pending_result:
                        payload = self._pending_result
                        self._pending_result = None
                        sent = self._send(payload)
                        if sent:
                            self._last_result = payload
                            self.state = "WAIT_RD"
                            self._start_resend_loop()
                else:
                    self._log("CK ignored (state =", self.state, ")")
            return

        # --- CF: confirm OK ---
        if msg == AVR_CONFIRM_OK:
            self._log("ATmega confirmed OK (CF)")
            return

        # --- RD: ready / reset done ---
        if msg == AVR_READY:
            with self._lock:
                self._log("ATmega READY (RD) -> IDLE")
                self.state = "IDLE"
                self._last_result = None
                self._pending_result = None

            self._stop_resend_loop()

            if self.on_ready:
                try:
                    self.on_ready()
                except Exception as e:
                    self._log("on_ready error:", e)
            return

        self._log("Unknown message:", msg)

    def _send(self, text: str) -> bool:
        if not self.ser or not self.ser.is_open:
            self._log("Send failed: serial not connected")
            return False
        try:
            payload = (text + "\r\n").encode("ascii", errors="ignore")
            self.ser.write(payload)
            self.ser.flush()
            self._log("TX:", repr(payload))
            return True
        except Exception as e:
            self._log("TX error:", e)
            return False

    def _start_resend_loop(self):
        self._resend_stop.set()
        if self._resend_thread and self._resend_thread.is_alive():
                try:
                    self._resend_thread.join(timeout=0.3)
                except Exception:
                    pass
        self._resend_stop.clear()

        def worker():
            t0 = time.time()
            while not self._resend_stop.is_set():
                with self._lock:
                    if self.state != "WAIT_RD" or not self._last_result:
                        break
                    self._send(self._last_result)

                if (time.time() - t0) >= self._resend_max_secs:
                    break
                time.sleep(self._resend_interval)

        self._resend_thread = threading.Thread(target=worker, daemon=True)
        self._resend_thread.start()

    def _stop_resend_loop(self):
        self._resend_stop.set()

    # ---------------- Public APIs ----------------
    def send_fail(self, resend: bool = False):
        payload = PC_FACE_FAIL
        with self._lock:
            if self.state == "WAIT_CK":
                self._log("send_fail queued (WAIT_CK)")
                self._pending_result = payload
                return

            if self.state not in ("RECOGNIZING", "WAIT_RD"):
                self._log("send_fail ignored (state =", self.state, ")")
                return

            ok = self._send(payload)
            if ok:
                self._last_result = payload
                self.state = "WAIT_RD"

        if resend or self.state == "WAIT_RD":
            self._start_resend_loop()

    def send_success(self, student_id: str | int, resend: bool = False):
        payload = pc_face_ok(student_id)
        with self._lock:
            if self.state == "WAIT_CK":
                self._log("send_success queued (WAIT_CK)")
                self._pending_result = payload
                return

            if self.state not in ("RECOGNIZING", "WAIT_RD"):
                self._log("send_success ignored (state =", self.state, ")")
                return

            ok = self._send(payload)
            if ok:
                self._last_result = payload
                self.state = "WAIT_RD"

        if resend or self.state == "WAIT_RD":
            self._start_resend_loop()

    def send_ruok(self):
        self._send(PC_CHECK_SENSOR)

    def get_state(self) -> str:
        with self._lock:
            return self.state
