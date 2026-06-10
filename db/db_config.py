# -*- coding: utf-8 -*-
"""
DB Konfigürasyon Yöneticisi
=============================
nakitAkim'in db_config.json dosyasını okur.
Ortam değişkenleri önceliklidir.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# nakitAkim'in config dosyası
_CONFIG_FILE = Path.home() / "NakitAkim" / "data" / "db_config.json"

_DEFAULTS: dict = {
    "mode":      "postgres",
    "pg_host":   "localhost",
    "pg_port":   5432,
    "pg_db":     "nakit_akim",
    "pg_user":   "postgres",
    "pg_pass":   "",
    "pg_sslmode": "prefer",
}


def load_config() -> dict:
    """nakitAkim db_config.json'ı okur; yoksa varsayılan döner."""
    cfg = dict(_DEFAULTS)
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                cfg[k] = v
        except Exception:
            pass

    # Ortam değişkenleri her şeyin üzerinde
    for env_key, cfg_key in [
        ("PG_HOST",    "pg_host"),
        ("PG_PORT",    "pg_port"),
        ("PG_DB",      "pg_db"),
        ("PG_USER",    "pg_user"),
        ("PG_PASS",    "pg_pass"),
        ("PG_SSLMODE", "pg_sslmode"),
    ]:
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = val

    return cfg


def get_pg_params() -> dict:
    """psycopg2.connect(**params) için dict döndürür."""
    cfg = load_config()
    params: dict = {
        "host":    cfg["pg_host"],
        "port":    int(cfg["pg_port"]),
        "dbname":  cfg["pg_db"],
        "user":    cfg["pg_user"],
        "sslmode": cfg.get("pg_sslmode", "prefer"),
        "connect_timeout": 10,
    }
    if cfg.get("pg_pass"):
        params["password"] = cfg["pg_pass"]
    return params
