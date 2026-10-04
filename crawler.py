"""Crawler Alfagift Data.

Login ke Alfagift dengan akun pemilik (no HP + password dari environment
ALFA_PHONE / ALFA_PASSWORD), lalu memakai toko Alfamart yang melayani alamat
aktif akun tsb (mis. Pontianak) — harga, stok, dan daftar produk mengikuti
toko itu. Produk diambil per kategori utama lewat API yang dipakai web
alfagift.id sendiri (200 produk per request).

Ada jeda antar request dan batas request per putaran. Pemindaian lengkap
menandai produk yang tidak lagi dijual/tersedia di toko tsb.

Kalau server menolak akses (HTTP 403/429), crawler BERHENTI — tidak mencoba
mengakali pembatasan.

Jalankan:  python3 crawler.py            (manual)
           python3 crawler.py --scheduled
"""
import datetime as dt
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

import db

GW = 'https://webcommerce-gw.alfagift.id'
PAGE_SIZE = 200
FINGERPRINT = 'hgBMvGVC3S4zMlFMl977vazpqfM0p2bW6e/80IKImVLTpWFJjDWe9W+rS/66P123'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120 Safari/537.36')
LOCK = db.DATA_DIR / 'crawl.lock'
CATS_CACHE = db.DATA_DIR / 'categories.json'
SESSION = db.DATA_DIR / 'session.json'    # token + toko (token berlaku ±30 hari)


# Alfagift downtime 23.00–06.00: API tidak bisa dipakai dan harga kembali normal
# (tanpa promo) — crawl di jam itu akan mencatat harga yang salah.
BLACKOUT = (dt.time(23, 0), dt.time(6, 0))


def in_blackout(t=None):
    t = (t or dt.datetime.now()).time()
    start, end = BLACKOUT
    return t >= start or t < end


class Blackout(Exception):
    """Memasuki jam downtime Alfagift — crawl dihentikan."""


class Blocked(Exception):
    """Server menolak akses — jangan dilanjutkan."""


class LoginError(Exception):
    pass


def now():
    return dt.datetime.now().isoformat(timespec='seconds')


# ─── HTTP ─────────────────────────────────────────────
def api(path, method='GET', body=None, token='', store=''):
    h = {'accept': 'application/json, text/plain, */*', 'accept-language': 'id', 'devicemodel': 'chrome',
         'devicetype': 'Web', 'fingerprint': FINGERPRINT, 'latitude': '0', 'longitude': '0', 'token': token,
         'origin': 'https://alfagift.id', 'referer': 'https://alfagift.id/', 'user-agent': UA,
         'trxid': str(random.randint(1_000_000_000, 9_999_999_999))}
    if store:
        h['storecode'] = store
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h['content-type'] = 'application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request(GW + path, data=data, headers=h, method=method), timeout=40) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise Blocked(f'akses ditolak Alfagift (HTTP {e.code})')
        raise


def with_retry(fn, log, waits=(15, 45)):
    """Coba ulang untuk timeout / error server sesaat (5xx). Blocked tidak dicoba ulang."""
    for wait in (*waits, None):
        try:
            return fn()
        except Blocked:
            raise
        except urllib.error.HTTPError as e:
            if e.code < 500 or wait is None:
                raise
            wait = min(wait, 10)   # error server biasanya permanen per kategori → jangan lama-lama
            log(f'  HTTP {e.code} — coba lagi dalam {wait} detik')
            time.sleep(wait)
        except (TimeoutError, OSError, urllib.error.URLError) as e:
            if wait is None:
                raise
            log(f'  {type(e).__name__}: {e} — coba lagi dalam {wait} detik')
            time.sleep(wait)


# ─── Sesi: login + toko dari alamat akun ──────────────
def token_valid(tok):
    try:
        import base64
        payload = json.loads(base64.urlsafe_b64decode(tok.split('.')[1] + '=='))
        return payload.get('exp', 0) - time.time() > 6 * 3600
    except Exception:
        return False


