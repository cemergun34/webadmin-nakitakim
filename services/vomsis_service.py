"""
VOMSİS API Servisi — nakitAkim/services/vomsis_service.py'dan taşındı
====================================================================
Fonksiyonlar:
    get_vomsis_bilgileri(userid)
    save_vomsis_bilgileri(userid, appkey, seckey, url)
    vomsis_authenticate(url, app_key, app_secret) → (token, err_msg)
    vomsis_get_banks(url, token)
    vomsis_get_accounts(url, token)
    vomsis_get_account_transactions(url, token, account_id, begin, end)
    vomsis_get_all_transactions(url, token, begin, end)
    vomsis_get_all_transactions_chunked(url, token, start_dt, end_dt)
    vomsis_get_terminals(url, token)
    vomsis_get_terminal_transactions(url, token, terminal_id, begin, end)
    vomsis_test_connection(url, app_key, app_secret)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from db.connection import get_connection

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://developers.vomsis.com/api/v2"


# ── Veritabanı işlemleri ──────────────────────────────────────────────────────

def get_vomsis_bilgileri(userid: int) -> dict:
    """vomsisbilgileri tablosundan kullanıcıya ait API bilgilerini döner."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT appkey, seckey, url FROM vomsisbilgileri WHERE userid=? LIMIT 1",
            (userid,)
        ).fetchone()
        if row:
            return {
                "success": True,
                "appkey":  row["appkey"] or "",
                "seckey":  row["seckey"] or "",
                "url":     row["url"]    or DEFAULT_API_URL,
            }
        return {"success": True, "appkey": "", "seckey": "", "url": DEFAULT_API_URL}
    except Exception as e:
        logger.error("VOMSİS bilgileri getirme hatası: %s", e)
        return {"success": False, "appkey": "", "seckey": "", "url": DEFAULT_API_URL}
    finally:
        conn.close()


def save_vomsis_bilgileri(userid: int, appkey: str, seckey: str,
                          url: str = DEFAULT_API_URL) -> dict:
    """vomsisbilgileri tablosuna kayıt ekler veya günceller."""
    if not appkey or not seckey or not url:
        return {"success": False, "message": "Tüm alanları doldurunuz."}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM vomsisbilgileri WHERE userid=? LIMIT 1",
            (userid,)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE vomsisbilgileri
                   SET appkey=%s, seckey=%s, url=%s, guncelleme_tarihi=%s
                   WHERE userid=%s""",
                (appkey, seckey, url, now, userid)
            )
            message = "Vomsis bilgileri güncellendi."
        else:
            conn.execute(
                """INSERT INTO vomsisbilgileri
                   (userid, appkey, seckey, url, kayit_tarihi, guncelleme_tarihi)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (userid, appkey, seckey, url, now, now)
            )
            message = "Vomsis bilgileri kaydedildi."

        conn.commit()
        return {"success": True, "message": message}
    except Exception as e:
        conn.rollback()
        logger.error("VOMSİS kaydetme hatası: %s", e)
        return {"success": False, "message": f"Hata: {e}"}
    finally:
        conn.close()


# ── VOMSİS API İstekleri ──────────────────────────────────────────────────────

def _get_requests():
    try:
        import requests as _req
        return _req
    except ImportError as e:
        raise ImportError("VOMSİS API için 'requests' kütüphanesi gerekli.") from e


