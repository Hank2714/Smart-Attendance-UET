#db/attendance_dal.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import date, timedelta
from .db_conn import fetch_all, fetch_one, execute
from datetime import date, datetime, time as dtime
import calendar


# =========================================================
# ============ Employees / Faces (giữ nguyên) =============
# =========================================================
def add_employee(
    student_id: int,
    full_name: str,
    email: str | None = None,
    phone: str | None = None,
    hire_date: date | None = None,
) -> int:
    if hire_date is None:
        return execute(
            "INSERT INTO employees(student_id, full_name, email, phone) VALUES (%s, %s, %s, %s)",
            (student_id, full_name, email, phone),
        )

    return execute(
        "INSERT INTO employees(student_id, full_name, email, phone, hire_date) VALUES (%s, %s, %s, %s, %s)",
        (student_id, full_name, email, phone, hire_date),
    )

def list_employees(active_only: bool = True)->List[Dict[str,Any]]:
    if active_only:
        sql = ("SELECT employee_id, student_id, full_name, email, phone, hire_date, end_date, active "
               "FROM employees WHERE COALESCE(active,1)=1 "
               "ORDER BY employee_id ASC")
        return fetch_all(sql, ())
    else:
        sql = ("SELECT employee_id, student_id, full_name, email, phone, hire_date, end_date, active "
               "FROM employees ORDER BY employee_id ASC")
        return fetch_all(sql, ())

def get_face(employee_id:int)->Optional[Dict[str,Any]]:
    row = fetch_one("SELECT * FROM faces WHERE employee_id=%s", (employee_id,))
    if not row:
        return None
    return {"image_path": row["image_path"]}

def delete_face_row(employee_id: int):
    execute("DELETE FROM faces WHERE employee_id=%s", (employee_id,))

def upsert_face(employee_id:int, image_path:str)->int:
    last_id = execute(
        "INSERT INTO faces(employee_id,image_path) VALUES(%s,%s) "
        "ON DUPLICATE KEY UPDATE image_path=VALUES(image_path)",
        (employee_id, image_path)
    )
    if last_id:
        return last_id
    row = fetch_one("SELECT face_id FROM faces WHERE employee_id=%s", (employee_id,))
    return row['face_id'] if row else 0

def deactivate_employee(employee_id: int, end_date=None):
    """
    Soft delete: set end_date (mặc định = hôm nay) + active=0.
    Giữ lại logs để phục vụ thống kê.
    """
    if end_date is None:
        execute("UPDATE employees SET end_date = CURDATE(), active=0 WHERE employee_id=%s",
                (employee_id,))
    else:
        execute("UPDATE employees SET end_date=%s, active=0 WHERE employee_id=%s",
                (end_date, employee_id))

def search_employees(q:str, status:str="all"):
    like = f"%{q}%"
    sql = ("SELECT * FROM employees WHERE (full_name LIKE %s OR CAST(student_id AS CHAR) LIKE %s)")
    if status == "active":
        sql += " AND COALESCE(active,1)=1"
    elif status == "inactive":
        sql += " AND COALESCE(active,1)=0"
    sql += " ORDER BY employee_id ASC"
    return fetch_all(sql, (like, like))

# =========================================================
# =============== Logs: các hàm hiện có ===================
# =========================================================
def list_logs_by_date(d)->List[Dict[str,Any]]:
    """
    d: datetime.date hoặc 'YYYY-MM-DD'
    Trả về log trong ngày d, kèm student_id để UI Raw logs hiển thị đầy đủ.
    """
    if isinstance(d, date):
        date_str = d.isoformat()
    else:
        date_str = str(d)

    return fetch_all(
        "SELECT a.log_id, a.employee_id, e.student_id, e.full_name, a.detected_at "
        "FROM attendance_logs a "
        "JOIN employees e ON e.employee_id = a.employee_id "
        "WHERE DATE(a.detected_at) = %s "
        "ORDER BY a.detected_at ASC",
        (date_str,)
    )

