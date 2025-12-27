-- =========================================================
-- Tạo database điểm danh
-- =========================================================
CREATE DATABASE IF NOT EXISTS attendance_db
  /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci */
  /*!80016 DEFAULT ENCRYPTION='N' */
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Sử dụng database vừa tạo
USE attendance_db;

-- =========================================================
-- Bảng employees: lưu thông tin nhân viên / sinh viên
-- =========================================================
CREATE TABLE IF NOT EXISTS employees (
   employee_id bigint NOT NULL AUTO_INCREMENT,     -- Khóa chính, ID nội bộ tự tăng
   student_id int unsigned NOT NULL,               -- Mã sinh viên (duy nhất, không âm)
   full_name varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL, 
                                                   -- Họ và tên đầy đủ
   email varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL, 
                                                   -- Email (có thể để trống)
   phone varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL, 
                                                   -- Số điện thoại (tùy chọn)
   hire_date date DEFAULT NULL,                    -- Ngày bắt đầu học/làm việc
   end_date date DEFAULT NULL,                     -- Ngày kết thúc (nếu có)
   active tinyint(1) NOT NULL DEFAULT '1',         -- Trạng thái: 1 = đang hoạt động, 0 = nghỉ
   PRIMARY KEY (employee_id),                      -- Định nghĩa khóa chính
   UNIQUE KEY student_id (student_id),             -- Đảm bảo student_id không trùng
   KEY idx_emp_active (active),                    -- Index cho truy vấn theo trạng thái
   KEY idx_emp_name (full_name),                   -- Index tìm kiếm theo tên
   KEY idx_emp_dates (hire_date,end_date)          -- Index theo khoảng thời gian
 ) ENGINE=InnoDB 
   DEFAULT CHARSET=utf8mb4 
   COLLATE=utf8mb4_unicode_ci;

-- =========================================================
-- Bảng attendance_logs: lưu lịch sử điểm danh
-- =========================================================
CREATE TABLE IF NOT EXISTS attendance_logs (
   log_id bigint NOT NULL AUTO_INCREMENT,           -- Khóa chính log điểm danh
   employee_id bigint NOT NULL,                     -- Khóa ngoại liên kết employees
   detected_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, 
                                                    -- Thời điểm hệ thống ghi nhận điểm danh
   detected_date date GENERATED ALWAYS AS (CAST(detected_at AS date)) STORED, 
                                                    -- Ngày điểm danh (tự sinh từ detected_at)
   PRIMARY KEY (log_id),                            -- Khóa chính
   KEY idx_logs_detected_date (detected_date),     -- Index thống kê theo ngày
   KEY idx_logs_emp_detected_date (employee_id,detected_date), 
                                                    -- Index thống kê theo nhân viên + ngày
   CONSTRAINT attendance_logs_ibfk_1 
     FOREIGN KEY (employee_id) 
     REFERENCES employees (employee_id) 
     ON DELETE CASCADE                              -- Xóa nhân viên thì xóa log liên quan
 ) ENGINE=InnoDB 
   DEFAULT CHARSET=utf8mb4 
   COLLATE=utf8mb4_unicode_ci;

-- =========================================================
-- Bảng faces: lưu thông tin khuôn mặt (ảnh) của mỗi người
-- =========================================================
CREATE TABLE IF NOT EXISTS faces (
   face_id bigint NOT NULL AUTO_INCREMENT,          -- Khóa chính ảnh khuôn mặt
   employee_id bigint NOT NULL,                     -- Khóa ngoại liên kết employees
   image_path varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL, 
                                                    -- Đường dẫn file ảnh khuôn mặt
   PRIMARY KEY (face_id),                           -- Khóa chính
   UNIQUE KEY employee_id (employee_id),            -- Mỗi nhân viên chỉ có 1 ảnh
   CONSTRAINT faces_ibfk_1 
     FOREIGN KEY (employee_id) 
     REFERENCES employees (employee_id) 
     ON DELETE CASCADE                              -- Xóa nhân viên thì xóa ảnh tương ứng
 ) ENGINE=InnoDB 
   DEFAULT CHARSET=utf8mb4 
   COLLATE=utf8mb4_unicode_ci;
