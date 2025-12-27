# tabs/home/services/recog_daemon.py
from __future__ import annotations
import os
import time
import threading
import tempfile
from typing import Any, Callable, Dict, Optional, List, Tuple
import cv2
import numpy as np
import unicodedata

try:
    from deepface import DeepFace
    _HAS_DEEPFACE = True
except Exception:
    _HAS_DEEPFACE = False

try:
    from mtcnn import MTCNN as _MTCNN_pkg
    _HAS_MTCNN = True
except Exception:
    _HAS_MTCNN = False
    _MTCNN_pkg = None  # type: ignore


def _to_rgb(img_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

def _var_laplacian(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32); b = b.astype(np.float32)
    na = np.linalg.norm(a) + 1e-9; nb = np.linalg.norm(b) + 1e-9
    return float(np.dot(a, b) / (na * nb))

def _ascii_no_diacritics(s: str) -> str:
    if not s:
        return s
    s_norm = unicodedata.normalize("NFKD", s)
    s_ascii = "".join(ch for ch in s_norm if not unicodedata.combining(ch))
    s_ascii = s_ascii.replace("đ", "d").replace("Đ", "D")
    return " ".join(s_ascii.split())


class RecognitionDaemon(threading.Thread):
    """Camera frame -> detect -> crop -> embedding -> cosine matching."""

    def __init__(
        self,
        last_frame_supplier: Callable[[], Optional[Any]],
        lib_supplier: Callable[[], List[Dict[str, Any]]],
        on_status: Callable[[str, str], None],
        on_hit: Callable[[int, int, str], None],
        on_visual: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None,
        period_sec: float = 1.0,
        threshold: float = 0.66,
        conf_min: float = 0.90,
        min_size_px: int = 120,
        top2_delta: float = 0.08,
        blur_thr: float = 50.0,
        model_name: str = "VGG-Face",
        rebuild_secs: float = 20.0
    ):
        super().__init__(daemon=True)
        self._last_frame_supplier = last_frame_supplier
        self._lib_supplier = lib_supplier
        self._on_status = on_status
        self._on_hit = on_hit
        self._on_visual = on_visual or (lambda *_: None)

        self._period = max(0.5, float(period_sec))
        self._threshold = float(threshold)
        self._conf_min = float(conf_min)
        self._min_size_px = int(min_size_px)
        self._top2_delta = float(top2_delta)
        self._blur_thr = float(blur_thr)
        self._model_name = str(model_name)
        self._rebuild_secs = float(rebuild_secs)

        self._stop_event = threading.Event()

        # thread-safe state
        self._state_lock = threading.Lock()
        self._consecutive_hits = 0
        self._last_hit_id: Optional[int] = None

        self._mtcnn = None
        if _HAS_MTCNN:
            try:
                self._mtcnn = _MTCNN_pkg()
            except Exception:
                self._mtcnn = None

        self._lib_cache: List[Dict[str, Any]] = []
        self._emb_cache: Dict[int, List[np.ndarray]] = {}
        self._last_rebuild = 0.0
        self._last_lib_count = -1

        self._armed_ts = 0.0
        self._min_arm_delay = 0.30

        self._last_status_key = None
        self._last_source_count = -1

        # ✅ NEW: pause recognition after success until arm_new_session()
        self._paused = True
        self._paused_notified = False
        self._armed_until = 0.0 #timestamp: hết hạn phiên nhận diện (0 = không armed)

        # TEMP FILE REUSE (giảm I/O)
        self._tmp_crop_path = os.path.join(
            tempfile.gettempdir(),
            f"smartatt_crop_{os.getpid()}_{id(self)}.jpg"
        )
        self._tmp_img_path = os.path.join(
            tempfile.gettempdir(),
            f"smartatt_img_{os.getpid()}_{id(self)}.jpg"
        )

    # ---------- control ----------
    def stop(self):
        self._stop_event.set()

    

    def arm_new_session(self, window_sec: float = 15.0):
        """
        Gọi khi bắt đầu 1 phiên 'checking' mới (sensor trigger).
        Mở nhận diện trong window_sec giây, hết hạn tự pause.
        """
        with self._state_lock:
            self._consecutive_hits = 0
            self._last_hit_id = None

            # để logic _min_arm_delay trong run() tự set lại
            self._armed_ts = 0.0

            now = time.time()
            self._armed_until = now + float(window_sec)

            self._paused = False
            self._paused_notified = False

    def pause(self):
        """Tắt nhận diện (idle) cho tới khi arm_new_session()."""
        with self._state_lock:
            self._paused = True
            self._paused_notified = False
            self._armed_until = 0.0
            self._armed_ts = 0.0
            self._consecutive_hits = 0
            self._last_hit_id = None

    # ---------- embedding ----------
    def _deepface_represent_path(self, path: str, backend: str) -> Optional[np.ndarray]:
        reps = DeepFace.represent(
            img_path=path,
            model_name=self._model_name,
            detector_backend=backend,
            enforce_detection=False,
            align=True
        )
        if isinstance(reps, list) and reps:
            return np.array(reps[0]["embedding"], dtype=np.float32)
        return None

    def _embed_path(self, path: str) -> Optional[np.ndarray]:
        # Try direct first
        for backend in ("opencv", "retinaface", "skip"):
            try:
                emb = self._deepface_represent_path(path, backend)
                if emb is not None:
                    return emb
            except Exception:
                pass

        # Fallback: overwrite 1 temp cố định (không create/delete liên tục)
        try:
            img = cv2.imread(path)
            if img is None:
                return None
            cv2.imwrite(self._tmp_img_path, img)
            for backend in ("opencv", "retinaface", "skip"):
                try:
                    emb = self._deepface_represent_path(self._tmp_img_path, backend)
                    if emb is not None:
                        return emb
                except Exception:
                    pass
            return None
        except Exception:
            return None

    def _embed_crop(self, crop_bgr: np.ndarray) -> Optional[np.ndarray]:
        """Ưu tiên in-memory; fallback overwrite 1 temp file cố định."""
        # Try in-memory first
        try:
            reps = DeepFace.represent(
                img_path=crop_bgr,
                model_name=self._model_name,
                detector_backend="skip",
                enforce_detection=False,
                align=False
            )
            if isinstance(reps, list) and reps:
                return np.array(reps[0]["embedding"], dtype=np.float32)
        except Exception:
            pass

        # Fallback: overwrite 1 temp file
        try:
            ok = cv2.imwrite(self._tmp_crop_path, crop_bgr)
            if not ok:
                return None
            reps = DeepFace.represent(
                img_path=self._tmp_crop_path,
                model_name=self._model_name,
                detector_backend="skip",
                enforce_detection=False,
                align=False
            )
            if isinstance(reps, list) and reps:
                return np.array(reps[0]["embedding"], dtype=np.float32)
            return None
        except Exception:
            return None

    # ---------- build library ----------
    def _build_library(self) -> bool:
        self._lib_cache = []
        self._emb_cache = {}
        lib = self._lib_supplier() or []

        for it in lib:
            try:
                eid = int(it["eid"])
                path = it["img_abs"]
                if not path or not os.path.isfile(path):
                    continue
                emb = self._embed_path(path)
                if emb is None:
                    continue
                self._lib_cache.append(it)
                self._emb_cache.setdefault(eid, []).append(emb)
            except Exception:
                continue

        self._last_lib_count = len(self._lib_cache)
        self._last_rebuild = time.time()
        self._last_source_count = len(lib)
        return self._last_lib_count > 0

    # ---------- detection ----------
    def _detect_faces_using_mtcnn(self, rgb_img: np.ndarray) -> List[Dict[str, float]]:
        if not self._mtcnn:
            return []
        try:
            boxes = []
            for r in self._mtcnn.detect_faces(rgb_img) or []:
                conf = float(r.get("confidence", 0.0))
                bx, by, bw, bh = r.get("box", [0, 0, 0, 0])
                x1, y1, w, h = max(0, int(bx)), max(0, int(by)), max(0, int(bw)), max(0, int(bh))
                if w >= self._min_size_px and h >= self._min_size_px and conf >= self._conf_min:
                    cx, cy = x1 + w / 2.0, y1 + h / 2.0
                    boxes.append({"x1": x1, "y1": y1, "w": w, "h": h, "cx": cx, "cy": cy,
                                  "area": float(w * h), "conf": conf})
            return boxes
        except Exception:
            return []

    def _choose_best_box(self, boxes: List[Dict[str, float]], img_w: int, img_h: int):
        if not boxes:
            return None
        img_cx, img_cy = img_w / 2.0, img_h / 2.0
        def scorer(b):
            dist = ((b["cx"] - img_cx) ** 2 + (b["cy"] - img_cy) ** 2) ** 0.5
            return (-b["conf"], -b["area"], dist)
        return sorted(boxes, key=scorer)[0]

    # ---------- matching ----------
    def _match_embedding(self, emb: np.ndarray) -> Optional[Tuple[int, int, str, float, float]]:
        if not self._lib_cache or not self._emb_cache:
            return None
        sims: List[Tuple[int, float]] = []
        for it in self._lib_cache:
            eid = int(it["eid"])
            emb_list = self._emb_cache.get(eid) or []
            if not emb_list:
                continue
            smax = max((_cosine(emb, e) for e in emb_list), default=0.0)
            sims.append((eid, smax))
        if not sims:
            return None
        sims.sort(key=lambda t: t[1], reverse=True)
        top1_eid, s1 = sims[0]
        s2 = sims[1][1] if len(sims) > 1 else 0.0
        info = next((x for x in self._lib_cache if int(x["eid"]) == top1_eid), None)
        if not info:
            return None
        sid = int(info.get("student_id") or 0)
        name = str(info.get("full_name") or "")
        return (top1_eid, sid, name, s1, s2)
    
    def _reset_hits(self):
        with self._state_lock:
            self._consecutive_hits = 0
            self._last_hit_id = None

    def run(self):
        if not _HAS_DEEPFACE or not self._mtcnn:
            self._set_status("⚠️ DeepFace or MTCNN missing", "warn")
            return

        self._set_status(f"Building face library… ({self._model_name})", "idle")
        ok = self._build_library()
        if not ok:
            self._set_status("No faces in database", "warn")

        self._set_status("Recognition ready — waiting for sensor…", "idle")

        try:
            while not self._stop_event.is_set():
                t0 = time.time()
                try:
                    # ✅ ON-DEMAND gating: nếu chưa armed hoặc đã hết hạn -> pause
                    now_ts = time.time()
                    with self._state_lock:
                        paused = self._paused
                        notified = self._paused_notified
                        armed_until = self._armed_until

                    # hết hạn phiên nhận diện -> pause
                    if (not paused) and armed_until and (now_ts > armed_until):
                        with self._state_lock:
                            self._paused = True
                            self._paused_notified = False
                        self._stop_event.wait(0.05)
                        continue

                    # đang pause -> ngủ nhẹ
                    if paused:
                        if not notified:
                            self._set_status("Idle — waiting for sensor trigger…", "idle")
                            with self._state_lock:
                                self._paused_notified = True
                        self._stop_event.wait(0.1)
                        continue

                    # Rebuild lib periodically (chỉ khi không pause)
                    if (time.time() - self._last_rebuild) >= self._rebuild_secs:
                        cur_lib = self._lib_supplier() or []
                        if len(cur_lib) != self._last_source_count:
                            self._build_library()
                        else:
                            self._last_rebuild = time.time()

                    frame_bgr = self._last_frame_supplier()
                    if frame_bgr is None:
                        self._on_visual(None)
                        self._sleep_rest(t0)
                        continue

                    # ARMING DELAY
                    now_ts = time.time()
                    with self._state_lock:
                        if self._armed_ts == 0.0:
                            self._armed_ts = now_ts
                        armed_ts = self._armed_ts

                    if (now_ts - armed_ts) < self._min_arm_delay:
                        self._sleep_rest(t0)
                        continue

                    rgb = _to_rgb(frame_bgr)
                    h, w = rgb.shape[:2]

                    if not self._lib_cache:
                        self._set_status("No faces in database", "warn")
                        self._sleep_rest(t0)
                        continue

                    boxes = self._detect_faces_using_mtcnn(rgb)
                    if not boxes:
                        self._reset_hits()
                        self._on_visual(None)
                        self._set_status("No face detected", "none")
                        self._sleep_rest(t0)
                        continue

                    best = self._choose_best_box(boxes, w, h)
                    if best is None:
                        self._reset_hits()
                        self._on_visual(None)
                        self._set_status("No face detected", "none")
                        self._sleep_rest(t0)
                        continue

                    x1, y1, bw, bh = int(best["x1"]), int(best["y1"]), int(best["w"]), int(best["h"])
                    pad = int(0.12 * max(bw, bh))
                    xa, ya = max(0, x1 - pad), max(0, y1 - pad)
                    xb, yb = min(w, x1 + bw + pad), min(h, y1 + bh + pad)

                    crop_bgr = frame_bgr[ya:yb, xa:xb]

                    # ✅ GUARD: crop rỗng => bỏ qua (tránh TF shape [0,...])
                    if crop_bgr is None or crop_bgr.size == 0:
                        self._reset_hits()
                        self._on_visual(None)
                        self._set_status("No face detected", "none")
                        self._sleep_rest(t0)
                        continue

                    # sharpness
                    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
                    if _var_laplacian(gray) < self._blur_thr:
                        self._reset_hits()
                        self._on_visual({"box": (xa, ya, xb, yb),
                                        "label": "Unknown",
                                        "color": (60, 180, 255),
                                        "ts": time.time()})
                        self._set_status("Face detected but not recognized", "warn")
                        self._sleep_rest(t0)
                        continue

                    emb = self._embed_crop(crop_bgr)
                    if emb is None:
                        self._reset_hits()
                        self._on_visual({"box": (xa, ya, xb, yb),
                                        "label": "Unknown",
                                        "color": (60, 180, 255),
                                        "ts": time.time()})
                        self._sleep_rest(t0)
                        continue

                    best_match = self._match_embedding(emb)
                    if best_match is None:
                        self._reset_hits()
                        self._on_visual({"box": (xa, ya, xb, yb),
                                        "label": "Unknown",
                                        "color": (60, 180, 255),
                                        "ts": time.time()})
                        self._sleep_rest(t0)
                        continue

                    eid, sid, name, s1, s2 = best_match
                    if s1 < self._threshold or (s1 - s2) < self._top2_delta:
                        self._reset_hits()
                        self._on_visual({"box": (xa, ya, xb, yb),
                                        "label": "Unknown",
                                        "color": (60, 180, 255),
                                        "ts": time.time()})
                        self._sleep_rest(t0)
                        continue

                    # streak
                    with self._state_lock:
                        if self._last_hit_id == eid:
                            self._consecutive_hits += 1
                        else:
                            self._consecutive_hits = 1
                            self._last_hit_id = eid
                        streak_now = self._consecutive_hits

                    clean_name = _ascii_no_diacritics(name)
                    label_text = f"{sid} - {clean_name}"

                    if streak_now >= 2:
                        self._on_visual({"box": (xa, ya, xb, yb),
                                        "label": label_text,
                                        "color": (80, 220, 100),
                                        "ts": time.time()})
                        self._set_status(f"✅ Recognized: {sid} — {clean_name}", "ok")

                        if streak_now == 2:
                            try:
                                self._on_hit(eid, sid, name)
                            except Exception:
                                pass
                            finally:
                                # ✅ PAUSE sau khi đã gửi kết quả để tránh spam
                                with self._state_lock:
                                    self._armed_ts = 0.0
                                    self._paused = True
                                    self._paused_notified = False
                    else:
                        self._on_visual({"box": (xa, ya, xb, yb),
                                        "label": "Verifying…",
                                        "color": (0, 255, 255),
                                        "ts": time.time()})
                        self._set_status("Verifying match…", "warn")

                except Exception as e:
                    self._set_status(f"Recognition error: {e}", "warn")

                self._sleep_rest(t0)

        finally:
            for p in (getattr(self, "_tmp_crop_path", None), getattr(self, "_tmp_img_path", None)):
                if p and os.path.isfile(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    # ---------- pacing ----------
    def _sleep_rest(self, t0: float):
        dt = time.time() - t0
        remain = max(0.0, self._period - dt)
        self._stop_event.wait(remain)

    def _set_status(self, msg: str, level: str):
        # tránh spam UI
        key = (msg, level)
        if key == self._last_status_key:
            return
        self._last_status_key = key
        try:
            self._on_status(msg, level)
        except Exception:
            pass