# === NEW: có cờ in_shift để UI lọc/tô màu =================
def list_logs_by_date_with_flag(d) -> List[Dict[str, Any]]:
    """
    Trả về log trong 1 ngày và cờ in_shift:
    in_shift = 1 nếu TIME(detected_at) trong 07:00–17:00; ngược lại = 0
    """
    return fetch_all(
        "SELECT a.log_id, a.employee_id, e.student_id, e.full_name, a.detected_at, "
        "CASE WHEN TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' THEN 1 ELSE 0 END AS in_shift "
        "FROM attendance_logs a "
        "JOIN employees e ON e.employee_id=a.employee_id "
        "WHERE DATE(a.detected_at)=%s "
        "ORDER BY a.detected_at ASC",
        (d,)
    )

def today_summary(date_str:str)->List[Dict[str,Any]]:
    return fetch_all(
        "SELECT e.employee_id, e.full_name, "
        "MIN(a.detected_at) AS first_seen, MAX(a.detected_at) AS last_seen "
        "FROM employees e LEFT JOIN attendance_logs a "
        " ON e.employee_id=a.employee_id AND DATE(a.detected_at)=%s "
        "GROUP BY e.employee_id, e.full_name ORDER BY e.employee_id",
        (date_str,)
    )

def logs_by_employee_month(employee_id:int, year:int, month:int):
    return fetch_all(
        "SELECT a.*, e.full_name FROM attendance_logs a "
        "JOIN employees e ON e.employee_id=a.employee_id "
        "WHERE a.employee_id=%s AND YEAR(a.detected_at)=%s AND MONTH(a.detected_at)=%s "
        "ORDER BY a.detected_at ASC",
        (employee_id, year, month)
    )

def monthly_summary(year:int, month:int):
    sql = """SELECT e.employee_id, e.full_name, DATE(a.detected_at) AS date,
        MIN(a.detected_at) AS first_seen, MAX(a.detected_at) AS last_seen,
        COUNT(a.log_id) AS total_logs
        FROM employees e
        LEFT JOIN attendance_logs a ON e.employee_id=a.employee_id
          AND YEAR(a.detected_at)=%s AND MONTH(a.detected_at)=%s
        GROUP BY e.employee_id, e.full_name, DATE(a.detected_at)
        ORDER BY e.employee_id, DATE(a.detected_at);"""
    return fetch_all(sql, (year, month))

# === New: Ghi log nhận diện với cooldown chống trùng ===
def insert_attendance_log(employee_id: int) -> int:
    """Ghi 1 log thẳng, trả về last insert id."""
    return execute(
        "INSERT INTO attendance_logs(employee_id) VALUES (%s)",
        (employee_id,)
    )

# =========================================================
# ================== Quick stats (giữ nguyên) =============
# =========================================================
def count_employees(active_only: bool = True) -> int:
    if active_only:
        row = fetch_one("SELECT COUNT(*) AS c FROM employees WHERE active=1")
    else:
        row = fetch_one("SELECT COUNT(*) AS c FROM employees")
    return row["c"] if row else 0

def count_faces():
    row = fetch_one("SELECT COUNT(*) AS c FROM faces")
    return row["c"] if row else 0

def count_logs_on_date(d: str):
    row = fetch_one("SELECT COUNT(*) AS c FROM attendance_logs WHERE DATE(detected_at)=%s", (d,))
    return row["c"] if row else 0

def count_logs_in_month(y: int, m: int):
    row = fetch_one(
        "SELECT COUNT(*) AS c FROM attendance_logs "
        "WHERE YEAR(detected_at)=%s AND MONTH(detected_at)=%s",
        (y, m)
    )
    return row["c"] if row else 0