def session(log=print, force=False):
    """{token, store, store_code, address, city} — dari cache bila token masih berlaku."""
    if not force and SESSION.exists():
        s = json.loads(SESSION.read_text())
        if token_valid(s.get('token', '')):
            return s
    phone, password = os.environ.get('ALFA_PHONE', ''), os.environ.get('ALFA_PASSWORD', '')
    if not phone or not password:
        raise LoginError('ALFA_PHONE / ALFA_PASSWORD belum diatur di server')
    d = with_retry(lambda: api('/v1/account/member/login', 'POST', {
        'login': phone, 'loginType': 'mobile', 'password': password, 'requestPosition': 'LOGIN'}), log)
    st = d.get('status') or {}
    if st.get('code') != '00' or not st.get('token'):
        raise LoginError(f"Login Alfagift gagal: {st.get('message') or st.get('code')}")
    tok = st['token']
    addrs = with_retry(lambda: api('/v1/account/member/address/coordinate/get', token=tok), log).get('address') or []
    a = next((x for x in addrs if x.get('active')), addrs[0] if addrs else None)
    if not a or not a.get('encodedStoreCode'):
        raise LoginError('Akun belum punya alamat aktif — atur alamat di app/web Alfagift dulu')
    s = {'token': tok, 'store': a['encodedStoreCode'], 'store_code': a.get('storeSupport') or '',
         'address': ', '.join(x for x in (a.get('district'), a.get('city')) if x),   # tanpa label/jalan (app publik)
         'city': a.get('city') or '', 'saved': now()}
    db.DATA_DIR.mkdir(exist_ok=True)
    SESSION.write_text(json.dumps(s))
    os.chmod(SESSION, 0o600)
    log(f"Login OK — toko {s['store_code']} ({s['address']})")
    return s


def load_categories(log=print, max_age_days=7):
    """Pohon kategori Alfagift (cache 7 hari): [{id, name, children:[...]}]."""
    if CATS_CACHE.exists() and time.time() - CATS_CACHE.stat().st_mtime < max_age_days * 86400:
        return json.loads(CATS_CACHE.read_text(encoding='utf-8'))

    def slim(c):
        kids = c.get('subCategories') or c.get('children') or []
        return {'id': c['categoryId'], 'name': c['categoryName'], 'children': [slim(k) for k in kids]}

    tree = [slim(c) for c in with_retry(lambda: api('/v2/categories'), log).get('categories') or []]
    db.DATA_DIR.mkdir(exist_ok=True)
    CATS_CACHE.write_text(json.dumps(tree, ensure_ascii=False), encoding='utf-8')
    return tree


# ─── Simpan ───────────────────────────────────────────
def slugify(s):
    return re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-')


def parse(p):
    price = int(p.get('basePrice') or 0)
    final = int(p.get('finalPrice') or 0)
    price = max(price, final)
    stock = int(p.get('stock') or 0)
    disc = float(p.get('discountPercent') or 0) or (round((price - final) / price * 100, 1) if price and final < price else 0)
    return {
        'sku': str(p.get('productId')),
        'name': p.get('productName') or '',
        'url': f"https://alfagift.id/p/{slugify(p.get('productName'))}-{p.get('productId')}",
        'brand': '',
        'image': p.get('image') or '',
        'price': price,
        'final_price': final,
        'discount_pct': round(disc, 1),
        # Dijual & ada stok di toko akun (harga 0 = tidak dijual di toko ini)
        'in_stock': 1 if final > 0 and stock > 0 else 0,
        'rating': 0.0,
        'review_count': 0,
        'stock': stock,
        'plu': p.get('plu') or '',
        'cat_names': [p.get(f'categoryNameLvl{i}') for i in range(4) if p.get(f'categoryNameLvl{i}')] or ['Lainnya'],
    }


