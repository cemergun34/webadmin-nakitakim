# -*- coding: utf-8 -*-
"""
webadmin-nakitAkim — Konfigürasyon
===================================
Tüm ortam değişkenleri ve sabit ayarlar burada.
"""
import os
from pathlib import Path

# ── Temel ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("WEBADMIN_SECRET_KEY", "webadmin-nakit-akim-gizli-anahtar-2024")
DEBUG      = os.environ.get("WEBADMIN_DEBUG", "true").lower() == "true"
PORT       = int(os.environ.get("WEBADMIN_PORT", 5050))
HOST       = os.environ.get("WEBADMIN_HOST", "0.0.0.0")

# ── REST API Güvenlik Anahtarı ────────────────────────────────────────────────
# nakitAkim POST isteği yaparken bu key'i X-API-Key header'ında gönderir
WEBADMIN_API_KEY = os.environ.get("WEBADMIN_API_KEY", "nakit-akim-api-key-2024-secure")

# ── PostgreSQL Bağlantısı ─────────────────────────────────────────────────────
# Önce ortam değişkenlerine bakar, yoksa nakitAkim db_config.json'ı okur
PG_HOST    = os.environ.get("PG_HOST",    None)
PG_PORT    = int(os.environ.get("PG_PORT", 5432))
PG_DB      = os.environ.get("PG_DB",      None)
PG_USER    = os.environ.get("PG_USER",    None)
PG_PASS    = os.environ.get("PG_PASS",    None)
PG_SSLMODE = os.environ.get("PG_SSLMODE", "prefer")

# nakitAkim config dosyası (fallback)
NAKIT_AKIM_CONFIG = (
    Path.home() / "NakitAkim" / "data" / "db_config.json"
)

# ── Womsis varsayılanları ─────────────────────────────────────────────────────
DEFAULT_VOMSIS_URL = "https://developers.vomsis.com/api/v2"

# ── Session ───────────────────────────────────────────────────────────────────
SESSION_LIFETIME_HOURS = 8
