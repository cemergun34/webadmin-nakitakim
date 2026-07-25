# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _pg_conn():
    from db.db_config import get_pg_params
    import psycopg2
    import psycopg2.extras
    params = get_pg_params()
    conn = psycopg2.connect(**params)
    return conn


def _ensure_sequence():
    try:
        conn = _pg_conn()
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(musterino), 1000) FROM sirket_profili")
        max_no = cur.fetchone()[0]
        cur.execute(f"""
            CREATE SEQUENCE IF NOT EXISTS musterino_seq
            START WITH {max_no + 1}
            INCREMENT BY 1
            NO MINVALUE NO MAXVALUE CACHE 1
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning("musterino_seq oluşturulamadı: %s", e)


def _next_musterino() -> int:
    _ensure_sequence()
    conn = _pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT nextval('musterino_seq')")
    val = cur.fetchone()[0]
    cur.close()
    conn.close()
    return val


def _hash_password(plain: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        import hashlib
        return hashlib.sha256(plain.encode()).hexdigest()


# ── Şirket ───────────────────────────────────────────────────────────────────

def get_all_sirketler(search: str = "") -> list[dict]:
    conn = _pg_conn()
    cur = conn.cursor()
    sql = """
        SELECT
            sp.id, sp.musterino, sp.unvan, sp.vergino,
            sp.il, sp.ilce, sp.vergidairesi, sp.adres,
            COALESCE(wc.aktif, FALSE) AS aktif,
            COALESCE(wc.webadmin_url, '') AS webadmin_url,
            COUNT(u.id) AS kullanici_sayisi
        FROM sirket_profili sp
        LEFT JOIN webadmin_sirket_config wc
               ON wc.musterino = sp.musterino
        LEFT JOIN uyelik u
               ON u.musterino = sp.musterino
        {where}
        GROUP BY sp.id, sp.musterino, sp.unvan, sp.vergino,
                 sp.il, sp.ilce, sp.vergidairesi, sp.adres,
                 wc.aktif, wc.webadmin_url
        ORDER BY sp.musterino
    """
    if search:
        where = "WHERE sp.unvan ILIKE %s OR sp.vergino ILIKE %s"
        cur.execute(sql.format(where=where), (f"%{search}%", f"%{search}%"))
    else:
        cur.execute(sql.format(where=""))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close(); conn.close()
    return rows


def get_sirket(musterino: int) -> Optional[dict]:
    conn = _pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT sp.*, wc.webadmin_url, wc.api_key, wc.aktif AS wc_aktif,
               wc.firmaadi AS wc_firmaadi, wc.id AS wc_id
        FROM sirket_profili sp
        LEFT JOIN webadmin_sirket_config wc ON wc.musterino = sp.musterino
        WHERE sp.musterino = %s
        LIMIT 1
    """, (musterino,))
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    cur.close(); conn.close()
    if row:
        return dict(zip(cols, row))
    return None


def create_sirket(data: dict) -> dict:
    try:
        musterino = _next_musterino()
        conn = _pg_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sirket_profili
                (userid, unvan, vergino, tckn, vergidairesi, adres, il, ilce, musterino)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            0,
            data.get("unvan", ""),
            data.get("vergino", ""),
            data.get("tckn", ""),
            data.get("vergidairesi", ""),
            data.get("adres", ""),
            data.get("il", ""),
            data.get("ilce", ""),
            musterino,
        ))
        webadmin_url = data.get("webadmin_url", "").strip()
        api_key      = data.get("api_key", "").strip()
        aktif        = data.get("aktif") in (True, "on", "1", "true")
        now_str      = datetime.now().isoformat()
        cur.execute("""
            INSERT INTO webadmin_sirket_config
                (userid, musterino, firmaadi, webadmin_url, api_key, aktif,
                 kayit_tarihi, guncelleme_tarihi)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (0, musterino, data.get("unvan", ""), webadmin_url, api_key,
              aktif, now_str, now_str))

        # ── Nakitakim servis tablolarına boş başlangıç satırları ────────────
        # vomsisbilgileri: Womsis API bağlantısı — boş başlangıç
        cur.execute("""
            INSERT INTO vomsisbilgileri (userid, musterino, appkey, seckey, url, kayit_tarihi, guncelleme_tarihi)
            SELECT 0, %s, '', '', 'https://developers.vomsis.com/api/v2', %s, %s
            WHERE NOT EXISTS (SELECT 1 FROM vomsisbilgileri WHERE musterino=%s)
        """, (musterino, now_str, now_str, musterino))

        # apisanalpos: PayTR API bağlantısı — boş başlangıç
        cur.execute("""
            INSERT INTO apisanalpos (userid, musterino, firma_adi, magaza_no, magaza_parola, magaza_gizli_anahtar, kayit_tarihi)
            SELECT 0, %s, %s, '', '', '', %s
            WHERE NOT EXISTS (SELECT 1 FROM apisanalpos WHERE musterino=%s)
        """, (musterino, data.get("unvan", ""), now_str, musterino))

        # moy_bilgileri: MOY muhasebe bağlantısı — boş başlangıç
        cur.execute("""
            INSERT INTO moy_bilgileri (musterino, url, username, sifre, moykayitno, tarih)
            SELECT %s, '', '', '', 0, %s
            WHERE NOT EXISTS (SELECT 1 FROM moy_bilgileri WHERE musterino=%s)
        """, (musterino, now_str, musterino))

        conn.commit()
        cur.close(); conn.close()
        return {"success": True, "musterino": musterino}
    except Exception as e:
        logger.error("create_sirket hata: %s", e)
        return {"success": False, "error": str(e)}



def update_sirket(musterino: int, data: dict) -> dict:
    try:
        conn = _pg_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE sirket_profili
               SET unvan=%s, vergino=%s, tckn=%s, vergidairesi=%s,
                   adres=%s, il=%s, ilce=%s
             WHERE musterino=%s
        """, (
            data.get("unvan", ""), data.get("vergino", ""),
            data.get("tckn", ""),  data.get("vergidairesi", ""),
            data.get("adres", ""), data.get("il", ""),
            data.get("ilce", ""),  musterino,
        ))
        aktif   = data.get("aktif") in (True, "on", "1", "true")
        now_str = datetime.now().isoformat()
        cur.execute("""
            UPDATE webadmin_sirket_config
               SET firmaadi=%s, webadmin_url=%s, api_key=%s,
                   aktif=%s, guncelleme_tarihi=%s
             WHERE musterino=%s
        """, (
            data.get("unvan", ""),
            data.get("webadmin_url", "").strip(),
            data.get("api_key", "").strip(),
            aktif, now_str, musterino,
        ))
        conn.commit()
        cur.close(); conn.close()
        return {"success": True}
    except Exception as e:
        logger.error("update_sirket hata: %s", e)
        return {"success": False, "error": str(e)}


