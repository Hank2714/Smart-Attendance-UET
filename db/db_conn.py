import os
from contextlib import contextmanager
from dotenv import load_dotenv, find_dotenv
import mysql.connector

load_dotenv(find_dotenv())

def _config():
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASS", ""),
        "database": os.getenv("DB_NAME", "attendance_db"),
    }

@contextmanager
def get_conn():
    cn = mysql.connector.connect(**_config())
    try:
        yield cn
        cn.commit()
    except:
        cn.rollback()
        raise
    finally:
        cn.close()

def fetch_all(sql, params=None):
    with get_conn() as cn:
        cur = cn.cursor(dictionary=True)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return rows

def fetch_one(sql, params=None):
    with get_conn() as cn:
        cur = cn.cursor(dictionary=True)
        cur.execute(sql, params or ())
        row = cur.fetchone()
        cur.close()
        return row

def execute(sql, params=None):
    with get_conn() as cn:
        cur = cn.cursor()
        cur.execute(sql, params or ())
        last_id = cur.lastrowid
        cur.close()
        return last_id
