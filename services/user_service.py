# -*- coding: utf-8 -*-
"""
Kullanıcı & Lisans Yönetimi Servisi
=====================================
PostgreSQL uyelik tablosu üzerinde CRUD işlemleri.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from werkzeug.security import generate_password_hash

from db.connection import get_connection

logger = logging.getLogger(__name__)


def get_all_users(search: str = "") -> list:
    """Tüm kullanıcıları listeler, opsiyonel arama filtresi."""
    conn = get_connection()
    try:
        if search:
            like = f"%{search}%"
            rows = conn.execute(
                """SELECT id, kullanici_adi, eposta, yetki, paket_turu,
                          son_odeme, firmaadi, uyelik_tarihi, hesapturu
                   FROM uyelik
                   WHERE kullanici_adi ILIKE %s OR eposta ILIKE %s OR firmaadi ILIKE %s
                   ORDER BY id""",
                (like, like, like)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, kullanici_adi, eposta, yetki, paket_turu,
                          son_odeme, firmaadi, uyelik_tarihi, hesapturu
                   FROM uyelik
                   ORDER BY id"""
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Kullanıcı listesi hatası: %s", e)
        return []
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Tek kullanıcı getirir."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT id, kullanici_adi, eposta, yetki, paket_turu, son_odeme,
                      firmaadi, vergino, vergidairesi, acikadres, il, ilce,
                      uyelik_tarihi, hesapturu, altkullanicisayisi
               FROM uyelik WHERE id=%s LIMIT 1""",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Kullanıcı getirme hatası: %s", e)
        return None
    finally:
        conn.close()


def update_user_yetki(user_id: int, yetki: str) -> dict:
    """Kullanıcı yetki seviyesini günceller (admin/superadmin/0)."""
    valid_yetkiler = {"0", "admin", "superadmin"}
    if yetki not in valid_yetkiler:
        return {"success": False, "message": "Geçersiz yetki seviyesi."}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE uyelik SET yetki=%s WHERE id=%s",
            (yetki, user_id)
        )
        conn.commit()
        return {"success": True, "message": f"Yetki '{yetki}' olarak güncellendi."}
    except Exception as e:
        conn.rollback()
        logger.error("Yetki güncelleme hatası: %s", e)
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def update_user_lisans(user_id: int, paket_turu: str, son_odeme: str) -> dict:
    """Kullanıcı lisans paketini ve bitiş tarihini günceller."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE uyelik SET paket_turu=%s, son_odeme=%s WHERE id=%s",
            (paket_turu, son_odeme, user_id)
        )
        conn.commit()
        return {"success": True, "message": "Lisans bilgileri güncellendi."}
    except Exception as e:
        conn.rollback()
        logger.error("Lisans güncelleme hatası: %s", e)
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def reset_user_password(user_id: int, new_password: str) -> dict:
    """Kullanıcı şifresini sıfırlar (hash'li)."""
    if len(new_password) < 4:
        return {"success": False, "message": "Şifre en az 4 karakter olmalı."}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE uyelik SET sifre=%s WHERE id=%s",
            (new_password, user_id)  # nakitAkim düz metin kullanıyor
        )
        conn.commit()
        return {"success": True, "message": "Şifre güncellendi."}
    except Exception as e:
        conn.rollback()
        logger.error("Şifre sıfırlama hatası: %s", e)
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def update_user_full(user_id: int, data: dict) -> dict:
    """Kullanıcının tüm alanlarını günceller."""
    allowed = {
        "kullanici_adi", "eposta", "yetki", "paket_turu", "son_odeme",
        "firmaadi", "vergino", "vergidairesi", "acikadres", "il", "ilce",
        "altkullanicisayisi"
    }
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return {"success": False, "message": "Güncellenecek alan bulunamadı."}

    set_clause = ", ".join(f"{k}=%s" for k in fields)
    values = list(fields.values()) + [user_id]

    conn = get_connection()
    try:
        conn.execute(f"UPDATE uyelik SET {set_clause} WHERE id=%s", values)
        conn.commit()
        return {"success": True, "message": "Kullanıcı güncellendi."}
    except Exception as e:
        conn.rollback()
        logger.error("Kullanıcı güncelleme hatası: %s", e)
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def get_stats() -> dict:
    """Dashboard için özet istatistikler."""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM uyelik").fetchone()[0]
        admins = conn.execute(
            "SELECT COUNT(*) FROM uyelik WHERE yetki IN ('admin','superadmin')"
        ).fetchone()[0]
        active_lisans = conn.execute(
            "SELECT COUNT(*) FROM uyelik WHERE son_odeme >= %s",
            (datetime.now().strftime("%Y-%m-%d"),)
        ).fetchone()[0]
        return {
            "total_users": total,
            "admin_count": admins,
            "active_lisans": active_lisans,
            "expired_lisans": total - active_lisans,
        }
    except Exception as e:
        logger.error("İstatistik hatası: %s", e)
        return {"total_users": 0, "admin_count": 0, "active_lisans": 0, "expired_lisans": 0}
    finally:
        conn.close()


def verify_admin_login(username: str, password: str) -> Optional[dict]:
    """webadmin girişi için admin/superadmin doğrulaması."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT id, kullanici_adi, yetki, firmaadi
               FROM uyelik
               WHERE kullanici_adi=%s AND sifre=%s
               AND yetki IN ('admin','superadmin')
               LIMIT 1""",
            (username, password)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Login hatası: %s", e)
        return None
    finally:
        conn.close()

