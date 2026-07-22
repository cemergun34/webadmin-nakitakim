# -*- coding: utf-8 -*-
"""
webadmin-nakitAkim — Otomatik Womsis Zamanlayıcısı
===================================================
APScheduler kullanarak her gece belirlenen saatte (varsayılan 00:00)
tüm kullanıcıların Womsis verilerini otomatik çeker ve sonuçları
womsis_sync_log tablosuna kaydeder.

Tablo (otomatik oluşturulur):
    womsis_sync_log(id, userid, tarih, durum, mesaj, cekilen, kayit_zamani)

Kullanım (app.py içinde):
    from services.scheduler_service import start_scheduler
    start_scheduler(app)
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ── Zamanlayıcı Durumu (bellekte tutulur) ────────────────────────────────────
_scheduler_state: dict = {
    "running":      False,
    "hour":         0,       # varsayılan gece 00:00
    "minute":       0,
    "last_run":     None,    # datetime | None
    "last_status":  None,    # "success" | "error" | None
    "last_message": "",
    "next_run":     None,    # datetime | None
}

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()


# ── DB Yardımcısı ─────────────────────────────────────────────────────────────

def _ensure_log_table():
    """womsis_sync_log tablosunu yoksa oluşturur."""
    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS womsis_sync_log (
                    id           SERIAL PRIMARY KEY,
                    userid       INTEGER NOT NULL,
                    tarih        VARCHAR(20),
                    durum        VARCHAR(20),
                    mesaj        TEXT,
                    cekilen      INTEGER DEFAULT 0,
                    kayit_zamani TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("womsis_sync_log tablo kontrolü başarısız: %s", e)


def _log_to_db(userid: int, tarih: str, durum: str, mesaj: str, cekilen: int = 0):
    """Sync sonucunu DB'ye yazar (hata olursa sessizce atlar)."""
    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO womsis_sync_log
                   (userid, tarih, durum, mesaj, cekilen)
                   VALUES (%s, %s, %s, %s, %s)""",
                (userid, tarih, durum, mesaj, cekilen)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.error("womsis_sync_log yazma hatası: %s", e)


def get_sync_logs(limit: int = 50) -> list[dict]:
    """Son sync loglarını döner (webadmin UI için)."""
    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT id, userid, tarih, durum, mesaj, cekilen, kayit_zamani
                   FROM womsis_sync_log
                   ORDER BY kayit_zamani DESC
                   LIMIT %s""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("womsis_sync_log okuma hatası: %s", e)
        return []


# ── Tek Kullanıcı İçin Sync ──────────────────────────────────────────────────

def _sync_user(userid: int, start_dt: datetime, end_dt: datetime) -> dict:
    """
    Bir kullanıcı için Womsis verilerini çeker.
    Döner: {"success": bool, "count": int, "message": str}
    """
    from services.vomsis_service import (
        get_vomsis_bilgileri, vomsis_authenticate,
        vomsis_get_all_transactions_chunked
    )

    bilgi = get_vomsis_bilgileri(userid)
    if not bilgi.get("appkey") or not bilgi.get("seckey"):
        return {"success": False, "count": 0,
                "message": "Womsis API bilgileri tanımlı değil."}

    api_url = bilgi.get("url", "https://developers.vomsis.com/api/v2")
    token, err = vomsis_authenticate(api_url, bilgi["appkey"], bilgi["seckey"])
    if not token:
        return {"success": False, "count": 0,
                "message": f"Token alınamadı: {err}"}

    try:
        txs = vomsis_get_all_transactions_chunked(api_url, token, start_dt, end_dt)
        
        # ── DB'ye kaydet (womsis_banka) ──────────────────────────────────────
        # Her zaman userid=1 ve musterino=1 olarak kaydediyoruz.
        saved, skipped = _save_womsis_to_db(txs, userid=1, musterino=1)
        
        return {
            "success": True,
            "count":   len(txs),
            "message": f"{len(txs)} işlem çekildi. ({saved} yeni eklendi, {skipped} atlandı)",
            "data":    txs,
        }
    except Exception as e:
        return {"success": False, "count": 0, "message": str(e)}

def _save_womsis_to_db(transactions: list, userid: int = 1, musterino: int = 1) -> tuple[int, int]:
    """
    Womsis API'den gelen işlemleri womsis_banka tablosuna kaydeder.
    Aynı womsiskey varsa atlar (mükerrer kayıt önleme).
    """
    if not transactions:
        return 0, 0

    saved   = 0
    skipped = 0
    now     = datetime.now()

    try:
        from db.connection import get_connection
        conn = get_connection()
        cur  = conn.cursor()

        for tx in transactions:
            account_id = str(tx.get('accountId') or tx.get('account_id') or '')
            tx_id      = str(tx.get('id') or tx.get('transactionId') or '')
            womsiskey  = f"{account_id}_{tx_id}" if account_id and tx_id else ''

            raw_tarih = str(tx.get('date') or tx.get('transactionDate') or tx.get('valueDate') or '')
            tarih_iso = None
            for fmt in ('%Y-%m-%d', '%d-%m-%Y %H:%M:%S', '%d-%m-%Y', '%Y-%m-%dT%H:%M:%S'):
                try:
                    tarih_iso = datetime.strptime(raw_tarih[:len(fmt)], fmt).strftime('%Y-%m-%d')
                    break
                except Exception:
                    continue
            if not tarih_iso:
                tarih_iso = now.strftime('%Y-%m-%d')

            tutar_raw    = tx.get('amount') or tx.get('tutar') or 0
            tutar        = abs(float(tutar_raw))
            debit        = float(tx.get('debit')  or 0)
            credit       = float(tx.get('credit') or 0)
            if credit > 0 and debit == 0:
                gelirgider = 'gelir'
            elif debit > 0 and credit == 0:
                gelirgider = 'gider'
            else:
                gelirgider = 'gelir' if float(tutar_raw) >= 0 else 'gider'

            aciklama  = str(tx.get('description') or tx.get('aciklama') or '')[:255]
            sube      = str(tx.get('accountName') or tx.get('bankName') or tx.get('sube') or '')
            iban      = str(tx.get('iban') or '')
            bakiye    = float(tx.get('balance') or tx.get('bakiye') or 0)
            hesap_turu= str(tx.get('currency') or tx.get('hesap_turu') or 'TL')
            dekont_no = str(tx.get('referenceNo') or tx.get('dekont_no') or '')

            if womsiskey:
                cur.execute(
                    'SELECT id FROM womsis_banka WHERE womsiskey = %s AND userid = %s LIMIT 1',
                    (womsiskey, userid)
                )
                if cur.fetchone():
                    skipped += 1
                    continue

            cur.execute("""
                INSERT INTO womsis_banka
                    (userid, musterino, tarih, aciklama, gelirgider, tutar,
                     sube, faturaunvan, womsiskey, kaynak,
                     created_at, bakiye, iban, hesap_turu, dekont_no)
                VALUES
                    (%s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s,
                     %s, %s, %s, %s, %s)
            """, (
                userid, musterino, tarih_iso, aciklama, gelirgider, tutar,
                sube, '-', womsiskey, 'womsis_scheduler',
                now, bakiye, iban, hesap_turu, dekont_no
            ))
            saved += 1

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error('womsis DB kayit hatasi: %s', e, exc_info=True)
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass

    return saved, skipped


def _get_all_userids() -> list[int]:
    """Womsis bilgisi tanımlı tüm kullanıcıları döner."""
    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT userid FROM vomsisbilgileri "
                "WHERE appkey IS NOT NULL AND appkey != '' "
                "AND seckey IS NOT NULL AND seckey != ''"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error("Kullanıcı listesi alınamadı: %s", e)
        return []


# ── Ana Job Fonksiyonu ────────────────────────────────────────────────────────

def run_womsis_sync_job():
    """
    Tüm kullanıcılar için Womsis verisini çeker.
    Scheduler tarafından veya manuel tetiklendiğinde çağrılır.
    """
    now = datetime.now()
    tarih_str = now.strftime("%Y-%m-%d %H:%M")
    logger.info("🕛  Otomatik Womsis sync başlıyor — %s", tarih_str)

    _scheduler_state["last_run"] = now
    _scheduler_state["last_status"] = "running"
    _scheduler_state["last_message"] = "İşlem devam ediyor..."

    # Baştan sona çek: 2026-01-01'den bugüne
    end_dt   = now.replace(hour=23, minute=59, second=59)
    start_dt = datetime(2026, 1, 1, 0, 0, 0)

    userids = _get_all_userids()
    if not userids:
        msg = "Womsis tanımlı kullanıcı bulunamadı."
        logger.warning(msg)
        _scheduler_state["last_status"]  = "warning"
        _scheduler_state["last_message"] = msg
        return

    logger.info("  %d kullanıcı işlenecek: %s", len(userids), userids)

    total_fetched = 0
    errors        = []

    for uid in userids:
        result = _sync_user(uid, start_dt, end_dt)
        cnt    = result.get("count", 0)
        total_fetched += cnt

        durum = "success" if result["success"] else "error"
        _log_to_db(uid, tarih_str, durum, result["message"], cnt)

        if result["success"]:
            logger.info("  ✅  userid=%d → %d işlem", uid, cnt)
        else:
            logger.error("  ❌  userid=%d → %s", uid, result["message"])
            errors.append(f"userid={uid}: {result['message']}")

    if errors:
        final_msg = f"{total_fetched} çekildi, hatalar: {'; '.join(errors)}"
        _scheduler_state["last_status"] = "partial"
    else:
        final_msg = f"{len(userids)} kullanıcı, toplam {total_fetched} işlem çekildi."
        _scheduler_state["last_status"] = "success"

    _scheduler_state["last_message"] = final_msg
    logger.info("✅  Otomatik sync tamamlandı — %s", final_msg)


# ── Zamanlayıcı Thread ───────────────────────────────────────────────────────

def _scheduler_loop(hour: int, minute: int):
    """
    Arka planda sonsuz döngü çalışır.
    Her gün belirlenen saat:dakikada job'u tetikler.
    """
    logger.info("🕐  Womsis zamanlayıcısı aktif — her gün %02d:%02d'de çalışacak.", hour, minute)

    while not _stop_event.is_set():
        now = datetime.now()
        # Bir sonraki çalışma zamanını hesapla
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        _scheduler_state["next_run"] = next_run
        wait_seconds = (next_run - now).total_seconds()

        logger.info("  ⏰  Sonraki sync: %s (%.0f saniye sonra)",
                    next_run.strftime("%d.%m.%Y %H:%M"), wait_seconds)

        # Bekleme: her 60 saniyede bir stop_event kontrol edilir
        remaining = wait_seconds
        while remaining > 0 and not _stop_event.is_set():
            sleep_for = min(60, remaining)
            _stop_event.wait(sleep_for)
            remaining -= sleep_for

        if _stop_event.is_set():
            break

        # Job'u çalıştır
        try:
            run_womsis_sync_job()
        except Exception as e:
            logger.error("Scheduler job hatası: %s", e, exc_info=True)
            _scheduler_state["last_status"]  = "error"
            _scheduler_state["last_message"] = str(e)

    logger.info("🛑  Womsis zamanlayıcısı durduruldu.")
    _scheduler_state["running"] = False


# ── Dışarıya Açık API ─────────────────────────────────────────────────────────

def start_scheduler(hour: int = 0, minute: int = 0):
    """
    Zamanlayıcı thread'ini başlatır.
    app.py içinde çağrılır:
        from services.scheduler_service import start_scheduler
        start_scheduler(hour=0, minute=0)   # gece 00:00

    hour   : 0–23
    minute : 0–59
    """
    global _scheduler_thread

    _ensure_log_table()

    if _scheduler_state["running"]:
        logger.warning("Zamanlayıcı zaten çalışıyor, tekrar başlatılmadı.")
        return

    _stop_event.clear()
    _scheduler_state["running"] = True
    _scheduler_state["hour"]    = hour
    _scheduler_state["minute"]  = minute

    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(hour, minute),
        daemon=True,          # Ana process kapanınca otomatik durur
        name="WomsisScheduler"
    )
    _scheduler_thread.start()


def stop_scheduler():
    """Zamanlayıcıyı durdurur (genellikle app kapatılırken)."""
    _stop_event.set()
    _scheduler_state["running"] = False


def get_scheduler_state() -> dict:
    """Mevcut zamanlayıcı durumunu döner (webadmin UI için)."""
    state = dict(_scheduler_state)
    # datetime nesnelerini string'e çevir (JSON uyumluluğu)
    for key in ("last_run", "next_run"):
        if isinstance(state.get(key), datetime):
            state[key] = state[key].strftime("%d.%m.%Y %H:%M:%S")
    return state
