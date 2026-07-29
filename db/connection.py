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
        connect_params = dict(params)
        # TCP keepalive: Neon/Supabase gibi bulut DB'lerde uzun
        # bekleme sonrasi baglanti kopmasin
        connect_params.setdefault('keepalives', 1)
        connect_params.setdefault('keepalives_idle', 30)
        connect_params.setdefault('keepalives_interval', 10)
        connect_params.setdefault('keepalives_count', 5)
        raw = psycopg2.connect(**connect_params)
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
        # Bağlantı havuzda kalır, havuza iade ediyoruz
        from db.connection import _pool
        if self._conn and _pool:
            try:
                _pool.putconn(self._conn)
            except Exception:
                pass
        self._conn = None

    def cursor(self):
        import psycopg2.extras
        return self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                self.rollback()
            else:
                self.commit()
        finally:
            self.close()
        return False


# Global Pool
_pool = None
_pool_lock = threading.Lock()

def _init_pool():
    global _pool
    if _pool is not None:
        return
    
    with _pool_lock:
        if _pool is not None:
            return
            
        from db.db_config import get_pg_params
        from psycopg2.pool import ThreadedConnectionPool
        
        params = get_pg_params()
        primary_port = int(params.get("port", 5432))
        attempt = dict(params)
        attempt["port"] = primary_port
        attempt.setdefault('keepalives', 1)
        attempt.setdefault('keepalives_idle', 30)
        attempt.setdefault('keepalives_interval', 10)
        attempt.setdefault('keepalives_count', 5)
        
        try:
            logger.info(f"[DB] ThreadedConnectionPool başlatılıyor: {attempt['host']}:{primary_port}")
            _pool = ThreadedConnectionPool(1, 10, **attempt)
        except Exception as exc:
            logger.warning(f"[DB] Havuz başlatılamadı (port {primary_port}): {exc}")
            # Fallback port if available
            if primary_port == 5432:
                attempt["port"] = 6543
            elif primary_port == 6543:
                attempt["port"] = 5432
            logger.info(f"[DB] Alternatif port deneniyor: {attempt['port']}")
            _pool = ThreadedConnectionPool(1, 10, **attempt)

def get_connection() -> _PgWrapper:
    """
    Thread-safe PostgreSQL bağlantısı döndürür.
    Bağlantı ThreadedConnectionPool'dan alınır.
    Kullanım sonrası conn.close() ile havuza iade edilir.
    """
    if _pool is None:
        _init_pool()
        
    try:
        raw = _pool.getconn()
        raw.autocommit = False
        return _PgWrapper(raw)
    except Exception as exc:
        raise RuntimeError(f"PostgreSQL bağlantısı kurulamadı: {exc}")


def close_pg_pool():
    """Tüm havuzu kapat (Uygulama sonlanırken çağrılabilir)."""
    global _pool
    if _pool:
        try:
            _pool.closeall()
        except Exception:
            pass
        _pool = None


def test_connection() -> dict:
    """Bağlantı testi — config sayfasından çağrılır."""
    try:
        if _pool is None:
            _init_pool()
        conn = _pool.getconn()
        ver = conn.server_version
        major, minor = ver // 10000, (ver % 10000) // 100
        _pool.putconn(conn)
        return {"success": True, "message": f"Bağlantı başarılı! PostgreSQL {major}.{minor} (Pool Aktif)"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
