# -*- coding: utf-8 -*-
"""
webadmin-nakitAkim — Flask Ana Uygulama
========================================
Çalıştırmak için: python app.py
Varsayılan port: 5050
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g
)

from config import SECRET_KEY, DEBUG, PORT, HOST, WEBADMIN_API_KEY
from db.connection import test_connection
from services.user_service import (
    get_all_users, get_user_by_id, update_user_yetki,
    update_user_lisans, reset_user_password, update_user_full,
    get_stats, verify_admin_login,
)
from services.vomsis_service import (
    get_vomsis_bilgileri, save_vomsis_bilgileri,
    vomsis_authenticate, vomsis_get_all_transactions_chunked,
    vomsis_get_accounts, vomsis_get_banks,
    vomsis_test_connection, DEFAULT_API_URL,
)
from api.womsis_api import womsis_bp
from api.sirket_config import sirket_config_bp
from api.fatura_api import fatura_bp
from services.scheduler_service import (
    start_scheduler, stop_scheduler, run_womsis_sync_job,
    get_scheduler_state, get_sync_logs,
)

# ── Loglama ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["WEBADMIN_API_KEY"] = WEBADMIN_API_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

# Blueprint kayıt
app.register_blueprint(womsis_bp)
app.register_blueprint(sirket_config_bp)
app.register_blueprint(fatura_bp)


# ── Auth dekoratörü ───────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Lütfen giriş yapın.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ────────────────────────────────────────────────────────────────────────────
# AUTH
# ────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = verify_admin_login(username, password)
        if user:
            session.permanent = True
            session["user_id"]   = user["id"]
            session["username"]  = user["kullanici_adi"]
            session["yetki"]     = user["yetki"]
            session["firmaadi"]  = user.get("firmaadi", "")
            flash(f"Hoş geldiniz, {user['kullanici_adi']}!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Kullanıcı adı veya şifre hatalı.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Çıkış yapıldı.", "info")
    return redirect(url_for("login"))


# ────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ────────────────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    stats = get_stats()
    db_status = test_connection()
    return render_template("dashboard.html",
                           stats=stats,
                           db_status=db_status,
                           now=datetime.now())


# ────────────────────────────────────────────────────────────────────────────
# KULLANICI YÖNETİMİ
# ────────────────────────────────────────────────────────────────────────────

@app.route("/users")
@login_required
def users():
    search = request.args.get("search", "").strip()
    user_list = get_all_users(search)
    return render_template("users.html", users=user_list, search=search)


@app.route("/users/<int:user_id>", methods=["GET", "POST"])
@login_required
def user_edit(user_id):
    user = get_user_by_id(user_id)
    if not user:
        flash("Kullanıcı bulunamadı.", "danger")
        return redirect(url_for("users"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_info":
            data = {
                "kullanici_adi": request.form.get("kullanici_adi", "").strip(),
                "eposta":        request.form.get("eposta", "").strip(),
                "firmaadi":      request.form.get("firmaadi", "").strip(),
                "vergino":       request.form.get("vergino", "").strip(),
                "il":            request.form.get("il", "").strip(),
                "ilce":          request.form.get("ilce", "").strip(),
            }
            result = update_user_full(user_id, data)
            flash(result["message"], "success" if result["success"] else "danger")

        elif action == "update_yetki":
            yetki = request.form.get("yetki", "0")
            result = update_user_yetki(user_id, yetki)
            flash(result["message"], "success" if result["success"] else "danger")

        elif action == "update_lisans":
            paket_turu = request.form.get("paket_turu", "")
            son_odeme  = request.form.get("son_odeme", "")
            result = update_user_lisans(user_id, paket_turu, son_odeme)
            flash(result["message"], "success" if result["success"] else "danger")

        elif action == "reset_password":
            new_pass = request.form.get("new_password", "").strip()
            result   = reset_user_password(user_id, new_pass)
            flash(result["message"], "success" if result["success"] else "danger")

        return redirect(url_for("user_edit", user_id=user_id))

    user = get_user_by_id(user_id)
    return render_template("user_edit.html", user=user)


# ────────────────────────────────────────────────────────────────────────────
# WOMSIS PANELİ
# ────────────────────────────────────────────────────────────────────────────

@app.route("/womsis", methods=["GET", "POST"])
@login_required
def womsis():
    userid = session["user_id"]
    bilgi  = get_vomsis_bilgileri(userid)
    result = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_settings":
            appkey = request.form.get("appkey", "").strip()
            seckey = request.form.get("seckey", "").strip()
            url    = request.form.get("url", DEFAULT_API_URL).strip()
            res    = save_vomsis_bilgileri(userid, appkey, seckey, url)
            flash(res["message"], "success" if res["success"] else "danger")
            bilgi  = get_vomsis_bilgileri(userid)

        elif action == "test_connection":
            appkey = request.form.get("appkey", "").strip() or bilgi.get("appkey", "")
            seckey = request.form.get("seckey", "").strip() or bilgi.get("seckey", "")
            url    = request.form.get("url", DEFAULT_API_URL).strip() or bilgi.get("url", DEFAULT_API_URL)
            result = vomsis_test_connection(url, appkey, seckey)

        elif action == "fetch_data":
            # "Verileri Çek" butonu
            appkey = bilgi.get("appkey", "")
            seckey = bilgi.get("seckey", "")
            url    = bilgi.get("url", DEFAULT_API_URL)

            start_str = request.form.get("start_date", "")
            end_str   = request.form.get("end_date", "")

            try:
                end_dt   = datetime.strptime(end_str, "%Y-%m-%d") if end_str else datetime.now()
                start_dt = datetime.strptime(start_str, "%Y-%m-%d") if start_str else end_dt - timedelta(days=30)
            except ValueError:
                flash("Tarih formatı hatalı (YYYY-MM-DD).", "danger")
                return redirect(url_for("womsis"))

            if not appkey or not seckey:
                flash("Önce Womsis API bilgilerini kaydedin.", "warning")
                return redirect(url_for("womsis"))

            token, err = vomsis_authenticate(url, appkey, seckey)
            if not token:
                flash(f"Token alınamadı: {err}", "danger")
            else:
                transactions = vomsis_get_all_transactions_chunked(url, token, start_dt, end_dt)
                
                # ── DB'ye kaydet (womsis_banka) ──────────────────────────────
                from services.scheduler_service import _save_womsis_to_db, _save_womsis_pos_to_db
                from services.vomsis_service import vomsis_get_terminals, vomsis_get_terminal_transactions
                
                saved, skipped = _save_womsis_to_db(transactions, userid=1, musterino=1)
                
                # ── POS Verilerini Çek ve Kaydet (Manuel Tetikleme) ───────────
                terminals = vomsis_get_terminals(url, token)
                pos_txs_total = []
                pos_saved = 0
                pos_skipped = 0

                if terminals:
                    b_str = start_dt.strftime("%d-%m-%Y %H:%M:%S")
                    e_str = end_dt.strftime("%d-%m-%Y %H:%M:%S")
                    for term in terminals:
                        t_id = term.get("stationId") or term.get("id") or term.get("terminalId")
                        if t_id:
                            term_txs = vomsis_get_terminal_transactions(url, token, t_id, b_str, e_str)
                            if term_txs:
                                pos_txs_total.extend(term_txs)
                                ps, psk = _save_womsis_pos_to_db(term_txs, t_id, userid=1, musterino=1)
                                pos_saved += ps
                                pos_skipped += psk
                
                result = {
                    "success": True,
                    "transactions": transactions,
                    "count": len(transactions),
                    "saved": saved,
                    "skipped": skipped,
                    "pos_count": len(pos_txs_total),
                    "pos_saved": pos_saved,
                    "pos_skipped": pos_skipped,
                    "period": {
                        "start": start_dt.strftime("%Y-%m-%d"),
                        "end":   end_dt.strftime("%Y-%m-%d"),
                    }
                }
                flash(f"Banka: {len(transactions)} ({saved} ek, {skipped} atl). POS: {len(pos_txs_total)} ({pos_saved} ek, {pos_skipped} atl).", "success")

    return render_template("womsis.html",
                           bilgi=bilgi,
                           result=result,
                           default_url=DEFAULT_API_URL,
                           today=datetime.now().strftime("%Y-%m-%d"),
                           month_ago=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))


# ────────────────────────────────────────────────────────────────────────────
# AJAX / JSON endpoint'ler (UI)
# ────────────────────────────────────────────────────────────────────────────

@app.route("/ajax/db-test")
@login_required
def ajax_db_test():
    return jsonify(test_connection())


@app.route("/ajax/womsis-test", methods=["POST"])
@login_required
def ajax_womsis_test():
    body   = request.get_json(silent=True) or {}
    userid = session["user_id"]
    bilgi  = get_vomsis_bilgileri(userid)
    appkey = body.get("appkey") or bilgi.get("appkey", "")
    seckey = body.get("seckey") or bilgi.get("seckey", "")
    url    = body.get("url")    or bilgi.get("url", DEFAULT_API_URL)
    return jsonify(vomsis_test_connection(url, appkey, seckey))


# ── API Key bilgisi ───────────────────────────────────────────────────────────
@app.route("/settings")
@login_required
def settings():
    if session.get("yetki") != "superadmin":
        flash("Bu sayfaya erişim yetkiniz yok.", "danger")
        return redirect(url_for("dashboard"))
    return render_template("settings.html",
                           api_key=WEBADMIN_API_KEY,
                           db_status=test_connection())


# ────────────────────────────────────────────────────────────────────────────
# ZAMANLAYICI YÖNETİMİ  (Otomatik Womsis Sync)
# ────────────────────────────────────────────────────────────────────────────

@app.route("/scheduler", methods=["GET", "POST"])
@login_required
def scheduler():
    """Otomatik Womsis sync zamanlayıcısını yönetir."""
    if request.method == "POST":
        action = request.form.get("action")

        if action == "set_schedule":
            try:
                hour   = int(request.form.get("hour",   0))
                minute = int(request.form.get("minute", 0))
                hour   = max(0, min(23, hour))
                minute = max(0, min(59, minute))
                stop_scheduler()                        # Önce durdur
                import time; time.sleep(0.2)            # Thread'in durmasını bekle
                start_scheduler(hour=hour, minute=minute)  # Yeni saatle başlat
                flash(
                    f"✅  Zamanlayıcı ayarlandı: her gün {hour:02d}:{minute:02d}'de çalışacak.",
                    "success"
                )
            except (ValueError, TypeError):
                flash("Geçersiz saat/dakika değeri.", "danger")

        elif action == "run_now":
            import threading
            t = threading.Thread(target=run_womsis_sync_job, daemon=True)
            t.start()
            flash("⚡  Womsis sync başlatıldı — arka planda çalışıyor.", "success")

        return redirect(url_for("scheduler"))

    state = get_scheduler_state()
    logs  = get_sync_logs(limit=50)
    return render_template("scheduler.html", state=state, logs=logs)


# ── Context processor ─────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return {
        "current_user": {
            "id":       session.get("user_id"),
            "username": session.get("username"),
            "yetki":    session.get("yetki"),
            "firmaadi": session.get("firmaadi"),
        },
        "now": datetime.now(),
    }


# ── Hata sayfaları ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def page_not_found(e):
    return render_template("base.html"), 404


# ── Başlat ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from pathlib import Path

    cert_file = Path(__file__).parent / "cert.pem"
    key_file  = Path(__file__).parent / "key.pem"

    ssl_context = None
    if cert_file.exists() and key_file.exists():
        ssl_context = (str(cert_file), str(key_file))
        proto = "https"
        logger.info(f"🔒  SSL modu aktif — cert: {cert_file.name}, key: {key_file.name}")
    else:
        proto = "http"
        logger.info("ℹ️   SSL sertifikası bulunamadı — HTTP modunda başlatılıyor.")
        logger.info("    HTTPS için nakitAkim → Eklentiler → webadmin Ayarları → Sertifika Oluştur")

    # ── Otomatik Womsis Zamanlayıcısı — gece 00:00'da başlar ─────────────────
    # Saati değiştirmek için webadmin → Zamanlayıcı sayfasını kullanın.
    # Veya burada start_scheduler(hour=X, minute=Y) ile override edebilirsiniz.
    start_scheduler(hour=0, minute=0)

    logger.info(f"webadmin-nakitAkim başlatılıyor → {proto}://{HOST}:{PORT}")
    app.run(
        host=HOST,
        port=PORT,
        debug=DEBUG,
        ssl_context=ssl_context,
    )
