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
from typing import Optional

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


def _log_to_db(userid: int, musterino: int, tarih: str, durum: str, mesaj: str, cekilen: int = 0):
    """Sync sonucunu DB'ye yazar (hata olursa sessizce atlar)."""
    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            # Not: womsis_sync_log tablosunda musterino kolonu yoksa hatayı yakalar, 
            # ancak biz bu tabloya şimdilik sadece userid yazıyoruz.
            # İleride musterino eklenebilir. Şimdilik userid kaydediyoruz.
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

def _sync_account(userid: int, musterino: int, start_dt: datetime, end_dt: datetime) -> dict:
    """
    Bir hesap (userid + musterino) için Womsis verilerini çeker.
    Döner: {"success": bool, "count": int, "message": str}
    """
    from services.vomsis_service import (
        get_vomsis_bilgileri, vomsis_authenticate,
        vomsis_get_accounts, vomsis_get_account_transactions
    )

    bilgi = get_vomsis_bilgileri(userid, musterino)
    if not bilgi.get("appkey") or not bilgi.get("seckey"):
        return {"success": False, "count": 0,
                "message": "Womsis API bilgileri tanımlı değil."}

    api_url = bilgi.get("url", "https://developers.vomsis.com/api/v2")
    token, err = vomsis_authenticate(api_url, bilgi["appkey"], bilgi["seckey"])
    if not token:
        return {"success": False, "count": 0,
                "message": f"Token alınamadı: {err}"}

    try:
        # PHP topluWomIsle.php: her hesap için ayrı ayrı /accounts/{id}/transactions çağrısı
        # Bu yöntem tx.account nesnesini (branch_name, bank_id) doğru döndürür
        accounts = vomsis_get_accounts(api_url, token)
        txs = []
        for acc in accounts:
            acc_id = acc.get('id')
            if not acc_id:
                continue
            current = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            while current < end_dt:
                chunk_end = min(current + timedelta(days=6), end_dt)
                chunk_end = chunk_end.replace(hour=23, minute=59, second=59)
                begin_str = current.strftime("%d-%m-%Y %H:%M:%S")
                end_str   = chunk_end.strftime("%d-%m-%Y %H:%M:%S")
                chunk_txs = vomsis_get_account_transactions(api_url, token, acc_id, begin_str, end_str)
                txs.extend(chunk_txs)
                logger.debug("Hesap %s chunk %s→%s: %d işlem", acc_id, begin_str[:10], end_str[:10], len(chunk_txs))
                current = (current + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        logger.info("Banka çekim: %d hesap, toplam %d işlem", len(accounts), len(txs))
        
        # ── DB'ye kaydet (womsis_banka) ──────────────────────────────────────
        saved, skipped = _save_womsis_to_db(txs, userid=userid, musterino=musterino)
        
        # ── POS Verilerini Çek ve Kaydet ─────────────────────────────────────
        # PHP womsisPosIsle.php ile birebir aynı mantık:
        #   - /pos-rapor/stations → terminal listesi
        #   - Her station için: id, station_no, workplace_no, bank_title alınır
        #   - /pos-rapor/stations/{id}/transactions?beginDate=DD-MM-YYYY&endDate=DD-MM-YYYY
        #   - 14 günlük parçalara bölünür
        from services.vomsis_service import (
            vomsis_get_terminals, vomsis_get_terminal_transactions,
            vomsis_get_pos_transactions_direct
        )
        CHUNK_DAYS = 14
        pos_txs_total = []
        pos_saved = 0
        pos_skipped = 0

        terminals = vomsis_get_terminals(api_url, token)
        if terminals:
            for station in terminals:
                # PHP: $stationId, $stationNo, $workplaceNo, $bankTitle
                station_id   = station.get("id")           or station.get("stationId")  or station.get("terminalId")
                station_no   = station.get("station_no")   or station.get("stationNo")  or str(station_id or "")
                workplace_no = station.get("workplace_no") or station.get("workplaceNo") or station.get("merchantNo") or ""
                bank_title   = station.get("bank_title")   or station.get("bank_name")  or station.get("bankTitle")  or ""

                if not station_id:
                    continue

                # 14 günlük parçalar halinde çek (PHP CHUNK_DAYS=14)
                current_start = start_dt
                while current_start <= end_dt:
                    current_end = current_start + timedelta(days=CHUNK_DAYS - 1)
                    if current_end > end_dt:
                        current_end = end_dt
                    # PHP: 'd-m-Y' formatı (saat YOK)
                    b_str = current_start.strftime("%d-%m-%Y")
                    e_str = current_end.strftime("%d-%m-%Y")
                    try:
                        term_txs = vomsis_get_terminal_transactions(api_url, token, station_id, b_str, e_str)
                        if term_txs:
                            # Her işleme station bilgilerini ekle (PHP'de $workplaceNo, $bankTitle doğrudan kullanılıyor)
                            for tx in term_txs:
                                tx.setdefault('_station_no',   station_no)
                                tx.setdefault('_workplace_no', workplace_no)
                                tx.setdefault('_bank_title',   bank_title)
                            pos_txs_total.extend(term_txs)
                            ps, psk = _save_womsis_pos_to_db(
                                term_txs, station_no,
                                userid=userid, musterino=musterino
                            )
                            pos_saved += ps
                            pos_skipped += psk
                            logger.info("POS terminal=%s (%s→%s): %d işlem, %d kaydedildi",
                                        station_id, b_str, e_str, len(term_txs), ps)
                    except Exception as te:
                        logger.warning("Terminal %s (%s→%s) hatası: %s", station_id, b_str, e_str, te)

                    current_start = current_end + timedelta(days=1)
        else:
            # Terminal tabanlı endpoint boş — direkt POS endpoint dene
            logger.info("Terminal listesi boş, direct POS endpoint deneniyor...")
            current_start = start_dt
            while current_start <= end_dt:
                current_end = current_start + timedelta(days=CHUNK_DAYS - 1)
                if current_end > end_dt:
                    current_end = end_dt
                b_str = current_start.strftime("%d-%m-%Y")
                e_str = current_end.strftime("%d-%m-%Y")
                try:
                    direct_txs = vomsis_get_pos_transactions_direct(api_url, token, b_str, e_str)
                    if direct_txs:
                        pos_txs_total.extend(direct_txs)
                        ps, psk = _save_womsis_pos_to_db(direct_txs, '', userid=userid, musterino=musterino)
                        pos_saved += ps
                        pos_skipped += psk
                except Exception as te:
                    logger.warning("Direct POS (%s→%s) hatası: %s", b_str, e_str, te)

                current_start = current_end + timedelta(days=1)

        return {
            "success": True,
            "count":   len(txs),
            "pos_count": len(pos_txs_total),
            "message": f"Banka: {len(txs)} ({saved} ek, {skipped} atl). POS: {len(pos_txs_total)} ({pos_saved} ek, {pos_skipped} atl).",
            "data":    txs,
            "pos_data": pos_txs_total
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
    conn    = None   # UnboundLocalError'u önlemek için önceden None yap

    try:
        from db.connection import get_connection
        conn = get_connection()
        cur  = conn.cursor()

        # PHP topluWomIsle.php banka ID → isim eşlemesi (bankConfig)
        BANK_ID_MAP = {
            19: 'Ziraat Bankası',
            20: 'İş Bankası',
            21: 'Yapı Kredi',
            22: 'Enpara',
            23: 'Vakıf Katılım',
        }

        for tx in transactions:
            # ── womsiskey: PHP $trx['key'] kullanır (id değil!) ──────────────
            womsiskey = str(tx.get('key') or '')
            if not womsiskey:
                # Fallback: account_id + transaction_id
                account_id = str(tx.get('accountId') or tx.get('account_id') or '')
                tx_id      = str(tx.get('id') or tx.get('transactionId') or '')
                womsiskey  = f"{account_id}_{tx_id}" if account_id and tx_id else ''

            # ── Tarih: PHP $trx['system_date'] ?? $trx['accounting_date'] ────
            raw_tarih = str(
                tx.get('system_date') or tx.get('accounting_date') or
                tx.get('date') or tx.get('transactionDate') or tx.get('valueDate') or ''
            )
            tarih_iso = None
            s = raw_tarih.strip()
            if "T" in s and "." in s:
                s = s.split(".")[0]
            for fmt in (
                '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
                '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%d.%m.%Y',
                '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
                '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M', '%d-%m-%Y',
                '%Y-%m-%dT%H:%M:%S'
            ):
                try:
                    tarih_iso = datetime.strptime(s, fmt).strftime('%Y-%m-%d')
                    break
                except Exception:
                    continue
            if not tarih_iso:
                tarih_iso = now.strftime('%Y-%m-%d')

            tutar_raw = tx.get('amount') or tx.get('tutar') or 0
            tutar     = abs(float(tutar_raw))

            # ── gelirgider: PHP $trx['type'] → 'borclu'/'alacakli' ───────────
            # PHP banka perspektifinden çevirir:
            #   'borclu'   = banka sizi borçlu sayar = para GİRİŞİ = gelir
            #   'alacakli' = banka sizden alacaklı   = para ÇIKIŞI = gider
            tx_type = str(tx.get('type') or '').lower().strip()
            if tx_type == 'borclu':
                gelirgider = 'gelir'
            elif tx_type == 'alacakli':
                gelirgider = 'gider'
            else:
                # Fallback: debit/credit alanları veya amount işareti
                debit  = float(tx.get('debit')  or 0)
                credit = float(tx.get('credit') or 0)
                if credit > 0 and debit == 0:
                    gelirgider = 'gelir'
                elif debit > 0 and credit == 0:
                    gelirgider = 'gider'
                else:
                    gelirgider = 'gelir' if float(tutar_raw) >= 0 else 'gider'

            aciklama = str(tx.get('description') or tx.get('aciklama') or '')[:255]

            # ── Şube: PHP $account['bank_id'] → bankConfig lookup ─────────────
            # tx.account = { bank_id, branch_name, iban, bank:{bank_title}, ... }
            _account_obj = tx.get('account') or {}
            if isinstance(_account_obj, dict):
                _bank_id   = _account_obj.get('bank_id') or tx.get('bank_id')
                _bank_obj  = _account_obj.get('bank') or {}
                _bank_title = ''
                # 1. bank_id → BANK_ID_MAP (PHP öncelik sırası)
                if _bank_id:
                    try:
                        _bank_title = BANK_ID_MAP.get(int(_bank_id), '')
                    except Exception:
                        pass
                # 2. account.bank.bank_title (nested)
                if not _bank_title and isinstance(_bank_obj, dict):
                    _bank_title = _bank_obj.get('bank_title') or _bank_obj.get('bank_name') or ''

                _branch_name = _account_obj.get('branch_name') or ''
                _acc_no      = _account_obj.get('formatted_account_number') or _account_obj.get('account_number') or ''
                _acc_iban    = _account_obj.get('iban') or ''

                if _branch_name and _bank_title:
                    sube = f"{_bank_title} - {_branch_name}"
                elif _branch_name:
                    sube = _branch_name
                elif _bank_title:
                    sube = _bank_title
                else:
                    sube = str(tx.get('accountName') or tx.get('bankName') or tx.get('sube') or '')
            else:
                _acc_iban = ''
                sube = str(tx.get('accountName') or tx.get('bankName') or tx.get('sube') or '')

            # ── IBAN: PHP $trx['opponent_iban'] ──────────────────────────────
            iban      = str(tx.get('opponent_iban') or tx.get('iban') or _acc_iban or '')
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
    except Exception as e:
        logger.error('womsis DB kayit hatasi: %s', e, exc_info=True)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        # Baglanti her durumda kapatilir — baglanti sizintisini onler
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return saved, skipped


def _save_womsis_pos_to_db(transactions: list, posno_fallback: str, userid: int = 1, musterino: int = 1) -> tuple[int, int]:
    """
    Womsis POS işlemlerini womsi_pos tablosuna kaydeder.
    Mükerrer kayıt engellemek için kontrol yapar.
    """
    if not transactions:
        return 0, 0

    saved   = 0
    skipped = 0
    now     = datetime.now()
    conn    = None   # UnboundLocalError'u önlemek için önceden None yap

    try:
        from db.connection import get_connection
        conn = get_connection()
        cur  = conn.cursor()

        for tx in transactions:
            # ── Tarih ─────────────────────────────────────────────────────────
            # PHP: $tx['date']
            raw_tarih = str(tx.get('date') or tx.get('transactionDate') or '')
            tarih_iso = None
            s = raw_tarih.strip()
            if "T" in s and "." in s:
                s = s.split(".")[0]
            for fmt in (
                '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
                '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%d.%m.%Y',
                '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
                '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M', '%d-%m-%Y',
                '%Y-%m-%dT%H:%M:%S'
            ):
                try:
                    tarih_iso = datetime.strptime(s, fmt).strftime('%Y-%m-%d %H:%M:%S')
                    break
                except Exception:
                    continue
            if not tarih_iso:
                tarih_iso = now.strftime('%Y-%m-%d %H:%M:%S')

            # ── Hesaba Geçiş Tarihi ───────────────────────────────────────────
            # PHP: $tx['valor'] ?? $tx['transfer_to_account_date']
            hesaba_gecis = str(tx.get('valor') or tx.get('transfer_to_account_date') or
                               tx.get('settlementDate') or tx.get('valueDate') or '')
            if hesaba_gecis:
                s = hesaba_gecis.strip()
                if "T" in s and "." in s:
                    s = s.split(".")[0]
                for fmt in (
                    '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
                    '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%d.%m.%Y',
                    '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
                    '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M', '%d-%m-%Y',
                    '%Y-%m-%dT%H:%M:%S'
                ):
                    try:
                        hesaba_gecis = datetime.strptime(s, fmt).strftime('%Y-%m-%d')
                        break
                    except Exception:
                        pass

            # ── Rakamlar ─────────────────────────────────────────────────────
            # PHP: (float)($tx['gross_amount'] ?? 0)
            islemtutari  = float(str(tx.get('gross_amount') or tx.get('amount') or 0).replace(",", "."))
            # PHP: (float)($tx['commission'] ?? 0)
            isyeriucreti = float(str(tx.get('commission') or tx.get('commissionAmount') or 0).replace(",", "."))
            # PHP: (float)($tx['net_amount'] ?? 0)
            nettutar     = float(str(tx.get('net_amount') or tx.get('netAmount') or 0).replace(",", "."))

            # ── Kart ve POS bilgileri ─────────────────────────────────────────
            # PHP: $tx['station'] ?? $stationNo  (station nesnesinden gelen)
            posno     = str(tx.get('station') or tx.get('_station_no') or posno_fallback or '')
            # PHP: $tx['sub_card_type'] ?? $tx['card_type']
            brand     = str(tx.get('sub_card_type') or tx.get('card_type') or '')
            # PHP: $tx['card_number']
            kartno    = str(tx.get('card_number') or tx.get('maskedCardNumber') or tx.get('maskedCardNo') or '')
            # PHP: $tx['transaction_type']
            islemtipi = str(tx.get('transaction_type') or tx.get('type') or '')
            # PHP: $workplaceNo ?: ($tx['workplace'] ?? '')  (station'dan öncelikli)
            isyerino  = str(tx.get('_workplace_no') or tx.get('workplace') or tx.get('workplaceNo') or '')
            aciklama  = str(tx.get('description') or '')[:255]
            # PHP: $bankTitle  (station'dan)
            carihesap = str(tx.get('_bank_title') or tx.get('bank_title') or '-')

            # PHP: date('d/m/Y')
            islemtarih_str = now.strftime('%d/%m/%Y')

            # ── Mükerrer kontrolü ─────────────────────────────────────────────
            tx_id = str(tx.get('id') or tx.get('transactionId') or tx.get('transaction_id') or '')
            if tx_id:
                cur.execute('SELECT id FROM womsi_pos WHERE kayittarihi = %s AND userid = %s LIMIT 1', (tx_id, userid))
                if cur.fetchone():
                    skipped += 1
                    continue
            else:
                cur.execute(
                    'SELECT id FROM womsi_pos WHERE userid=%s AND islemtarihi=%s AND islemtutari=%s AND kartno=%s AND posno=%s LIMIT 1',
                    (userid, tarih_iso, islemtutari, kartno, posno)
                )
                if cur.fetchone():
                    skipped += 1
                    continue

            cur.execute("""
                INSERT INTO womsi_pos
                    (userid, islemtutari, isyeriucretitutar, nettutar, musterino,
                     islemtarihi, posno, kayittarihi, islemtarih, brand,
                     kartno, islemtipi, isyerino, carihesap, hesabagecistarihi, aciklama)
                VALUES
                    (%s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s)
            """, (
                userid, islemtutari, isyeriucreti, nettutar, musterino,
                tarih_iso, posno, tx_id, islemtarih_str, brand,
                kartno, islemtipi, isyerino, carihesap, hesaba_gecis, aciklama
            ))
            saved += 1

        conn.commit()
        cur.close()
    except Exception as e:
        logger.error('womsi_pos DB kayit hatasi: %s', e, exc_info=True)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        # Baglanti her durumda kapatilir — baglanti sizintisini onler
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return saved, skipped


def _get_all_womsis_accounts() -> list[tuple[int, int]]:
    """Womsis bilgisi tanımlı tüm (userid, musterino) çiftlerini döner."""
    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT userid, musterino FROM vomsisbilgileri "
                "WHERE appkey IS NOT NULL AND appkey != '' "
                "AND seckey IS NOT NULL AND seckey != ''"
            ).fetchall()
            return [(r[0], r[1]) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error("Kullanıcı listesi alınamadı: %s", e)
        return []


# ── Ana Job Fonksiyonu ────────────────────────────────────────────────────────

def run_womsis_sync_job(start_dt: Optional[datetime] = None, end_dt: Optional[datetime] = None):
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

    # Varsayılan: 2026-01-01'den bugüne
    if not end_dt:
        end_dt = now.replace(hour=23, minute=59, second=59)
    if not start_dt:
        start_dt = datetime(2026, 1, 1, 0, 0, 0)

    accounts = _get_all_womsis_accounts()
    if not accounts:
        msg = "Womsis tanımlı hesap bulunamadı."
        logger.warning(msg)
        _scheduler_state["last_status"]  = "warning"
        _scheduler_state["last_message"] = msg
        return

    logger.info("  %d Womsis hesabı işlenecek: %s", len(accounts), accounts)

    total_fetched = 0
    errors        = []

    for uid, mus in accounts:
        result = _sync_account(uid, mus, start_dt, end_dt)
        cnt    = result.get("count", 0)
        total_fetched += cnt

        durum = "success" if result["success"] else "error"
        _log_to_db(uid, mus, tarih_str, durum, result["message"], cnt)

        if result["success"]:
            logger.info("  ✅  userid=%d, musterino=%d → %d işlem", uid, mus, cnt)
        else:
            logger.error("  ❌  userid=%d, musterino=%d → %s", uid, mus, result["message"])
            errors.append(f"uid={uid}/mus={mus}: {result['message']}")

    if errors:
        final_msg = f"{total_fetched} çekildi, hatalar: {'; '.join(errors)}"
        _scheduler_state["last_status"] = "partial"
    else:
        final_msg = f"{len(accounts)} hesap, toplam {total_fetched} işlem çekildi."
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
