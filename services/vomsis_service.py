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

def get_vomsis_bilgileri(userid: int, musterino: int = 1) -> dict:
    """vomsisbilgileri tablosundan kullanıcıya ait API bilgilerini döner.
    musterino ile şirket bazlı izolasyon sağlanır.
    """
    conn = get_connection()
    try:
        # Önce (userid, musterino) çiftiyle bak; bulamazsa sadece userid ile dene
        row = conn.execute(
            "SELECT appkey, seckey, url FROM vomsisbilgileri "
            "WHERE userid=%s AND musterino=%s LIMIT 1",
            (userid, musterino)
        ).fetchone()
        if not row:
            # Geriye dönük uyumluluk: musterino=1 varsayılan kaydına düş
            row = conn.execute(
                "SELECT appkey, seckey, url FROM vomsisbilgileri "
                "WHERE userid=%s ORDER BY id ASC LIMIT 1",
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


def save_vomsis_bilgileri(userid: int, musterino: int = 1,
                          appkey: str = "", seckey: str = "",
                          url: str = DEFAULT_API_URL) -> dict:
    """vomsisbilgileri tablosuna kayıt ekler veya günceller.
    (userid, musterino) çifti uniq anahtardır.
    """
    if not appkey or not seckey or not url:
        return {"success": False, "message": "Tüm alanları doldurunuz."}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM vomsisbilgileri WHERE userid=%s AND musterino=%s LIMIT 1",
            (userid, musterino)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE vomsisbilgileri
                   SET appkey=%s, seckey=%s, url=%s, guncelleme_tarihi=%s
                   WHERE userid=%s AND musterino=%s""",
                (appkey, seckey, url, now, userid, musterino)
            )
            message = "Vomsis bilgileri güncellendi."
        else:
            conn.execute(
                """INSERT INTO vomsisbilgileri
                   (userid, musterino, appkey, seckey, url, kayit_tarihi, guncelleme_tarihi)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (userid, musterino, appkey, seckey, url, now, now)
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
        if resp.status_code == 404:
            logger.debug("VOMSİS 404 [%s]", api_url)
            return {}
        resp.raise_for_status()
        data = resp.json()
        logger.debug("VOMSİS GET OK [%s] status=%s keys=%s",
                     api_url, resp.status_code, list(data.keys()) if isinstance(data, dict) else type(data).__name__)
        return data
    except Exception as e:
        logger.warning("VOMSİS GET hatası [%s]: %s", api_url, e)
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
    """
    POS terminal/station listesini çeker.
    PHP womsisPosIsle.php: /pos-rapor/stations
    Her station nesnesi: {id, station_no, workplace_no, bank_title, ...}
    """
    # Önce PHP ile aynı primary endpoint dene
    candidate_paths = [
        "/pos-rapor/stations",
        "/pos/stations",
        "/womsiPos/stations",
        "/terminals",
    ]
    for path in candidate_paths:
        data = _vomsis_get(f"{api_base.rstrip('/')}{path}", token)
        if data:
            result = (
                data.get("data") or
                data.get("stations") or
                data.get("terminals") or
                data.get("pos_stations") or
                []
            )
            if result:
                logger.info("POS terminaller bulundu [%s]: %d adet — örnek: %s",
                            path, len(result),
                            list(result[0].keys()) if result else [])
                return result
    logger.warning("POS terminal listesi boş döndü — tüm endpoint'ler denendi.")
    return []


def vomsis_get_terminal_transactions(api_base: str, token: str,
                                      terminal_id, begin_date: str,
                                      end_date: str) -> list:
    """
    Belirli terminal için POS işlemlerini çeker.
    PHP womsisPosIsle.php: beginDate/endDate formatı 'd-m-Y' (DD-MM-YYYY, saat YOK)
    Örnek: beginDate=01-07-2026, endDate=14-07-2026
    """
    from urllib.parse import urlencode

    # PHP ile aynı tarih formatına çevir: eğer 'DD-MM-YYYY HH:MM:SS' geliyorsa
    # sadece 'DD-MM-YYYY' kısmını al
    def _to_date_only(d: str) -> str:
        return d[:10] if d else d  # 'DD-MM-YYYY HH:MM:SS' → 'DD-MM-YYYY'

    begin_only = _to_date_only(begin_date)
    end_only   = _to_date_only(end_date)

    params = urlencode({"beginDate": begin_only, "endDate": end_only})

    # Endpoint adayları (PHP primary'si önce)
    candidate_urls = [
        f"{api_base.rstrip('/')}/pos-rapor/stations/{terminal_id}/transactions?{params}",
        f"{api_base.rstrip('/')}/pos/stations/{terminal_id}/transactions?{params}",
        f"{api_base.rstrip('/')}/pos-rapor/{terminal_id}/transactions?{params}",
    ]
    for url in candidate_urls:
        data = _vomsis_get(url, token)
        if data:
            result = (
                data.get("transactions") or
                data.get("data") or
                data.get("posTransactions") or
                []
            )
            if result:
                logger.info("POS işlemler bulundu [terminal=%s]: %d adet (%s → %s)",
                            terminal_id, len(result), begin_only, end_only)
                return result
    logger.debug("POS işlem bulunamadı [terminal=%s, %s → %s]", terminal_id, begin_only, end_only)
    return []


def vomsis_get_pos_transactions_direct(api_base: str, token: str,
                                       begin_date: str, end_date: str) -> list:
    """
    Terminal bazlı değil, doğrudan tüm POS işlemlerini tek endpoint'ten çeker.
    Bazı Womsis API versiyonlarında /pos/transactions endpoint'i mevcuttur.
    """
    from urllib.parse import urlencode
    params = urlencode({"beginDate": begin_date, "endDate": end_date})
    candidate_paths = [
        f"/pos/transactions?{params}",
        f"/womsiPos/transactions?{params}",
        f"/pos-rapor/transactions?{params}",
    ]
    for path in candidate_paths:
        data = _vomsis_get(f"{api_base.rstrip('/')}{path}", token)
        if data:
            result = (
                data.get("transactions") or
                data.get("posTransactions") or
                data.get("pos_transactions") or
                data.get("data") or
                []
            )
            if result:
                logger.info("POS direct endpoint [%s]: %d kayıt", path, len(result))
                return result
    return []


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