# =========================================================
# === present_counts_by_day: chỉnh theo ca 07:00–17:00 ====
# =========================================================
def present_counts_by_day(y: int, m: int):
    """
    Đếm DISTINCT employee có log trong khung 07:00–17:00 từng ngày của tháng (local-time).
    """
    sql = (
        "WITH RECURSIVE days AS ( "
        "  SELECT DATE(CONCAT(%s,'-',LPAD(%s,2,'0'),'-01')) AS d "
        "  UNION ALL "
        "  SELECT d + INTERVAL 1 DAY FROM days "
        "  WHERE MONTH(d + INTERVAL 1 DAY) = %s AND YEAR(d + INTERVAL 1 DAY) = %s "
        ") "
        "SELECT days.d AS date, COUNT(DISTINCT a.employee_id) AS present_count "
        "FROM days "
        "LEFT JOIN attendance_logs a "
        "  ON DATE(a.detected_at)=days.d "
        " AND TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "GROUP BY days.d "
        "ORDER BY days.d"
    )
    return fetch_all(sql, (y, m, m, y))

# =========================================================
# ===== New: APIs dùng cho Attendance tabs (UI mới) =======
# =========================================================

def get_daily_stack(d1: date, d2: date) -> List[Dict[str, Any]]:
    """
    Trả về danh sách cho range [d1..d2]:
      - day (DATE)
      - total_active: số nhân viên ACTIVE-TRONG-NGÀY
      - present: có >=1 log trong ca 07:00–17:00 ở day
      - absent: total_active - present
    """
    if d2 < d1:
        d1, d2 = d2, d1

    sql = (
        "WITH RECURSIVE days AS ( "
        "  SELECT CAST(%s AS DATE) AS d "
        "  UNION ALL "
        "  SELECT d + INTERVAL 1 DAY FROM days WHERE d < %s "
        "), "
        "p AS ( "
        "  SELECT DATE(a.detected_at) AS d, a.employee_id "
        "  FROM attendance_logs a "
        "  JOIN employees e ON e.employee_id = a.employee_id "
        "  WHERE TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "  GROUP BY DATE(a.detected_at), a.employee_id "
        ") "
        "SELECT "
        "  days.d AS day, "
        "  COUNT(e.employee_id) AS total_active, "
        "  COUNT(DISTINCT p2.employee_id) AS present, "
        "  (COUNT(e.employee_id) - COUNT(DISTINCT p2.employee_id)) AS absent "
        "FROM days "
        "LEFT JOIN employees e "
        "  ON e.hire_date <= days.d "
        " AND (e.end_date IS NULL OR e.end_date >= days.d) "
        "LEFT JOIN p p2 "
        "  ON p2.d = days.d "
        " AND p2.employee_id = e.employee_id "
        "GROUP BY days.d "
        "ORDER BY days.d"
    )
    return fetch_all(sql, (d1, d2))


def get_day_rosters(d: date) -> Dict[str, List[Dict[str, Any]]]:
    """
    Cho một ngày d, trả về:
      - present: list {employee_id, student_id, full_name} đã có log trong 07:00–17:00
      - absent : list {employee_id, student_id, full_name} ACTIVE-TRONG-NGÀY nhưng KHÔNG có log trong ca
    """
    # Present
    present_sql = (
        "SELECT DISTINCT e.employee_id, e.student_id, e.full_name "
        "FROM employees e "
        "JOIN attendance_logs a ON a.employee_id = e.employee_id "
        "WHERE DATE(a.detected_at)=%s "
        "  AND TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "  AND e.hire_date <= %s "
        "  AND (e.end_date IS NULL OR e.end_date >= %s) "
        "ORDER BY e.employee_id"
    )
    present = fetch_all(present_sql, (d, d, d))

    # Absent = Active-on-day but no present log
    absent_sql = (
        "SELECT e.employee_id, e.student_id, e.full_name "
        "FROM employees e "
        "WHERE e.hire_date <= %s "
        "  AND (e.end_date IS NULL OR e.end_date >= %s) "
        "  AND NOT EXISTS ( "
        "        SELECT 1 FROM attendance_logs a "
        "        WHERE a.employee_id = e.employee_id "
        "          AND DATE(a.detected_at)=%s "
        "          AND TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "  ) "
        "ORDER BY e.employee_id"
    )
    absent = fetch_all(absent_sql, (d, d, d))

    return {"present": present, "absent": absent}

