# -*- coding: utf-8 -*-
"""
Şirket Bazlı webadmin Bağlantı Yapılandırması
===============================================
Her şirket (userid) için hangi webadmin URL'ine bağlanacağını yönetir.
Bu veriler PostgreSQL webadmin_sirket_config tablosunda tutulur.
nakitAkim uygulaması bu tablodan okur — JSON dosyasına gerek yok.

🔒 Güvenlik: Kaydetmeden önce bağlantı + API key testi yapılır.
   Başarısız olursa kayıt gerçekleşmez.
"""
from __future__ import annotations

import logging
import requests as req_lib
from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for, flash

logger = logging.getLogger(__name__)

sirket_config_bp = Blueprint("sirket_config", __name__)

_TEST_TIMEOUT = 7   # saniye


# ── Bağlantı Test Fonksiyonu ─────────────────────────────────────────────────

def test_webadmin_connection(webadmin_url: str, api_key: str) -> dict:
    """
    webadmin sunucusuna iki aşamalı bağlantı testi yapar:
      1. GET /  → sunucu erişilebilir mi?
      2. GET /api/womsis/accounts?userid=0  → API key geçerli mi?
    
    Returns:
        {"ok": True, "msg": "..."}  veya  {"ok": False, "msg": "..."}
    """
    base = webadmin_url.rstrip("/")

    # ── Aşama 1: Sunucu erişilebilirlik ──────────────────────────────────────
    try:
        r = req_lib.get(f"{base}/", timeout=_TEST_TIMEOUT, verify=False,
                        allow_redirects=True)
        if r.status_code not in (200, 302, 404):
            return {
                "ok": False,
                "msg": f"Sunucuya ulaşıldı ancak beklenmedik HTTP {r.status_code} döndü. "
                       f"URL doğru mu? ({base})"
            }
    except req_lib.exceptions.ConnectionError:
        return {
            "ok": False,
            "msg": f"❌  {base} adresine bağlanılamadı. "
                   "Sunucunun çalıştığından ve portun açık olduğundan emin olun."
        }
    except req_lib.exceptions.Timeout:
        return {
            "ok": False,
            "msg": f"⏱  {base} adresi {_TEST_TIMEOUT} saniyede yanıt vermedi. "
                   "Sunucu çalışıyor mu? Firewall kontrolü yapın."
        }
    except Exception as e:
        return {"ok": False, "msg": f"Bağlantı hatası: {e}"}

    # ── Aşama 2: API Key doğrulaması ──────────────────────────────────────────
    if api_key:
        try:
            r2 = req_lib.get(
                f"{base}/api/womsis/sync",
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                params={"userid": 0},
                timeout=_TEST_TIMEOUT,
                verify=False,
            )
            # 422 veya 200 → API key kabul edildi
            # 401/403 → geçersiz API key
            if r2.status_code in (401, 403):
                return {
                    "ok": False,
                    "msg": f"🔑  API Key geçersiz! Sunucu HTTP {r2.status_code} döndü. "
                           "webadmin config.py'deki WEBADMIN_API_KEY ile eşleşmeli."
                }
        except Exception:
            # API test başarısız olsa bile sunucu çalışıyorsa devam et
            pass

    return {
        "ok": True,
        "msg": f"✅  {base} adresine başarıyla bağlanıldı ve API key doğrulandı."
    }


# ── DB yardımcı ──────────────────────────────────────────────────────────────

def _get_conn():
    from db.connection import get_pg_connection
    return get_pg_connection()


