import psycopg2
from psycopg2 import pool
from config import DATABASE_URL, get_logger

log = get_logger("db")

_pool: pool.ThreadedConnectionPool | None = None


def get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        log.info("Creating DB connection pool")
        _pool = pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
    return _pool


def get_conn():
    return get_pool().getconn()


def put_conn(conn):
    get_pool().putconn(conn)


def execute(query: str, params=None, fetch=False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch:
                rows = cur.fetchall()
                conn.commit()
                return rows
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def execute_one(query: str, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            conn.commit()
            return row
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def is_healthy() -> bool:
    try:
        row = execute_one("SELECT 1")
        return row is not None and row[0] == 1
    except Exception as e:
        log.error("DB health check failed: %s", e)
        return False
