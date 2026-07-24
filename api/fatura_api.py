# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import re
from functools import wraps

from flask import Blueprint, request, jsonify, current_app, send_from_directory
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

fatura_bp = Blueprint("fatura_api", __name__, url_prefix="/api/fatura")

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        expected = current_app.config.get("WEBADMIN_API_KEY", "")
        if not api_key or api_key != expected:
            return jsonify({"success": False, "error": "Geçersiz API anahtarı."}), 401
        return f(*args, **kwargs)
    return decorated

def get_base_upload_dir():
    basedir = os.path.dirname(os.path.dirname(__file__))
    upload_dir = os.path.join(basedir, "data", "fatura_xmls")
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir

def _sirket_klasor(sirket: str) -> str:
    if not sirket:
        return ""
    return re.sub(r'[^\w\-]', '_', sirket.strip())

@fatura_bp.route("/upload_xml", methods=["POST"])
@require_api_key
def upload_xml():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "Dosya bulunamadı."}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "Dosya seçilmedi."}), 400

    if not file.filename.endswith('.xml'):
        return jsonify({"success": False, "error": "Geçersiz dosya formatı. Sadece XML kabul edilir."}), 400

    sirket = _sirket_klasor(request.form.get("sirket", ""))
    base_dir = get_base_upload_dir()

    if sirket:
        target_dir = os.path.join(base_dir, sirket)
        os.makedirs(target_dir, exist_ok=True)
    else:
        target_dir = base_dir

    filename = secure_filename(file.filename)
    save_path = os.path.join(target_dir, filename)
    file.save(save_path)
    return jsonify({"success": True, "filename": filename, "sirket": sirket})

@fatura_bp.route("/get_xml/<sirket>/<filename>", methods=["GET"])
@require_api_key
def get_xml_sirket(sirket, filename):
    base_dir = get_base_upload_dir()
    safe_sirket   = _sirket_klasor(sirket)
    safe_filename = secure_filename(filename)
    target_dir    = os.path.join(base_dir, safe_sirket)
    full_path     = os.path.join(target_dir, safe_filename)
    if os.path.exists(full_path):
        return send_from_directory(target_dir, safe_filename)
    return jsonify({"success": False, "error": "Dosya bulunamadı."}), 404

@fatura_bp.route("/get_xml/<filename>", methods=["GET"])
@require_api_key
def get_xml(filename):
    base_dir      = get_base_upload_dir()
    safe_filename = secure_filename(filename)
    full_path     = os.path.join(base_dir, safe_filename)
    if os.path.exists(full_path):
        return send_from_directory(base_dir, safe_filename)
    for entry in os.scandir(base_dir):
        if entry.is_dir():
            candidate = os.path.join(entry.path, safe_filename)
            if os.path.exists(candidate):
                return send_from_directory(entry.path, safe_filename)
    return jsonify({"success": False, "error": "Dosya bulunamadı."}), 404