def ensure_table():
    """webadmin_sirket_config tablosu yoksa oluşturur."""
    try:
        conn = _get_conn()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS webadmin_sirket_config (
                id                SERIAL PRIMARY KEY,
                userid            INTEGER NOT NULL UNIQUE,
                firmaadi          TEXT    NOT NULL DEFAULT '',
                webadmin_url      TEXT    NOT NULL DEFAULT 'http://localhost:5050',
                api_key           TEXT    NOT NULL DEFAULT '',
                aktif             BOOLEAN NOT NULL DEFAULT TRUE,
                kayit_tarihi      TEXT    DEFAULT NOW()::TEXT,
                guncelleme_tarihi TEXT    DEFAULT NOW()::TEXT
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning("ensure_table hata: %s", e)


def get_all_configs():
    try:
        conn = _get_conn()
        if not conn:
            return []
        cur = conn.cursor()
        cur.execute("""
            SELECT wsc.id, wsc.userid, wsc.firmaadi, wsc.webadmin_url,
                   wsc.api_key, wsc.aktif, wsc.guncelleme_tarihi,
                   u.kullanici_adi
            FROM webadmin_sirket_config wsc
            LEFT JOIN uyelik u ON u.id = wsc.userid
            ORDER BY wsc.id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {
                "id":           r[0],
                "userid":       r[1],
                "firmaadi":     r[2],
                "webadmin_url": r[3],
                "api_key":      r[4],
                "aktif":        r[5],
                "guncelleme":   r[6],
                "kullanici_adi": r[7] or "",
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("get_all_configs hata: %s", e)
        return []


def get_all_users_simple():
    """Mevcut uyelik kullanıcılarını döner (dropdown için)."""
    try:
        conn = _get_conn()
        if not conn:
            return []
        cur = conn.cursor()
        cur.execute("SELECT id, kullanici_adi, eposta FROM uyelik ORDER BY id")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"id": r[0], "kullanici_adi": r[1], "eposta": r[2] or ""} for r in rows]
    except Exception as e:
        logger.warning("get_all_users_simple hata: %s", e)
        return []


def upsert_config(userid: int, firmaadi: str, webadmin_url: str,
                  api_key: str, aktif: bool) -> bool:
    try:
        conn = _get_conn()
        if not conn:
            return False
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO webadmin_sirket_config
                (userid, firmaadi, webadmin_url, api_key, aktif, guncelleme_tarihi)
            VALUES (%s, %s, %s, %s, %s, NOW()::TEXT)
            ON CONFLICT (userid) DO UPDATE SET
                firmaadi          = EXCLUDED.firmaadi,
                webadmin_url      = EXCLUDED.webadmin_url,
                api_key           = EXCLUDED.api_key,
                aktif             = EXCLUDED.aktif,
                guncelleme_tarihi = NOW()::TEXT
        """, (userid, firmaadi, webadmin_url.rstrip("/"), api_key, aktif))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error("upsert_config hata: %s", e)
        return False


def delete_config(config_id: int) -> bool:
    try:
        conn = _get_conn()
        if not conn:
            return False
        cur = conn.cursor()
        cur.execute("DELETE FROM webadmin_sirket_config WHERE id = %s", (config_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error("delete_config hata: %s", e)
        return False


# ── Web route'ları ────────────────────────────────────────────────────────────

@sirket_config_bp.route("/admin/sirket-config")
def sirket_config_list():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    ensure_table()
    configs  = get_all_configs()
    users    = get_all_users_simple()
    return render_template(
        "sirket_config.html",
        configs=configs,
        users=users,
        page_title="Şirket webadmin Yapılandırması",
    )


@sirket_config_bp.route("/admin/sirket-config/save", methods=["POST"])
def sirket_config_save():
    """
    🔒 GÜVENLİK: Kaydetmeden önce webadmin bağlantısını test eder.
    Test başarısız olursa hiçbir şey kaydedilmez.
    """
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    userid       = int(request.form.get("userid", 0))
    firmaadi     = request.form.get("firmaadi", "").strip()
    webadmin_url = request.form.get("webadmin_url", "").strip()
    api_key      = request.form.get("api_key", "").strip()
    aktif        = request.form.get("aktif") == "1"

    if not userid or not webadmin_url:
        flash("⚠️  Kullanıcı ve webadmin URL zorunludur.", "error")
        return redirect(url_for("sirket_config.sirket_config_list"))

    # ── 🔒 Önce bağlantı testi ────────────────────────────────────────────────
    test = test_webadmin_connection(webadmin_url, api_key)
    if not test["ok"]:
        flash(
            f"❌  Bağlantı testi başarısız — kayıt yapılmadı!\n{test['msg']}",
            "error"
        )
        return redirect(url_for("sirket_config.sirket_config_list"))

    # ── Test başarılı → Kaydet ────────────────────────────────────────────────
    ok = upsert_config(userid, firmaadi, webadmin_url, api_key, aktif)
    if ok:
        flash(
            f"✅  Bağlantı testi başarılı ve yapılandırma kaydedildi. "
            f"(userid={userid}, firma={firmaadi or '—'})",
            "success"
        )
    else:
        flash("❌  Bağlantı başarılı ancak DB'ye yazılamadı. DB bağlantısını kontrol edin.", "error")
    return redirect(url_for("sirket_config.sirket_config_list"))


@sirket_config_bp.route("/admin/sirket-config/test", methods=["POST"])
def sirket_config_test():
    """
    AJAX endpoint — sadece test yapar, kaydetmez.
    Formdan 'Test Et' butonuna basıldığında çağrılır.
    """
    if not session.get("logged_in"):
        return jsonify({"ok": False, "msg": "Oturum açmanız gerekiyor."}), 401

    data         = request.get_json(silent=True) or {}
    webadmin_url = data.get("webadmin_url", "").strip()
    api_key      = data.get("api_key", "").strip()

    if not webadmin_url:
        return jsonify({"ok": False, "msg": "webadmin URL boş olamaz."})

    result = test_webadmin_connection(webadmin_url, api_key)
    return jsonify(result)


@sirket_config_bp.route("/admin/sirket-config/delete/<int:config_id>", methods=["POST"])
def sirket_config_delete(config_id: int):
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    ok = delete_config(config_id)
    flash("✅  Kayıt silindi." if ok else "❌  Silinemedi.", "success" if ok else "error")
    return redirect(url_for("sirket_config.sirket_config_list"))


# ── REST API (nakitAkim'in okuyabileceği endpoint) ───────────────────────────

@sirket_config_bp.route("/api/sirket-config/<int:userid>", methods=["GET"])
def api_get_config(userid: int):
    """nakitAkim bu endpoint'i kullanarak kendi webadmin URL'ini öğrenebilir."""
    from config import WEBADMIN_API_KEY
    api_key = request.headers.get("X-API-Key", "")
    if api_key != WEBADMIN_API_KEY:
        return jsonify({"success": False, "error": "Yetkisiz erişim"}), 401

    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT webadmin_url, api_key, aktif, firmaadi "
            "FROM webadmin_sirket_config WHERE userid = %s LIMIT 1",
            (userid,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return jsonify({
                "success":      True,
                "webadmin_url": row[0],
                "api_key":      row[1],
                "aktif":        row[2],
                "firmaadi":     row[3],
            })
        return jsonify({"success": False, "error": "Şirket kaydı bulunamadı"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
