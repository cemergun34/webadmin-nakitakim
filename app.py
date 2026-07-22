"""
webadmin-nakitakim Flask Uygulamasi
Port: 5050 (WEBADMIN_PORT env ile degistirilebilir)
"""
import os
import hashlib
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, request, jsonify, session,
    render_template_string, redirect, url_for
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('webadmin')

# ── Config (ortam degiskenlerinden oku) ───────────────────────────────────────
SECRET_KEY = os.environ.get('WEBADMIN_SECRET_KEY', 'fallback-secret-key-change-me')
DEBUG      = os.environ.get('WEBADMIN_DEBUG', 'false').lower() == 'true'
PORT       = int(os.environ.get('WEBADMIN_PORT', 5050))
HOST       = os.environ.get('WEBADMIN_HOST', '0.0.0.0')
API_KEY    = os.environ.get('WEBADMIN_API_KEY', 'nakit-akim-api-key-2024-secure')

PG_HOST    = os.environ.get('PG_HOST', '127.0.0.1')
PG_PORT    = int(os.environ.get('PG_PORT', 5432))
PG_DB      = os.environ.get('PG_DB', 'neondb')
PG_USER    = os.environ.get('PG_USER', 'postgres')
PG_PASS    = os.environ.get('PG_PASS', '123')
PG_SSLMODE = os.environ.get('PG_SSLMODE', 'disable')

# ── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY


# ── DB Baglantisi ─────────────────────────────────────────────────────────────
def get_db():
    import psycopg2
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
        sslmode=PG_SSLMODE,
        connect_timeout=10
    )
    return conn