def save(con, row, today, stamp, store_label, city):
    """Upsert produk + riwayat harga (hanya saat berubah). Return (baru?, harga berubah?)."""
    old = con.execute('SELECT final_price FROM products WHERE sku = ?', (row['sku'],)).fetchone()
    names = row['cat_names']
    args = {**row, 'cpath': '/' + '/'.join('~' + n for n in names) + '/', 'cname': ' › '.join(names),
            'cid': '~' + names[-1], 'now': stamp, 'city': city, 'prev': old['final_price'] if old else None,
            'city_qty': row['stock'] if row['in_stock'] else 0,
            'city_stores': json.dumps([[store_label, row['stock']]] if row['in_stock'] else [], ensure_ascii=False)}
    # Harga 0 = tidak dijual di toko → jangan dianggap perubahan harga
    changed = bool(old and row['final_price'] and old['final_price'] and old['final_price'] != row['final_price'])
    if old is None:
        con.execute("""INSERT INTO products (sku, name, url, brand, category_id, category_path, category_name, image,
                         price, final_price, discount_pct, in_stock, rating, review_count, first_seen, last_seen,
                         city_name, city_qty, city_stores, city_checked, plu)
                       VALUES (:sku, :name, :url, :brand, :cid, :cpath, :cname, :image, :price, :final_price,
                         :discount_pct, :in_stock, :rating, :review_count, :now, :now,
                         :city, :city_qty, :city_stores, :now, :plu)""", args)
    else:
        con.execute(f"""UPDATE products SET name=:name, url=:url, image=:image, price=:price, final_price=:final_price,
                         discount_pct=:discount_pct, in_stock=:in_stock, last_seen=:now, category_id=:cid,
                         category_path=:cpath, category_name=:cname, city_name=:city, city_qty=:city_qty,
                         city_stores=:city_stores, city_checked=:now, plu=:plu
                         {', last_change=:now, prev_price=:prev' if changed else ''}
                       WHERE sku=:sku""", args)
    if row['final_price']:
        last = con.execute('SELECT price, final_price, in_stock FROM price_history WHERE sku = ? '
                           'ORDER BY date DESC LIMIT 1', (row['sku'],)).fetchone()
        if last is None or (last['price'], last['final_price'], last['in_stock']) != (row['price'], row['final_price'], row['in_stock']):
            con.execute("""INSERT INTO price_history (sku, date, price, final_price, in_stock) VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(sku, date) DO UPDATE SET price=excluded.price, final_price=excluded.final_price,
                             in_stock=excluded.in_stock""",
                        (row['sku'], today, row['price'], row['final_price'], row['in_stock']))
    return old is None, changed


