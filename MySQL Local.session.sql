UPDATE employees e
JOIN (
    SELECT employee_id, MIN(DATE(detected_at)) AS first_day
    FROM attendance_logs
    GROUP BY employee_id
) x ON x.employee_id = e.employee_id
SET e.hire_date = CASE
    WHEN e.hire_date IS NULL THEN x.first_day
    WHEN e.hire_date > x.first_day THEN x.first_day
    ELSE e.hire_date
END;