def vomsis_authenticate(api_url: str, app_key: str, app_secret: str,
                         timeout: int = 15) -> tuple[Optional[str], str]:
    """VOMSİS token alır. Döner: (token, '') veya (None, hata_mesajı)."""
    req = _get_requests()
    url = api_url.rstrip("/") + "/authenticate"
    try:
        resp = req.post(
            url,
            json={"app_key": app_key, "app_secret": app_secret},
            headers={"Content-Type": "application/json"},
            timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token")
        if token:
            return token, ""
        api_msg = data.get("message") or data.get("error") or "API yanıtında token bulunamadı."
        logger.warning("VOMSİS token alınamadı: %s", api_msg)
        return None, api_msg
    except req.exceptions.Timeout:
        return None, "Bağlantı zaman aşımı."
    except req.exceptions.ConnectionError:
        return None, "VOMSİS sunucusuna ulaşılamadı."
    except Exception as e:
        logger.error("VOMSİS authenticate hatası: %s", e)
        return None, str(e)


def _vomsis_get(api_url: str, token: str, timeout: int = 20) -> dict:
    req = _get_requests()
    try:
        resp = req.get(
            api_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("VOMSİS GET hatası [%s]: %s", api_url, e)
        return {}


def vomsis_get_banks(api_base: str, token: str) -> list:
    data = _vomsis_get(f"{api_base.rstrip('/')}/banks", token)
    return data.get("banks", [])


def vomsis_get_accounts(api_base: str, token: str) -> list:
    data = _vomsis_get(f"{api_base.rstrip('/')}/accounts", token)
    return data.get("accounts", [])


def vomsis_get_account_detail(api_base: str, token: str, account_id) -> dict:
    return _vomsis_get(f"{api_base.rstrip('/')}/accounts/{account_id}", token)


def vomsis_get_account_transactions(api_base: str, token: str,
                                     account_id, begin_date: str,
                                     end_date: str) -> list:
    from urllib.parse import urlencode
    params = urlencode({"beginDate": begin_date, "endDate": end_date})
    url = f"{api_base.rstrip('/')}/accounts/{account_id}/transactions?{params}"
    data = _vomsis_get(url, token)
    return data.get("transactions", [])


def vomsis_get_all_transactions(api_base: str, token: str,
                                 begin_date: str, end_date: str,
                                 bank_name: str = None) -> list:
    from urllib.parse import urlencode
    params = {"beginDate": begin_date, "endDate": end_date}
    if bank_name:
        params["bankName"] = bank_name
    url = f"{api_base.rstrip('/')}/transactions?{urlencode(params)}"
    data = _vomsis_get(url, token)
    return data.get("transactions", [])


def vomsis_get_all_transactions_chunked(api_base: str, token: str,
                                         start_dt: datetime,
                                         end_dt: datetime) -> list:
    """7 günlük parçalara bölerek tüm işlemleri çeker."""
    all_results = []
    current = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    while current < end_dt:
        chunk_end = min(current + timedelta(days=6), end_dt)
        chunk_end = chunk_end.replace(hour=23, minute=59, second=59)
        begin_str = current.strftime("%d-%m-%Y %H:%M:%S")
        end_str   = chunk_end.strftime("%d-%m-%Y %H:%M:%S")
        txs = vomsis_get_all_transactions(api_base, token, begin_str, end_str)
        all_results.extend(txs)
        current = (current + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    return all_results


def vomsis_get_terminals(api_base: str, token: str) -> list:
    data = _vomsis_get(f"{api_base.rstrip('/')}/pos-rapor/stations", token)
    return data.get("data", [])


def vomsis_get_terminal_transactions(api_base: str, token: str,
                                      terminal_id, begin_date: str,
                                      end_date: str) -> list:
    from urllib.parse import urlencode
    params = urlencode({"beginDate": begin_date, "endDate": end_date})
    url = f"{api_base.rstrip('/')}/pos-rapor/stations/{terminal_id}/transactions?{params}"
    data = _vomsis_get(url, token)
    return data.get("transactions", [])


def vomsis_test_connection(api_base: str, app_key: str, app_secret: str) -> dict:
    """Bağlantı testi: token alabiliyorsa hesap listesini döner."""
    token, err_msg = vomsis_authenticate(api_base, app_key, app_secret)
    if not token:
        return {"success": False, "message": err_msg or "Token alınamadı."}
    accounts = vomsis_get_accounts(api_base, token)
    return {
        "success":  True,
        "message":  f"Bağlantı başarılı! {len(accounts)} hesap bulundu.",
        "token":    token,
        "accounts": accounts,
    }