# ─── Putaran ──────────────────────────────────────────
def is_running():
    if not LOCK.exists():
        return False
    try:
        os.kill(int(LOCK.read_text()), 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def acquire_lock():
    """Kunci atomik (O_EXCL) supaya dua crawl tidak pernah jalan bersamaan."""
    db.DATA_DIR.mkdir(exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            if is_running():
                return False
            LOCK.unlink(missing_ok=True)
    return False


def crawl(trigger='manual', log=print):
    if in_blackout():
        log('Jam downtime Alfagift (23.00–06.00) — crawl tidak dijalankan.')
        return None
    if not acquire_lock():
        log('Crawler sudah berjalan.')
        return None
    con = db.connect()
    try:
        return _crawl(con, trigger, log)
    finally:
        con.close()
        LOCK.unlink(missing_ok=True)


class _Budget(Exception):
    pass


def _crawl(con, trigger, log):
    s = db.get_settings(con)
    run_id = con.execute('INSERT INTO runs (trigger, started, status) VALUES (?, ?, ?)',
                         (trigger, now(), 'running')).lastrowid
    con.commit()
    stats = {'requests': 0, 'products': 0, 'new_products': 0, 'price_changes': 0, 'stock_checks': 0}

    def finish(status, message):
        con.execute("""UPDATE runs SET finished=?, status=?, requests=?, products=?, new_products=?,
                         price_changes=?, stock_checks=?, message=? WHERE id=?""",
                    (now(), status, stats['requests'], stats['products'], stats['new_products'],
                     stats['price_changes'], stats['stock_checks'], message, run_id))
        con.commit()
        log(f'[{status}] {message} — {stats}')
        return status

    try:
        return _run(con, s, stats, log, finish)
    except Blackout:
        con.commit()
        return finish('limit', f'Dihentikan otomatis pukul {dt.datetime.now():%H.%M}: memasuki jam downtime Alfagift '
                               f'(23.00–06.00). {stats["products"]} produk sudah tersimpan.')
    except Blocked as e:
        con.rollback()
        return finish('blocked', f'Dihentikan: {e}. Coba lagi nanti atau kurangi jumlah request.')
    except LoginError as e:
        return finish('error', str(e))
    except Exception as e:   # error tak terduga → tetap tercatat, tidak menggantung "running"
        con.rollback()
        return finish('error', f'{type(e).__name__}: {e}')


def _run(con, s, stats, log, finish):
    delay = max(1.0, float(s['delay']))
    limit = max(1, int(s['max_requests']))
    today, run_start = dt.date.today().isoformat(), now()

    def call(fn, budget=True):
        if in_blackout():
            raise Blackout()
        if budget and stats['requests'] >= limit:
            raise _Budget()
        if stats['requests']:
            time.sleep(delay)
        out = with_retry(fn, log)
        stats['requests'] += 1
        return out

    sess = session(log)
    tree = load_categories(log)
    store_label = f"Alfamart {sess['store_code']}"
    city = sess['city'] or 'toko'
    db.set_settings(con, city=city, account={'address': sess['address'], 'city': sess['city'], 'store_code': sess['store_code']})
    picked = set(s.get('categories') or [])
    cats = [c for c in tree if not picked or c['id'] in picked]

    seen, skipped = set(), []
    queue = list(cats)
    try:
        while queue:
            c = queue.pop(0)
            # Catatan: parameter `start` API Alfagift adalah NOMOR HALAMAN (0, 1, 2, …), bukan offset
            page, pages = 0, None
            while pages is None or page < pages:
                path = f"/v2/products/category/{c['id']}?sortDirection=asc&start={page}&limit={PAGE_SIZE}"
                try:
                    r = call(lambda: api(path, token=sess['token'], store=sess['store']))
                except urllib.error.HTTPError as e:
                    if e.code == 401:   # token kedaluwarsa lebih cepat → login ulang sekali
                        sess = session(log, force=True)
                        continue
                    if c['children'] and page == 0:
                        # Server Alfagift kadang gagal di kategori induk tapi lancar per subkategori
                        log(f"  {c['name']}: HTTP {e.code} → dipindai per subkategori")
                        queue[:0] = c['children']
                    else:
                        skipped.append(c['name'])
                    break
                total = int(r.get('totalData') or 0)
                pages = int(r.get('totalPage') or 0)
                prods = r.get('products') or []
                stamp = now()
                for p in prods:
                    row = parse(p)
                    if row['sku'] in seen:
                        continue
                    seen.add(row['sku'])
                    new, changed = save(con, row, today, stamp, store_label, city)
                    stats['products'] += 1
                    stats['new_products'] += new
                    stats['price_changes'] += changed
                    stats['stock_checks'] += row['in_stock']
                con.commit()
                log(f"{c['name']}: halaman {page + 1}/{pages} ({total} produk)")
                if not prods:
                    break
                page += 1
    except _Budget:
        con.commit()
        return finish('limit', f'Batas {limit} request tercapai setelah {len(seen)} produk — naikkan "Maks. request" '
                               f'supaya satu putaran mencakup semua kategori.')

    note = f" Dilewati (ditolak server Alfagift): {', '.join(skipped)}." if skipped else ''
    note += _details(con, s, stats, call, log)
    if not picked:
        # Pemindaian lengkap → produk yang tidak muncul lagi = tidak dijual lagi di toko ini
        # (kecuali produk dari kategori yang gagal dipindai)
        keep = ''.join(" AND category_name NOT LIKE ?" for _ in skipped)
        gone = con.execute(f"""UPDATE products SET in_stock=0, city_qty=0, city_stores='[]', city_checked=?
                              WHERE in_stock=1 AND last_seen < ?{keep}""",
                           (now(), run_start, *[f'%{n}%' for n in skipped])).rowcount
        con.commit()
        if gone:
            note += f' {gone} produk tidak lagi tersedia.'
    return finish('done', f"{store_label} ({sess['address']}): {len(seen)} produk dipindai, "
                          f"{stats['stock_checks']} tersedia.{note}")


def _details(con, s, stats, call, log):
    """Ambil barcode & brand dari detail produk (1 request/produk, sekali per produk).
    Produk yang tersedia di toko didahulukan. Return catatan untuk pesan run."""
    budget = int(s.get('detail_checks') or 0)
    if budget <= 0:
        return ''
    skus = [r['sku'] for r in con.execute(
        """SELECT sku FROM products WHERE detail_checked IS NULL
           ORDER BY in_stock DESC, discount_pct DESC LIMIT ?""", (budget,))]
    done = 0
    for sku in skus:
        try:
            d = call(lambda: api(f'/v2/products/{sku}'), budget=False)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            d = {}   # produk sudah dihapus dari Alfagift
        pd = (d or {}).get('productDetail') or {}
        con.execute('UPDATE products SET barcode=?, brand=COALESCE(NULLIF(?, \'\'), brand), detail_checked=? WHERE sku=?',
                    (str(pd.get('barcode') or '') or None, (pd.get('brandName') or '').strip(' -'), now(), sku))
        done += 1
        if done % 25 == 0:
            con.commit()
            log(f'Detail: {done}/{len(skus)}')
    con.commit()
    stats['details'] = done
    left = con.execute('SELECT COUNT(*) FROM products WHERE detail_checked IS NULL').fetchone()[0]
    return f" Barcode diambil untuk {done} produk" + (f", {left} belum." if left else ', semua lengkap.')


if __name__ == '__main__':
    crawl('scheduled' if '--scheduled' in sys.argv else 'manual')
