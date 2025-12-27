# hardware/protocol.py
"""
UART protocol giữa PC và ATmega16
================================

ATmega  -> PC
----------------
NG      : Sensor phát hiện có người (event trigger)
CK      : ATmega đã sẵn sàng nhận kết quả (READY_FOR_RESULT)
RD      : ATmega đã hiển thị xong, reset về IDLE (READY / back to idle)
CF      : Trả lời RUOK (module OK)

PC -> ATmega
----------------
RUOK        : Kiểm tra module
F           : Nhận diện thất bại
T<student>  : Nhận diện thành công (student_id)
"""

# ---------- ATmega -> PC ----------
AVR_PERSON_DETECTED = "NG"
AVR_CHECK_READY     = "CK"
AVR_READY           = "RD"
AVR_CONFIRM_OK      = "CF"

# ---------- PC -> ATmega ----------
PC_CHECK_SENSOR     = "RUOK"
PC_FACE_FAIL        = "F"

def pc_face_ok(student_id: str | int) -> str:
    return f"T{student_id}"
