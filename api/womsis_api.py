# -*- coding: utf-8 -*-
"""
REST API Blueprint — Womsis Endpoint'leri
==========================================
nakitAkim uygulaması bu endpoint'leri çağırarak Womsis verilerini alır.

Güvenlik: X-API-Key header zorunlu.

Endpoint'ler:
  POST /api/womsis/sync   → Womsis'ten tüm işlemleri çek, JSON dön
  GET  /api/womsis/status → Son sync durumunu getir
  POST /api/womsis/test   → Bağlantı testi
  GET  /api/womsis/accounts → Hesap listesi
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, current_app

from services.vomsis_service import (
    get_vomsis_bilgileri,
    vomsis_authenticate,
    vomsis_get_all_transactions_chunked,
    vomsis_get_accounts,
    vomsis_get_banks,
    vomsis_test_connection,
)

logger = logging.getLogger(__name__)

womsis_bp = Blueprint("womsis_api", __name__, url_prefix="/api/womsis")

# ── Son sync sonucu bellekte tutulur (process restart'ta sıfırlanır) ──────────
_last_sync: dict = {
    "timestamp": None,
    "count":     0,
    "data":      [],
    "error":     None,
}


# ── API Key doğrulama dekoratörü ──────────────────────────────────────────────

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        expected = current_app.config.get("WEBADMIN_API_KEY", "")
        if not api_key or api_key != expected:
            return jsonify({"success": False, "error": "Geçersiz API anahtarı."}), 401
        return f(*args, **kwargs)
    return decorated


# ── Endpoint'ler ──────────────────────────────────────────────────────────────

@womsis_bp.route("/sync", methods=["POST"])
@require_api_key
def sync_womsis():
    """
    nakitAkim'den POST tetiklendiğinde Womsis'ten son verileri çeker.

    Request Body (JSON):
        {
          "userid":     1,           (zorunlu)
          "start_date": "2024-01-01",  (opsiyonel, varsayılan: 30 gün önce)
          "end_date":   "2024-12-31"   (opsiyonel, varsayılan: bugün)
        }

    Response:
        {
          "success": true,
          "count": 42,
          "transactions": [...],
          "timestamp": "2024-06-09T12:00:00"
        }
    """
    global _last_sync
    body = request.get_json(silent=True) or {}
    userid = body.get("userid", 1)

    # ── Aşama 0: Şirket profili (sirket_profili) tanımlı mı? ─────────────────
    # nakitAkim'in PostgreSQL veritabanında bu kullanıcıya ait şirket kaydı
    # yoksa Womsis verisini çekmenin anlamı yok — doğrudan hata döndür.
    try:
        from db.connection import get_connection
        _conn = get_connection()
        try:
            _sp = _conn.execute(
                """SELECT id, unvan, vergino
                   FROM sirket_profili
                   WHERE userid=%s LIMIT 1""",
                (userid,)
            ).fetchone()
            _sirket_ok = (
                _sp is not None and
                bool((_sp.get("vergino") or _sp.get("unvan") or "").strip())
            )
        finally:
            _conn.close()

        if not _sirket_ok:
            logger.warning("sirket_profili bulunamadı — userid=%s", userid)
            return jsonify({
                "success":    False,
                "error_code": "no_sirket_profili",
                "error":      "Önce iqDenetim üzerinden kullanıcı tanımının yapılması gerekir."
                              " Şirket profili tanımlanmadan Womsis verisi çekilemez."
            }), 422

    except Exception as _sp_exc:
        # sirket_profili tablosu yoksa (eski kurulum) kontrol atlanır
        logger.debug("sirket_profili kontrol hatası (atlandı): %s", _sp_exc)

    # Tarih aralığı
    try:
        end_dt = datetime.now()
        if body.get("end_date"):
            end_dt = datetime.strptime(body["end_date"], "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=30)
        if body.get("start_date"):
            start_dt = datetime.strptime(body["start_date"], "%Y-%m-%d")
    except ValueError as e:
        return jsonify({"success": False, "error": f"Tarih formatı hatalı: {e}"}), 400

    # Womsis bağlantı bilgilerini DB'den al
    bilgi = get_vomsis_bilgileri(userid)
    if not bilgi.get("appkey") or not bilgi.get("seckey"):
        return jsonify({
            "success": False,
            "error": "Womsis API bilgileri tanımlı değil. Önce webadmin'den ayarlayın."
        }), 400

    api_url = bilgi.get("url", "https://developers.vomsis.com/api/v2")
    appkey  = bilgi["appkey"]
    seckey  = bilgi["seckey"]

    # Token al
    token, err = vomsis_authenticate(api_url, appkey, seckey)
    if not token:
        _last_sync["error"] = err
        return jsonify({"success": False, "error": err}), 502

    # Verileri çek
    try:
        transactions = vomsis_get_all_transactions_chunked(api_url, token, start_dt, end_dt)
    except Exception as e:
        logger.error("Womsis sync hatası: %s", e)
        _last_sync["error"] = str(e)
        return jsonify({"success": False, "error": str(e)}), 500

    # Bellekte sakla
    now_str = datetime.now().isoformat()
    _last_sync = {
        "timestamp": now_str,
        "count":     len(transactions),
        "data":      transactions,
        "error":     None,
    }

    logger.info("Womsis sync tamamlandı: %d işlem (userid=%s)", len(transactions), userid)
    return jsonify({
        "success":      True,
        "count":        len(transactions),
        "transactions": transactions,
        "timestamp":    now_str,
        "period":       {
            "start": start_dt.strftime("%Y-%m-%d"),
            "end":   end_dt.strftime("%Y-%m-%d"),
        }
    })


@womsis_bp.route("/status", methods=["GET"])
@require_api_key
def sync_status():
    """Son sync durumunu döner (veri olmadan)."""
    return jsonify({
        "success":   True,
        "timestamp": _last_sync["timestamp"],
        "count":     _last_sync["count"],
        "error":     _last_sync["error"],
    })


@womsis_bp.route("/test", methods=["POST"])
@require_api_key
def test_connection():
    """Womsis bağlantısını test eder."""
    body = request.get_json(silent=True) or {}
    userid = body.get("userid", 1)

    bilgi = get_vomsis_bilgileri(userid)
    api_url = bilgi.get("url", "https://developers.vomsis.com/api/v2")
    appkey  = bilgi.get("appkey", "")
    seckey  = bilgi.get("seckey", "")

    # Body'den override et
    if body.get("appkey"):
        appkey = body["appkey"]
    if body.get("seckey"):
        seckey = body["seckey"]
    if body.get("url"):
        api_url = body["url"]

    result = vomsis_test_connection(api_url, appkey, seckey)
    return jsonify(result), 200 if result["success"] else 502


@womsis_bp.route("/accounts", methods=["GET"])
@require_api_key
def get_accounts():
    """Womsis hesap listesini döner."""
    userid = request.args.get("userid", 1, type=int)
    bilgi  = get_vomsis_bilgileri(userid)
    if not bilgi.get("appkey"):
        return jsonify({"success": False, "error": "Womsis bilgileri tanımlı değil."}), 400

    token, err = vomsis_authenticate(
        bilgi.get("url", "https://developers.vomsis.com/api/v2"),
        bilgi["appkey"],
        bilgi["seckey"]
    )
    if not token:
        return jsonify({"success": False, "error": err}), 502

    accounts = vomsis_get_accounts(
        bilgi.get("url", "https://developers.vomsis.com/api/v2"), token
    )
    return jsonify({"success": True, "accounts": accounts, "count": len(accounts)})