# ── Dekoratörler ─────────────────────────────────────────────────────────────
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key')
        if key != API_KEY:
            return jsonify({'success': False, 'error': 'Unauthorized - Gecersiz API Key'}), 401
        return f(*args, **kwargs)
    return decorated


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── HTML Sablonlar ────────────────────────────────────────────────────────────
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IQ Finans - Webadmin Giris</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: 'Segoe UI', Arial, sans-serif;
    background: linear-gradient(135deg, #1a1a2e 0%, #0f3460 100%);
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh;
}
.card {
    background: #16213e;
    border: 1px solid #0f3460;
    border-radius: 16px;
    padding: 44px 40px;
    width: 400px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
}
.logo { color: #e94560; font-size: 26px; font-weight: 700; margin-bottom: 4px; }
.sub  { color: #6677aa; font-size: 13px; margin-bottom: 32px; }
label { display: block; color: #9999bb; font-size: 12px; font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
input {
    width: 100%; padding: 12px 16px;
    background: #0a1628; border: 1px solid #1e3a5f;
    border-radius: 8px; color: #fff; font-size: 14px;
    margin-bottom: 20px; outline: none; transition: border 0.2s;
}
input:focus { border-color: #e94560; }
button {
    width: 100%; padding: 13px;
    background: #e94560; border: none; border-radius: 8px;
    color: #fff; font-size: 15px; font-weight: 700;
    cursor: pointer; transition: background 0.2s; letter-spacing: 0.5px;
}
button:hover { background: #c73652; }
.error {
    background: rgba(233,69,96,0.15);
    border: 1px solid #e94560;
    border-radius: 8px; padding: 12px 16px;
    color: #e94560; font-size: 13px; margin-bottom: 20px;
}
</style>
</head>
<body>
<div class="card">
  <div class="logo">IQ Finans</div>
  <div class="sub">Webadmin Yonetim Paneli</div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="post" autocomplete="off">
    <label>Kullanici Adi</label>
    <input type="text" name="username" placeholder="kullanici adi veya e-posta" autofocus required>
    <label>Sifre</label>
    <input type="password" name="password" placeholder="sifreniz" required>
    <button type="submit">GIRIS YAP</button>
  </form>
</div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>IQ Finans - Webadmin</title>
<style>
body { font-family: 'Segoe UI', Arial, sans-serif; background: #1a1a2e; color: #eee; padding: 40px; }
h1   { color: #e94560; margin-bottom: 20px; }
.box { background: #16213e; border: 1px solid #0f3460; border-radius: 10px; padding: 24px; margin-bottom: 16px; }
.key { color: #6677aa; font-size: 12px; }
.val { color: #fff; font-size: 14px; font-weight: 600; margin-top: 2px; }
a    { color: #e94560; text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1>Webadmin Paneli</h1>
<div class="box">
  <div class="key">Hosgeldiniz</div>
  <div class="val">{{ username }}</div>
</div>
<div class="box">
  <div class="key">Sunucu</div>
  <div class="val">http://{{ host }}:{{ port }}</div>
  <div class="key" style="margin-top:12px">Veritabani</div>
  <div class="val">{{ pg_db }} @ {{ pg_host }}</div>
  <div class="key" style="margin-top:12px">API Endpoint</div>
  <div class="val">POST /api/womsis/sync</div>
</div>
<a href="/logout">Cikis Yap</a>
</body>
</html>
"""


# ── Web Route'lari ────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        user = _authenticate(username, password)
        if user:
            session['user_id']   = user['id']
            session['username']  = user['kullanici_adi']
            session['musterino'] = user.get('musterino', 1)
            logger.info('Giris basarili: %s', username)
            return redirect(url_for('dashboard'))
        error = 'Kullanici adi veya sifre yanlis.'
        logger.warning('Basarisiz giris denemesi: %s', username)
    return render_template_string(LOGIN_HTML, error=error)


@app.route('/dashboard')
@login_required
def dashboard():
    return render_template_string(
        DASHBOARD_HTML,
        username=session.get('username', ''),
        host=PG_HOST, port=PORT,
        pg_db=PG_DB, pg_host=PG_HOST
    )


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── API Route'lari ────────────────────────────────────────────────────────────
@app.route('/api/womsis/sync', methods=['POST'])
@require_api_key
def api_womsis_sync():
    try:
        data      = request.get_json() or {}
        userid    = int(data.get('userid', 1))
        musterino = int(data.get('musterino', 1))
        start     = data.get('start_date') or (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        end       = data.get('end_date')   or datetime.now().strftime('%Y-%m-%d')

        creds = _get_womsis_creds(userid)
        if not creds:
            return jsonify({
                'success': False,
                'error_code': 'no_sirket_profili',
                'error': 'Bu kullanici icin Womsis bilgisi tanimli degil.'
            })

        start_dt = datetime.strptime(start, '%Y-%m-%d')
        end_dt   = datetime.strptime(end,   '%Y-%m-%d').replace(hour=23, minute=59, second=59)

        transactions = _fetch_womsis_transactions(
            creds['url'], creds['appkey'], creds['seckey'],
            start_dt, end_dt
        )

        # ── DB'ye kaydet (womsis_banka) ──────────────────────────────────────
        saved, skipped = _save_womsis_to_db(transactions, userid=userid, musterino=musterino)
        logger.info('womsis/sync: %d cekildi, %d kaydedildi, %d atlandı (userid=%d, musterino=%d)',
                    len(transactions), saved, skipped, userid, musterino)

        return jsonify({
            'success':      True,
            'count':        len(transactions),
            'saved':        saved,
            'skipped':      skipped,
            'timestamp':    datetime.now().isoformat(),
            'period':       {'start': start, 'end': end}
        })

    except Exception as e:
        logger.error('womsis/sync hatasi: %s', e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/womsis/test', methods=['POST'])
@require_api_key
def api_womsis_test():
    try:
        data   = request.get_json() or {}
        userid = int(data.get('userid', 1))
        creds  = _get_womsis_creds(userid)
        if not creds:
            return jsonify({'success': False, 'error': 'Womsis bilgisi tanimli degil.'})

        import requests as req
        url  = creds['url'].rstrip('/') + '/authenticate'
        resp = req.post(
            url,
            json={'app_key': creds['appkey'], 'app_secret': creds['seckey']},
            timeout=15
        )
        token = resp.json().get('token')
        if token:
            return jsonify({'success': True, 'message': 'Womsis baglantisi basarili.'})
        return jsonify({'success': False, 'error': 'Token alinamadi.'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/womsis/status', methods=['GET'])
@require_api_key
def api_womsis_status():
    return jsonify({
        'success':   True,
        'status':    'ok',
        'timestamp': datetime.now().isoformat(),
        'server':    f'{HOST}:{PORT}'
    })


@app.route('/api/womsis/accounts', methods=['GET'])
@require_api_key
def api_womsis_accounts():
    try:
        userid = int(request.args.get('userid', 1))
        creds  = _get_womsis_creds(userid)
        if not creds:
            return jsonify({'success': False, 'error': 'Womsis bilgisi tanimli degil.'})

        import requests as req
        auth_url = creds['url'].rstrip('/') + '/authenticate'
        resp  = req.post(auth_url, json={'app_key': creds['appkey'], 'app_secret': creds['seckey']}, timeout=15)
        token = resp.json().get('token')
        if not token:
            return jsonify({'success': False, 'error': 'Token alinamadi.'})

        acc_url  = creds['url'].rstrip('/') + '/accounts'
        aresp    = req.get(acc_url, headers={'Authorization': f'Bearer {token}'}, timeout=20)
        accounts = aresp.json().get('accounts', [])
        return jsonify({'success': True, 'accounts': accounts})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Yardimci Fonksiyonlar ─────────────────────────────────────────────────────
def _authenticate(username: str, password: str):
    """uyelik tablosundan kullanici dogrula — duz metin / MD5 / bcrypt."""
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute(
            """SELECT id, kullanici_adi, sifre, musterino
               FROM uyelik
               WHERE kullanici_adi = %s OR eposta = %s
               LIMIT 1""",
            (username, username)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return None

        uid, uname, stored, musterino = row
        stored = stored or ''

        # 1. Duz metin
        if stored == password:
            return {'id': uid, 'kullanici_adi': uname, 'musterino': musterino or 1}

        # 2. MD5
        if stored == hashlib.md5(password.encode()).hexdigest():
            return {'id': uid, 'kullanici_adi': uname, 'musterino': musterino or 1}

        # 3. Bcrypt ($2y$ PHP uyumu)
        try:
            import bcrypt
            check = stored.replace('$2y$', '$2b$', 1).encode()
            if bcrypt.checkpw(password.encode(), check):
                return {'id': uid, 'kullanici_adi': uname, 'musterino': musterino or 1}
        except Exception:
            pass

        return None

    except Exception as e:
        logger.error('Kimlik dogrulama DB hatasi: %s', e)
        return None


def _get_womsis_creds(userid: int):
    """vomsisbilgileri tablosundan Womsis bilgilerini getir."""
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute(
            "SELECT appkey, seckey, url FROM vomsisbilgileri WHERE userid = %s LIMIT 1",
            (userid,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return {
                'appkey': row[0] or '',
                'seckey': row[1] or '',
                'url':    row[2] or 'https://developers.vomsis.com/api/v2'
            }
        return None
    except Exception as e:
        logger.error('Womsis creds DB hatasi: %s', e)
        return None


def _fetch_womsis_transactions(api_url, app_key, app_secret, start_dt, end_dt):
    """Womsis API'den 7 gunluk parcalar halinde tum islemleri cek."""
    import requests as req
    from urllib.parse import urlencode

    # Token al
    auth_url = api_url.rstrip('/') + '/authenticate'
    resp  = req.post(auth_url, json={'app_key': app_key, 'app_secret': app_secret}, timeout=15)
    token = resp.json().get('token')
    if not token:
        raise ValueError('Womsis token alinamadi: ' + str(resp.json()))

    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    results = []
    current = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    while current < end_dt:
        chunk_end = min(current + timedelta(days=6), end_dt).replace(hour=23, minute=59, second=59)
        params    = urlencode({
            'beginDate': current.strftime('%d-%m-%Y %H:%M:%S'),
            'endDate':   chunk_end.strftime('%d-%m-%Y %H:%M:%S')
        })
        tx_url = f"{api_url.rstrip('/')}/transactions?{params}"
        try:
            r = req.get(tx_url, headers=headers, timeout=30)
            results.extend(r.json().get('transactions', []))
        except Exception as ce:
            logger.warning('Chunk [%s] hatasi: %s', current.date(), ce)
        current = (current + timedelta(days=7)).replace(hour=0, minute=0, second=0)

    return results


def _save_womsis_to_db(transactions: list, userid: int = 1, musterino: int = 1) -> tuple[int, int]:
    """
    Womsis API'den gelen işlemleri womsis_banka tablosuna kaydeder.
    Aynı womsiskey varsa atlar (mükerrer kayıt önleme).
    Returns: (kaydedilen, atlanan)
    """
    if not transactions:
        return 0, 0

    saved   = 0
    skipped = 0
    now     = datetime.now()

    try:
        conn = get_db()
        cur  = conn.cursor()

        for tx in transactions:
            # womsiskey: tekil anahtar — account_id + transaction_id kombinasyonu
            account_id = str(tx.get('accountId') or tx.get('account_id') or '')
            tx_id      = str(tx.get('id') or tx.get('transactionId') or '')
            womsiskey  = f"{account_id}_{tx_id}" if account_id and tx_id else ''

            # Tarih — Womsis genellikle 'YYYY-MM-DD' veya 'DD-MM-YYYY HH:MM:SS' döner
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

            # Tutar ve yön
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

            # Mükerrer kontrol
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


# ── Hata Yoneticileri ─────────────────────────────────────────────────────────
@app.errorhandler(500)
def handle_500(e):
    logger.error('500 hatasi: %s', e, exc_info=True)
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Sunucu hatasi: ' + str(e)}), 500
    return f'<h2>Sunucu Hatasi</h2><pre>{e}</pre>', 500


@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Endpoint bulunamadi.'}), 404
    return redirect(url_for('login'))


# ── Baslat ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    logger.info('=' * 50)
    logger.info('webadmin-nakitakim basliyor')
    logger.info('Adres  : http://%s:%s', HOST, PORT)
    logger.info('DB     : %s@%s:%s/%s (ssl=%s)', PG_USER, PG_HOST, PG_PORT, PG_DB, PG_SSLMODE)
    logger.info('Debug  : %s', DEBUG)
    logger.info('=' * 50)
    app.run(host=HOST, port=PORT, debug=DEBUG)
