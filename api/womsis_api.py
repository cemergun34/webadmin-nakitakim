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
    vomsis_get_terminals,
    vomsis_get_terminal_transactions,
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
          "userid":     19,          (zorunlu)
          "musterino": 1,            (opsiyonel, varsayılan: 1)
          "start_date": "2024-01-01",
          "end_date":   "2024-12-31"
        }
    """
    global _last_sync
    body     = request.get_json(silent=True) or {}
    userid   = body.get("userid", 1)
    musterino = int(body.get("musterino", 1))

    # ── Aşama 0: Şirket profili kontrolü ──────────────────────────────────────
    try:
        from db.connection import get_connection
        _conn = get_connection()
        try:
            _sp = _conn.execute(
                "SELECT id FROM sirket_profili WHERE musterino=%s LIMIT 1",
                (musterino,)
            ).fetchone()
            if _sp is None:
                logger.warning("sirket_profili bulunamadı — musterino=%s (sync devam ediyor)", musterino)
        finally:
            _conn.close()
    except Exception as _sp_exc:
        logger.debug("sirket_profili kontrol hatası (atlandı): %s", _sp_exc)

    # Tarih aralığı
    try:
        end_dt = datetime.now()
        if body.get("end_date"):
            end_dt = datetime.strptime(body["end_date"], "%Y-%m-%d")
        start_dt = datetime(2026, 1, 1)
        if body.get("start_date"):
            start_dt = datetime.strptime(body["start_date"], "%Y-%m-%d")
    except ValueError as e:
        return jsonify({"success": False, "error": f"Tarih formatı hatalı: {e}"}), 400

    # Womsis bağlantı bilgilerini DB'den al — musterino ile şirket bazlı
    bilgi = get_vomsis_bilgileri(userid, musterino)
    if not bilgi.get("appkey") or not bilgi.get("seckey"):
        return jsonify({
            "success": False,
            "error": f"Womsis API bilgileri tanımlı değil (userid={userid}, musterino={musterino}). "
                     "Önce şirket ayarlarından Womsis bilgilerini kaydedin."
        }), 400

    api_url = bilgi.get("url", DEFAULT_API_URL)
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

    logger.info("Womsis sync tamamlandı: %d işlem (userid=%s, musterino=%s)",
                len(transactions), userid, musterino)
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


@womsis_bp.route("/pos-sync", methods=["POST"])
@require_api_key
def pos_sync_womsis():
    """
    nakitAkim'den POST tetiklendiğinde Womsis POS terminal verilerini çeker
    ve womsi_pos tablosuna kaydeder.

    Banka hareketi sync (/api/womsis/sync) ile birebir aynı mimari.

    Request Body (JSON):
        {
          "userid":     19,
          "musterino":  1,
          "start_date": "2026-01-01",
          "end_date":   "2026-07-27"
        }
    """
    body      = request.get_json(silent=True) or {}
    userid    = body.get("userid", 1)
    musterino = int(body.get("musterino", 1))

    # ── Tarih aralığı ──────────────────────────────────────────────────────────
    try:
        end_dt = datetime.now()
        if body.get("end_date"):
            end_dt = datetime.strptime(body["end_date"], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
        start_dt = datetime(2026, 1, 1)
        if body.get("start_date"):
            start_dt = datetime.strptime(body["start_date"], "%Y-%m-%d")
    except ValueError as e:
        return jsonify({"success": False, "error": f"Tarih formatı hatalı: {e}"}), 400

    # ── Womsis bağlantı bilgileri ──────────────────────────────────────────────
    bilgi = get_vomsis_bilgileri(userid, musterino)
    if not bilgi.get("appkey") or not bilgi.get("seckey"):
        return jsonify({
            "success": False,
            "error":   f"Womsis API bilgileri tanımlı değil (userid={userid}, musterino={musterino})."
        }), 400

    api_url = bilgi.get("url", "https://developers.vomsis.com/api/v2")
    token, err = vomsis_authenticate(api_url, bilgi["appkey"], bilgi["seckey"])
    if not token:
        return jsonify({"success": False, "error": err}), 502

    # ── Terminal listesini al ──────────────────────────────────────────────────
    try:
        terminals = vomsis_get_terminals(api_url, token)
    except Exception as e:
        logger.error("Womsis terminal listesi hatası: %s", e)
        return jsonify({"success": False, "error": f"Terminal listesi alınamadı: {e}"}), 500

    if not terminals:
        return jsonify({
            "success": True,
            "count":   0,
            "saved":   0,
            "skipped": 0,
            "message": "Womsis'te tanımlı terminal bulunamadı.",
            "period":  {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
        })

    # ── Her terminal için 14 günlük parçalar hâlinde veri çek ─────────────────
    from services.scheduler_service import _save_womsis_pos_to_db
    from datetime import timedelta

    total_fetched = 0
    total_saved   = 0
    total_skipped = 0
    now_str = datetime.now().isoformat()
    CHUNK_DAYS = 14

    for term in terminals:
        t_id = term.get("stationId") or term.get("id") or term.get("terminalId")
        if not t_id:
            continue
            
        current_start = start_dt
        while current_start <= end_dt:
            current_end = current_start + timedelta(days=CHUNK_DAYS - 1)
            if current_end > end_dt:
                current_end = end_dt
                
            current_end = current_end.replace(hour=23, minute=59, second=59)

            b_str = current_start.strftime("%d-%m-%Y %H:%M:%S")
            e_str = current_end.strftime("%d-%m-%Y %H:%M:%S")

            try:
                term_txs = vomsis_get_terminal_transactions(api_url, token, t_id, b_str, e_str)
                if term_txs:
                    total_fetched += len(term_txs)
                    ps, psk = _save_womsis_pos_to_db(
                        term_txs, str(t_id), userid=userid, musterino=musterino
                    )
                    total_saved   += ps
                    total_skipped += psk
                    logger.info("POS sync — terminal %s (%s - %s): %d çekildi, %d kaydedildi, %d atlandı",
                                t_id, b_str, e_str, len(term_txs), ps, psk)
            except Exception as te:
                logger.warning("Terminal %s hatası (%s - %s): %s", t_id, b_str, e_str, te)
                
            current_start = current_end + timedelta(seconds=1)

    logger.info("Womsis POS sync tamamlandı: %d çekildi, %d kaydedildi (userid=%s, musterino=%s)",
                total_fetched, total_saved, userid, musterino)

    return jsonify({
        "success":   True,
        "count":     total_fetched,
        "saved":     total_saved,
        "skipped":   total_skipped,
        "timestamp": now_str,
        "period":    {
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
    """Womsis bağlantısını test eder.
    Body: {userid, musterino, appkey, seckey, url}
    """
    body      = request.get_json(silent=True) or {}
    userid    = body.get("userid", 1)
    musterino = int(body.get("musterino", 1))

    bilgi   = get_vomsis_bilgileri(userid, musterino)
    api_url = bilgi.get("url", DEFAULT_API_URL)
    appkey  = bilgi.get("appkey", "")
    seckey  = bilgi.get("seckey", "")

    # Body'den override et (canlı test için)
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