def delete_sirket(musterino: int) -> dict:
    try:
        conn = _pg_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM uyelik WHERE musterino=%s", (musterino,))
        cur.execute("DELETE FROM webadmin_sirket_config WHERE musterino=%s", (musterino,))
        cur.execute("DELETE FROM sirket_profili WHERE musterino=%s", (musterino,))
        conn.commit()
        cur.close(); conn.close()
        return {"success": True}
    except Exception as e:
        logger.error("delete_sirket hata: %s", e)
        return {"success": False, "error": str(e)}


# ── Kullanıcı ─────────────────────────────────────────────────────────────────

def get_sirket_users(musterino: int) -> list[dict]:
    conn = _pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ad, soyad, kullanici_adi, eposta,
               yetki, paket_turu, son_odeme, hesapturu
        FROM uyelik
        WHERE musterino = %s
        ORDER BY id
    """, (musterino,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close(); conn.close()
    return rows


def get_user(user_id: int) -> Optional[dict]:
    conn = _pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ad, soyad, kullanici_adi, eposta,
               yetki, paket_turu, son_odeme, hesapturu, musterino
        FROM uyelik WHERE id=%s LIMIT 1
    """, (user_id,))
    cols = [d[0] for d in cur.description]
    row  = cur.fetchone()
    cur.close(); conn.close()
    return dict(zip(cols, row)) if row else None


def create_user(musterino: int, data: dict) -> dict:
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute("SELECT id FROM uyelik WHERE kullanici_adi=%s LIMIT 1",
                    (data.get("kullanici_adi", ""),))
        if cur.fetchone():
            cur.close(); conn.close()
            return {"success": False, "error": "Bu kullanıcı adı zaten kullanılıyor."}

        hashed  = _hash_password(data.get("sifre", ""))
        now_str = datetime.now().isoformat()
        cur.execute("""
            INSERT INTO uyelik
                (ad, soyad, kullanici_adi, eposta, sifre,
                 musterino, yetki, paket_turu, son_odeme,
                 uyelik_tarihi, hesapturu)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            data.get("ad", ""),
            data.get("soyad", ""),
            data.get("kullanici_adi", ""),
            data.get("eposta", ""),
            hashed,
            musterino,
            data.get("yetki", "user"),
            data.get("paket_turu", ""),
            data.get("son_odeme", "") or None,
            now_str,
            0,
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close(); conn.close()
        return {"success": True, "id": new_id}
    except Exception as e:
        logger.error("create_user hata: %s", e)
        return {"success": False, "error": str(e)}


def update_user(user_id: int, data: dict) -> dict:
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        sifre = data.get("sifre", "").strip()
        if sifre:
            hashed = _hash_password(sifre)
            cur.execute("""
                UPDATE uyelik
                   SET ad=%s, soyad=%s, kullanici_adi=%s, eposta=%s,
                       yetki=%s, paket_turu=%s, son_odeme=%s, sifre=%s
                 WHERE id=%s
            """, (data.get("ad",""), data.get("soyad",""),
                  data.get("kullanici_adi",""), data.get("eposta",""),
                  data.get("yetki","user"), data.get("paket_turu",""),
                  data.get("son_odeme","") or None, hashed, user_id))
        else:
            cur.execute("""
                UPDATE uyelik
                   SET ad=%s, soyad=%s, kullanici_adi=%s, eposta=%s,
                       yetki=%s, paket_turu=%s, son_odeme=%s
                 WHERE id=%s
            """, (data.get("ad",""), data.get("soyad",""),
                  data.get("kullanici_adi",""), data.get("eposta",""),
                  data.get("yetki","user"), data.get("paket_turu",""),
                  data.get("son_odeme","") or None, user_id))
        conn.commit()
        cur.close(); conn.close()
        return {"success": True}
    except Exception as e:
        logger.error("update_user hata: %s", e)
        return {"success": False, "error": str(e)}


def delete_user(user_id: int) -> dict:
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute("DELETE FROM uyelik WHERE id=%s", (user_id,))
        conn.commit()
        cur.close(); conn.close()
        return {"success": True}
    except Exception as e:
        logger.error("delete_user hata: %s", e)
        return {"success": False, "error": str(e)}
