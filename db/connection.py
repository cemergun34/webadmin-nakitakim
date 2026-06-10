# -*- coding: utf-8 -*-
"""
PostgreSQL Bağlantı Yöneticisi
================================
Thread-safe bağlantı havuzu.
Servis kodları: conn = get_connection() → conn.execute(...) → conn.close()
"""
from __future__ import annotations

import threading
import logging

logger = logging.getLogger(__name__)

_pg_local = threading.local()


def _try_pg_connect(params: dict):
    import psycopg2
    try:
        raw = psycopg2.connect(**params)
        raw.autocommit = False
        return raw
    except psycopg2.OperationalError as exc:
        raise RuntimeError(f"PostgreSQL bağlantı hatası: {exc}") from exc


class _CIRow(dict):
    """Büyük/küçük harf duyarsız satır sarmalayıcı (sqlite3 uyumlu)."""
    def __init__(self, row):
        super().__init__(row)
        self._keys_list = list(row.keys())
        self._lower_map = {k.lower(): k for k in self._keys_list}

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._keys_list[key])
        try:
            return super().__getitem__(key)
        except KeyError:
            actual = self._lower_map.get(key.lower())
            if actual is not None:
                return super().__getitem__(actual)
            raise

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def __contains__(self, key):
        if isinstance(key, int):
            return 0 <= key < len(self._keys_list)
        return super().__contains__(key) or key.lower() in self._lower_map


def _wrap_row(row):
    if row is None:
        return None
    return _CIRow(dict(row))


def _to_pg_sql(sql: str) -> str:
    """sqlite3 ? → psycopg2 %s dönüşümü."""
    result = []
    in_str = False
    str_char = None
    for c in sql:
        if in_str:
            if c == '%':
                result.append('%%')
            elif c == str_char:
                in_str = False
                result.append(c)
            else:
                result.append(c)
        elif c in ("'", '"'):
            in_str = True
            str_char = c
            result.append(c)
        elif c == '?':
            result.append('%s')
        else:
            result.append(c)
    return ''.join(result)


class _PgCursor:
    def __init__(self, pg_cur):
        self._cur = pg_cur
        self.lastrowid: int | None = None
        self.rowcount: int = -1

    def fetchone(self):
        return _wrap_row(self._cur.fetchone())

    def fetchall(self):
        return [_wrap_row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    def close(self):
        self._cur.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _PgWrapper:
    """psycopg2 bağlantısını sqlite3.Connection API'siyle uyumlu hale getirir."""
    row_factory = None

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql: str, params=()):
        import psycopg2.extras
        sql = _to_pg_sql(sql)
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(sql, params or ())
        wrapper = _PgCursor(cur)
        wrapper.rowcount = cur.rowcount
        if sql.strip().upper().startswith("INSERT"):
            try:
                if "RETURNING" in sql.upper():
                    row = cur.fetchone()
                    if row:
                        wrapper.lastrowid = row[0]
                else:
                    lv = self._conn.cursor()
                    lv.execute("SELECT lastval()")
                    wrapper.lastrowid = lv.fetchone()[0]
                    lv.close()
            except Exception:
                wrapper.lastrowid = None
        return wrapper

    def executemany(self, sql: str, params_list):
        sql = _to_pg_sql(sql)
        cur = self._conn.cursor()
        cur.executemany(sql, params_list)
        cur.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass  # Bağlantı havuzda kalır

    def cursor(self):
        import psycopg2.extras
        return self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        return False


def get_connection() -> _PgWrapper:
    """
    Thread-safe PostgreSQL bağlantısı döndürür.
    Mevcut bağlantı geçerliyse yeniden kullanır.
    """
    from db.db_config import get_pg_params
    raw = getattr(_pg_local, "conn", None)
    if raw is not None and not raw.closed:
        try:
            raw.rollback()
            return _PgWrapper(raw)
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            _pg_local.conn = None

    params = get_pg_params()
    # Port fallback: 5432 → 6543 (Supabase)
    primary_port = int(params.get("port", 5432))
    fallback_ports = [primary_port]
    if primary_port == 5432:
        fallback_ports.append(6543)
    elif primary_port == 6543:
        fallback_ports.append(5432)

    last_exc = None
    for port in fallback_ports:
        attempt = dict(params)
        attempt["port"] = port
        attempt["connect_timeout"] = 10 if port == primary_port else 15
        try:
            logger.info(f"[DB] Bağlantı deneniyor: {attempt['host']}:{port}")
            conn = _try_pg_connect(attempt)
            _pg_local.conn = conn
            logger.info(f"[DB] Bağlantı başarılı ✅ port={port}")
            return _PgWrapper(conn)
        except RuntimeError as exc:
            last_exc = exc
            logger.warning(f"[DB] Port {port} başarısız: {exc}")

    raise last_exc or RuntimeError("PostgreSQL bağlantısı kurulamadı.")


def close_pg_pool():
    """Thread bağlantısını kapat."""
    raw = getattr(_pg_local, "conn", None)
    if raw:
        try:
            raw.close()
        except Exception:
            pass
        _pg_local.conn = None


def test_connection() -> dict:
    """Bağlantı testi — config sayfasından çağrılır."""
    try:
        from db.db_config import get_pg_params
        import psycopg2
        conn = psycopg2.connect(**get_pg_params())
        ver = conn.server_version
        major, minor = ver // 10000, (ver % 10000) // 100
        conn.close()
        return {"success": True, "message": f"Bağlantı başarılı! PostgreSQL {major}.{minor}"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