def count_day(d: date) -> Dict[str, int]:
    """
    Trả về dict: { total_active, present, absent } cho ngày d.
    """
    # total_active
    row = fetch_one(
        "SELECT COUNT(*) AS c FROM employees "
        "WHERE hire_date <= %s AND (end_date IS NULL OR end_date >= %s)",
        (d, d)
    )
    total_active = row["c"] if row else 0

    # present
    row2 = fetch_one(
        "SELECT COUNT(DISTINCT a.employee_id) AS c "
        "FROM attendance_logs a "
        "JOIN employees e ON e.employee_id = a.employee_id "
        "WHERE DATE(a.detected_at)=%s "
        "  AND TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "  AND e.hire_date <= %s "
        "  AND (e.end_date IS NULL OR e.end_date >= %s)",
        (d, d, d)
    )
    present = row2["c"] if row2 else 0

    return {"total_active": total_active, "present": present, "absent": max(total_active - present, 0)}


###########################################################################################################
# cấu hình ca làm
_SHIFT_START   = "07:00:00"
_SHIFT_END     = "17:00:00"
_LATE_AFTER    = "08:00:00"

def get_day_checkio(d: date) -> List[Dict[str, Any]]:
    """
    Trả về 1 dòng / nhân viên ACTIVE-TRONG-NGÀY:
      employee_id, student_id, full_name,
      checkin (datetime|None), checkout (datetime|None),
      is_late (0/1), duration_minutes (int, nếu đủ in/out)
    """
    sql = f"""
        SELECT
          e.employee_id,
          e.student_id,
          e.full_name,
          MIN(CASE
                WHEN TIME(a.detected_at) BETWEEN '{_SHIFT_START}' AND '{_SHIFT_END}'
                THEN a.detected_at END) AS checkin,
          MAX(CASE
                WHEN TIME(a.detected_at) BETWEEN '{_SHIFT_START}' AND '{_SHIFT_END}'
                THEN a.detected_at END) AS checkout,
          CASE
            WHEN MIN(CASE
                       WHEN TIME(a.detected_at) BETWEEN '{_SHIFT_START}' AND '{_SHIFT_END}'
                       THEN a.detected_at END) IS NOT NULL
                 AND TIME(MIN(CASE
                                WHEN TIME(a.detected_at) BETWEEN '{_SHIFT_START}' AND '{_SHIFT_END}'
                                THEN a.detected_at END)) > '{_LATE_AFTER}'
              THEN 1 ELSE 0
          END AS is_late
        FROM employees e
        LEFT JOIN attendance_logs a
               ON a.employee_id = e.employee_id
              AND a.detected_date = %s
        WHERE e.hire_date <= %s
          AND (e.end_date IS NULL OR e.end_date >= %s)
        GROUP BY e.employee_id, e.student_id, e.full_name
        ORDER BY e.employee_id
    """
    rows = fetch_all(sql, (d, d, d)) or []
    # tính duration phút nếu đủ in/out
    for r in rows:
        ci = r.get("checkin")
        co = r.get("checkout")
        if ci and co:
            diff = co - ci
            r["duration_minutes"] = int(diff.total_seconds() // 60)
        else:
            r["duration_minutes"] = None
    return rows

def get_range_checkio(d1: date, d2: date) -> List[Dict[str, Any]]:
    """
    Range [d1..d2]: trả về nhiều dòng (1 dòng/nhân viên/ngày)
      day, employee_id, student_id, full_name, checkin, checkout, is_late, duration_minutes
    """
    if d2 < d1:
        d1, d2 = d2, d1
    sql = f"""
        WITH RECURSIVE days AS (
          SELECT CAST(%s AS DATE) AS d
          UNION ALL
          SELECT d + INTERVAL 1 DAY FROM days WHERE d < %s
        )
        SELECT
          days.d AS day,
          e.employee_id, e.student_id, e.full_name,
          MIN(CASE
                WHEN TIME(a.detected_at) BETWEEN '{_SHIFT_START}' AND '{_SHIFT_END}'
                THEN a.detected_at END) AS checkin,
          MAX(CASE
                WHEN TIME(a.detected_at) BETWEEN '{_SHIFT_START}' AND '{_SHIFT_END}'
                THEN a.detected_at END) AS checkout,
          CASE
            WHEN MIN(CASE
                       WHEN TIME(a.detected_at) BETWEEN '{_SHIFT_START}' AND '{_SHIFT_END}'
                       THEN a.detected_at END) IS NOT NULL
                 AND TIME(MIN(CASE
                                WHEN TIME(a.detected_at) BETWEEN '{_SHIFT_START}' AND '{_SHIFT_END}'
                                THEN a.detected_at END)) > '{_LATE_AFTER}'
              THEN 1 ELSE 0
          END AS is_late
        FROM days
        JOIN employees e
          ON e.hire_date <= days.d
         AND (e.end_date IS NULL OR e.end_date >= days.d)
        LEFT JOIN attendance_logs a
               ON a.employee_id = e.employee_id
              AND a.detected_date = days.d
        GROUP BY days.d, e.employee_id, e.student_id, e.full_name
        ORDER BY days.d, e.employee_id
    """
    rows = fetch_all(sql, (d1, d2)) or []
    for r in rows:
        ci = r.get("checkin")
        co = r.get("checkout")
        r["duration_minutes"] = int((co - ci).total_seconds() // 60) if ci and co else None
    return rows

# === NEW: Daily stack with LATE count (checkin > 08:00) ======================
def get_daily_stack_plus(d1: date, d2: date):
    """
    Range [d1..d2]:
      - day
      - total_active: số nhân viên ACTIVE-TRONG-NGÀY
      - present: có >=1 log trong 07:00–17:00
      - late: số nhân viên có first_seen > 08:00
      - absent = total_active - present
    """
    if d2 < d1:
        d1, d2 = d2, d1

    sql = (
        "WITH RECURSIVE days AS ( "
        "  SELECT CAST(%s AS DATE) AS d "
        "  UNION ALL "
        "  SELECT d + INTERVAL 1 DAY FROM days WHERE d < %s "
        "), "
        "firsts AS ( "
        "  SELECT DATE(a.detected_at) AS d, a.employee_id, MIN(a.detected_at) AS first_seen "
        "  FROM attendance_logs a "
        "  JOIN employees e ON e.employee_id = a.employee_id "
        "  WHERE TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "  GROUP BY DATE(a.detected_at), a.employee_id "
        ") "
        "SELECT "
        "  days.d AS day, "
        "  COUNT(e.employee_id) AS total_active, "
        "  COUNT(firsts.employee_id) AS present, "
        "  SUM(CASE WHEN firsts.first_seen IS NOT NULL AND TIME(firsts.first_seen) > '08:00:00' THEN 1 ELSE 0 END) AS late, "
        "  (COUNT(e.employee_id) - COUNT(firsts.employee_id)) AS absent "
        "FROM days "
        "LEFT JOIN employees e "
        "  ON e.hire_date <= days.d "
        " AND (e.end_date IS NULL OR e.end_date >= days.d) "
        "LEFT JOIN firsts "
        "  ON firsts.d = days.d "
        " AND firsts.employee_id = e.employee_id "
        "GROUP BY days.d "
        "ORDER BY days.d"
    )
    return fetch_all(sql, (d1, d2))


# === NEW: By-Day roster with LATE list =======================================
def get_day_rosters_plus(d: date):
    """
    Trả về:
      - present: {employee_id, student_id, full_name}
      - absent : {employee_id, student_id, full_name}
      - late   : {employee_id, student_id, full_name, checkin} với checkin > 08:00
    """
    # Present (distinct by emp in 07:00–17:00)
    present_sql = (
        "SELECT DISTINCT e.employee_id, e.student_id, e.full_name "
        "FROM employees e "
        "JOIN attendance_logs a ON a.employee_id = e.employee_id "
        "WHERE DATE(a.detected_at)=%s "
        "  AND TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "  AND e.hire_date <= %s "
        "  AND (e.end_date IS NULL OR e.end_date >= %s) "
        "ORDER BY e.employee_id"
    )
    present = fetch_all(present_sql, (d, d, d))

    # Absent (active-on-day but no log in shift)
    absent_sql = (
        "SELECT e.employee_id, e.student_id, e.full_name "
        "FROM employees e "
        "WHERE e.hire_date <= %s "
        "  AND (e.end_date IS NULL OR e.end_date >= %s) "
        "  AND NOT EXISTS ( "
        "        SELECT 1 FROM attendance_logs a "
        "        WHERE a.employee_id = e.employee_id "
        "          AND DATE(a.detected_at)=%s "
        "          AND TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "  ) "
        "ORDER BY e.employee_id"
    )
    absent = fetch_all(absent_sql, (d, d, d))

    # Late arrivals (first_seen > 08:00)
    late_sql = (
        "SELECT e.employee_id, e.student_id, e.full_name, MIN(a.detected_at) AS checkin "
        "FROM employees e "
        "JOIN attendance_logs a ON a.employee_id = e.employee_id "
        "WHERE DATE(a.detected_at)=%s "
        "  AND TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00' "
        "  AND e.hire_date <= %s "
        "  AND (e.end_date IS NULL OR e.end_date >= %s) "
        "GROUP BY e.employee_id, e.student_id, e.full_name "
        "HAVING TIME(checkin) > '08:00:00' "
        "ORDER BY e.employee_id"
    )
    late = fetch_all(late_sql, (d, d, d))

    return {"present": present, "absent": absent, "late": late}

def get_day_rosters_inout(day: date) -> Dict[str, List[dict]]:
    """
    Roster theo ngày (có check_in/check_out):
    - Lấy toàn bộ nhân viên "thuộc biên chế" trong ngày đó (hire_date <= day <= end_date/NULL)
    - LEFT JOIN logs theo detected_date = day để lấy:
        check_in  = MIN(detected_at)
        check_out = MAX(detected_at) (để trống nếu chỉ có 1 log)
        log_count
    - Phân loại:
        present: có log và check_in trong ca 07:00–17:00
        late:    subset của present với check_in > 08:00
        absent:  không có log (log_count = 0)
    """

    # Ca làm việc bạn đang dùng ở UI
    SHIFT_START = dtime(7, 0, 0)
    SHIFT_END   = dtime(17, 0, 0)
    LATE_AFTER  = dtime(8, 0, 0)

    sql = """
    SELECT
        e.employee_id,
        e.student_id,
        e.full_name,
        e.active,
        e.hire_date,
        e.end_date,
        MIN(l.detected_at) AS check_in,
        MAX(l.detected_at) AS check_out,
        COUNT(l.log_id)    AS log_count
    FROM employees e
    LEFT JOIN attendance_logs l
        ON l.employee_id = e.employee_id
       AND l.detected_date = %s
    WHERE
        e.hire_date <= %s
        AND (e.end_date IS NULL OR e.end_date >= %s)
        AND e.active IN (0,1)
    GROUP BY
        e.employee_id, e.student_id, e.full_name, e.active, e.hire_date, e.end_date
    ORDER BY
        e.student_id ASC;
    """

    rows = fetch_all(sql, (day, day, day)) or []

    def _to_dt(v) -> Optional[datetime]:
        # db layer đôi lúc trả string, đôi lúc trả datetime
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            # MySQL thường: "YYYY-mm-dd HH:MM:SS"
            try:
                return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    # fallback có microseconds
                    return datetime.fromisoformat(v)
                except Exception:
                    return None
        return None

    def _fmt(v: Optional[datetime]) -> str:
        return "" if not v else v.strftime("%Y-%m-%d %H:%M:%S")

    present: List[dict] = []
    late: List[dict] = []
    absent: List[dict] = []

    for r in rows:
        cin = _to_dt(r.get("check_in"))
        cout = _to_dt(r.get("check_out"))
        cnt = int(r.get("log_count") or 0)

        # Nếu chỉ có 1 log -> check_out để trống
        if cnt <= 1:
            cout = None

        # Chuẩn hoá string để Treeview/CSV đẹp
        r["check_in"] = _fmt(cin)
        r["check_out"] = _fmt(cout)
        r["log_count"] = cnt

        if cnt <= 0 or cin is None:
            absent.append(r)
            continue

        t = cin.time()
        in_shift = (SHIFT_START <= t <= SHIFT_END)

        if in_shift:
            present.append(r)
            if t > LATE_AFTER:
                late.append(r)
        else:
            # Có log nhưng ngoài ca: tuỳ bạn muốn xử lý.
            # Mặc định: vẫn coi là present để không "mất" người
            # (nếu muốn strict ca 07-17 thì comment 2 dòng dưới và cho vào absent)
            present.append(r)

    return {"present": present, "absent": absent, "late": late}

def search_logs_by_employee(
    q: str,
    date_from=None,
    date_to=None
):
    """
    q: student_id hoặc full_name (LIKE)
    """
    like = f"%{q}%"

    sql = (
        "SELECT a.log_id, a.employee_id, e.student_id, e.full_name, a.detected_at "
        "FROM attendance_logs a "
        "JOIN employees e ON e.employee_id = a.employee_id "
        "WHERE (CAST(e.student_id AS CHAR) LIKE %s OR e.full_name LIKE %s)"
    )
    params = [like, like]

    if date_from:
        sql += " AND DATE(a.detected_at) >= %s"
        params.append(date_from)

    if date_to:
        sql += " AND DATE(a.detected_at) <= %s"
        params.append(date_to)

    sql += " ORDER BY a.detected_at DESC"

    return fetch_all(sql, tuple(params))

def get_monthly_employee_summary(employee_id: int, year: int, month: int) -> dict:
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    emp = get_employee_dates(employee_id)
    hire = emp.get("hire_date")
    end  = emp.get("end_date")

    if not hire:
        return {"present": 0, "late": 0, "absent": 0}

    # effective range của nhân viên trong tháng
    start = max(first_day, hire)
    stop  = min(last_day, end) if end else last_day

    today = date.today()
    if (year, month) == (today.year, today.month):
        stop = min(stop, today)

    if stop < start:
        return {"present": 0, "late": 0, "absent": 0}

    sql = """
    SELECT DATE(a.detected_at) AS d,
           MIN(a.detected_at) AS first_seen
    FROM attendance_logs a
    WHERE a.employee_id = %s
      AND DATE(a.detected_at) BETWEEN %s AND %s
      AND TIME(a.detected_at) BETWEEN '07:00:00' AND '17:00:00'
    GROUP BY DATE(a.detected_at)
    """
    rows = fetch_all(sql, (employee_id, start, stop)) or []
    first_map = {r["d"]: r["first_seen"] for r in rows}

    present = late = absent = 0
    d = start

    while d <= stop:
        if d not in first_map:
            absent += 1
        else:
            if first_map[d].time() > dtime(8, 0, 0):
                late += 1
            else:
                present += 1
        d += timedelta(days=1)

    return {
        "present": present,
        "late": late,
        "absent": absent
    }


def get_employee_dates(employee_id: int) -> dict:
    """
    Trả về { hire_date: date, end_date: date|None }
    """
    row = fetch_one(
        "SELECT hire_date, end_date FROM employees WHERE employee_id=%s",
        (employee_id,)
    )
    if not row:
        return {"hire_date": None, "end_date": None}
    return {
        "hire_date": row["hire_date"],
        "end_date": row["end_date"]
    }