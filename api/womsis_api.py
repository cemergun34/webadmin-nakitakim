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

    # ── Aşama 0: Şirket profili (sirket_profili) kontrolü — sadece uyarı ──────
    try:
        from db.connection import get_connection
        _conn = get_connection()
        try:
            _sp = _conn.execute(
                "SELECT id FROM sirket_profili WHERE userid=%s LIMIT 1",
                (userid,)
            ).fetchone()
            if _sp is None:
                logger.warning("sirket_profili bulunamadı — userid=%s (sync devam ediyor)", userid)
        finally:
            _conn.close()
    except Exception as _sp_exc:
        logger.debug("sirket_profili kontrol hatası (atlandı): %s", _sp_exc)

    # Tarih aralığı
    try:
        end_dt = datetime.now()
        if body.get("end_date"):
            end_dt = datetime.strptime(body["end_date"], "%Y-%m-%d")
        start_dt = datetime(2026, 1, 1)  # Baştan sona çek
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


# ── Fatura XML ────────────────────────────────────────────────────────────────

import os
import re
from flask import send_from_directory
from werkzeug.utils import secure_filename


def _get_xml_dir(sirket: str = "") -> str:
    basedir = os.path.dirname(os.path.dirname(__file__))
    base = os.path.join(basedir, "data", "fatura_xmls")
    if sirket:
        clean = re.sub(r'[^\w\-]', '_', sirket.strip())
        target = os.path.join(base, clean)
    else:
        target = base
    os.makedirs(target, exist_ok=True)
    return target


@womsis_bp.route("/fatura/upload_xml", methods=["POST"])
@require_api_key
def fatura_upload_xml():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "Dosya bulunamadı."}), 400
    file = request.files['file']
    if not file.filename or not file.filename.endswith('.xml'):
        return jsonify({"success": False, "error": "Geçersiz dosya formatı."}), 400
    sirket = request.form.get("sirket", "")
    target_dir = _get_xml_dir(sirket)
    filename = secure_filename(file.filename)
    file.save(os.path.join(target_dir, filename))
    return jsonify({"success": True, "filename": filename, "sirket": sirket})


@womsis_bp.route("/fatura/get_xml/<sirket>/<filename>", methods=["GET"])
@require_api_key
def fatura_get_xml_sirket(sirket, filename):
    basedir = os.path.dirname(os.path.dirname(__file__))
    base = os.path.join(basedir, "data", "fatura_xmls")
    safe_s = re.sub(r'[^\w\-]', '_', sirket.strip())
    safe_f = secure_filename(filename)
    target = os.path.join(base, safe_s)
    if os.path.exists(os.path.join(target, safe_f)):
        return send_from_directory(target, safe_f)
    return jsonify({"success": False, "error": "Dosya bulunamadı."}), 404


@womsis_bp.route("/fatura/get_xml/<filename>", methods=["GET"])
@require_api_key
def fatura_get_xml(filename):
    basedir = os.path.dirname(os.path.dirname(__file__))
    base = os.path.join(basedir, "data", "fatura_xmls")
    safe_f = secure_filename(filename)
    if os.path.exists(os.path.join(base, safe_f)):
        return send_from_directory(base, safe_f)
    for entry in os.scandir(base) if os.path.exists(base) else []:
        if entry.is_dir():
            candidate = os.path.join(entry.path, safe_f)
            if os.path.exists(candidate):
                return send_from_directory(entry.path, safe_f)
    return jsonify({"success": False, "error": "Dosya bulunamadı."}), 404

