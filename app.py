"""Alfagift Data — server web + API.

    python3 app.py                 # http://localhost:9093
    PORT=8000 python3 app.py
    APP_PASSWORD=rahasia python3 app.py   # wajib login (HTTP Basic, user bebas)
"""
import base64
import datetime as dt
import time
import csv
import hmac
import http.server
import io
import json
import math
import mimetypes
import os
import pathlib
import threading
import urllib.parse

import crawler
import db

PORT = int(os.environ.get('PORT', 9094))
PASSWORD = os.environ.get('APP_PASSWORD', '')
STATIC = pathlib.Path(__file__).parent / 'static'

SORTS = {
    'name': 'name', 'price': 'final_price', 'discount': 'discount_pct', 'change': 'last_change',
    'rating': 'rating', 'reviews': 'review_count', 'seen': 'last_seen', 'brand': 'brand',
}
_crawl_thread = None


# ─── Skor "Worth It" ─────────────────────────────────────
def rp_short(n):
    return f'Rp {n / 1_000_000:.1f} jt'.replace('.0 ', ' ') if n >= 1_000_000 else f'Rp {round(n / 1000):,} rb'.replace(',', '.')


def worth_score(p):
    """Skor 0–100 + alasan, dari diskon, nominal hemat, rating/ulasan, dan riwayat harga."""
    disc = min(p['discount_pct'] or 0, 70) / 70
    saving = max((p['price'] or 0) - (p['final_price'] or 0), 0)
    sav = min(math.log10(1 + saving) / 6, 1)                      # Rp 1 jt → penuh
    if p['review_count']:
        rate = (p['rating'] / 5) * min(math.log10(1 + p['review_count']) / math.log10(201), 1)  # 200 ulasan → penuh
    else:
        rate = 0.35                                                 # belum ada ulasan → netral
    days = p['hist_days'] or 0
    lowest = days >= 2 and p['final_price'] <= (p['hist_min'] or 0)
    if days < 2:
        hist = 0.5                                                  # riwayat belum cukup → netral
    elif lowest:
        hist = 1.0
    elif p['final_price'] < (p['hist_max'] or 0):
        hist = 0.6
    else:
        hist = 0.2
    score = round(100 * (0.45 * disc + 0.15 * sav + 0.25 * rate + 0.15 * hist))
    reasons = [f"Diskon {p['discount_pct']:g}%", f'Hemat {rp_short(saving)}']
    if p['review_count'] >= 5 and p['rating'] >= 4.5:
        reasons.append(f"★ {p['rating']:g} dari {p['review_count']:,} ulasan".replace(',', '.'))
    if lowest:
        reasons.append(f'Harga terendah dalam {days} hari')
    elif days >= 2 and p['final_price'] > (p['hist_min'] or 0):
        reasons.append(f"Pernah lebih murah ({rp_short(p['hist_min'])})")
    return score, reasons


def query_products(con, q):
    where, args = [], []
    city = db.get_settings(con).get('city') or ''
    if q.get('q'):
        where.append('(name LIKE ? OR sku LIKE ? OR brand LIKE ?)')
        like = f"%{q['q']}%"
        args += [like, like, like]
    if q.get('cat'):
        # filter per nama kategori (termasuk subkategorinya): "Rumah Tangga" / "Rumah Tangga › Pembersih"
        where.append("(category_name = ? OR category_name LIKE ? ESCAPE '\\')")
        prefix = q['cat'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        args += [q['cat'], prefix + ' › %']
    if q.get('store'):
        where.append('city_stores LIKE ?')
        args.append('%' + json.dumps(q['store'], ensure_ascii=False)[1:-1] + '%')
    if q.get('brand'):
        where.append('brand = ?')
        args.append(q['brand'])
    if q.get('city') == '1' and city:
        # hanya produk yang sudah dicek dan ada stoknya di toko kota pilihan
        where.append('city_name = ? AND city_qty > 0')
        args.append(city)
    if q.get('stock') in ('0', '1'):
        where.append('in_stock = ?')
        args.append(int(q['stock']))
    if q.get('discount') == '1':
        where.append('discount_pct > 0')
    if q.get('changed') == '1':
        where.append('last_change IS NOT NULL')
    sql_where = ('WHERE ' + ' AND '.join(where)) if where else ''
    col = SORTS.get(q.get('sort'), 'name')
    direction = 'DESC' if q.get('dir') == 'desc' else 'ASC'
    nulls = f'{col} IS NULL, ' if col == 'last_change' else ''
    return sql_where, args, f'ORDER BY {nulls}{col} {direction}, name'


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'AlfagiftData/1.0'

    def log_message(self, fmt, *a):
        pass

    # ── helpers ─────────────────────────────────────
    def _authorized(self):
        if not PASSWORD:
            return True
        h = self.headers.get('Authorization', '')
        if h.startswith('Basic '):
            try:
                pw = base64.b64decode(h[6:]).decode().split(':', 1)[1]
                return hmac.compare_digest(pw, PASSWORD)
            except Exception:
                return False
        return False

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    def _guard(self):
        if self._authorized():
            return True
        self._send(401, {'error': 'login'}, extra={'WWW-Authenticate': 'Basic realm="Alfagift Data"'})
        return False

    # ── routes ──────────────────────────────────────
    def do_GET(self):
        if not self._guard():
            return
        url = urllib.parse.urlparse(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        path = url.path
        if path.startswith('/api/'):
            con = db.connect()
            try:
                return self._api_get(con, path, q)
            finally:
                con.close()
        self._static(path)

    def do_POST(self):
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        con = db.connect()
        try:
            if path == '/api/settings':
                b = self._body()
                s = db.get_settings(con)
                new = {}
                if 'categories' in b:
                    valid = {c['id'] for c in crawler.load_categories()}
                    new['categories'] = [c for c in dict.fromkeys(map(str, b['categories'])) if c in valid]
                if 'delay' in b:
                    new['delay'] = min(60.0, max(1.0, float(b['delay'])))
                if 'max_requests' in b:
                    new['max_requests'] = min(5000, max(1, int(b['max_requests'])))
                if 'schedule' in b:
                    times = sorted({dt.time.fromisoformat(str(t)[:5]).strftime('%H:%M') for t in b['schedule']})
                    if len(times) > 12:
                        raise ValueError('Maksimal 12 jadwal per hari')
                    new['schedule'] = times
                if 'schedule_on' in b:
                    new['schedule_on'] = bool(b['schedule_on'])
                db.set_settings(con, **new)
                return self._send(200, {'ok': True, 'settings': db.get_settings(con)})
            if path == '/api/run':
                return self._send(200, {'started': start_crawl()})
            self._send(404, {'error': 'not found'})
        except (ValueError, TypeError) as e:
            self._send(400, {'error': str(e)})
        finally:
            con.close()

    def _api_get(self, con, path, q):
        if path == '/api/status':
            s = db.get_settings(con)
            city = s.get('city') or ''
            counts = con.execute("""SELECT COUNT(*) total, SUM(in_stock) in_stock, SUM(discount_pct > 0) discounted,
                                      SUM(last_change >= date('now', '-7 day')) changed_7d,
                                      SUM(city_name = ? AND city_qty > 0) in_city,
                                      SUM(in_stock = 1 AND city_name = ? AND city_checked IS NOT NULL) city_checked
                                      FROM products""", (city, city)).fetchone()
            runs = [dict(r) for r in con.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 30')]
            return self._send(200, {'settings': s, 'running': crawler.is_running(), 'counts': dict(counts), 'runs': runs,
                                    'next_run': next_run(s, dt.datetime.now())})
        if path == '/api/categories':
            counts = {r['top']: r['n'] for r in con.execute(
                """SELECT substr(category_name, 1, instr(category_name || ' ›', ' ›') - 1) top, COUNT(*) n
                   FROM products WHERE in_stock = 1 GROUP BY top""")}
            tree = [{**c, 'n': counts.get(c['name'], 0)} for c in crawler.load_categories()]
            return self._send(200, {'tree': tree, 'watched': db.get_settings(con)['categories']})
        if path == '/api/highlights':
            s = db.get_settings(con)
            where = ['p.in_stock = 1', 'p.discount_pct >= ?']
            args = [max(10.0, float(q.get('min_discount') or 10))]
            if q.get('city', '1') == '1' and s.get('city'):
                where.append('p.city_name = ? AND p.city_qty > 0')
                args.append(s['city'])
            if q.get('store'):
                where.append('p.city_stores LIKE ?')
                args.append('%' + json.dumps(q['store'], ensure_ascii=False)[1:-1] + '%')
            if q.get('cat'):
                where.append("(p.category_name = ? OR p.category_name LIKE ?)")
                args += [q['cat'], q['cat'] + ' › %']
            rows = con.execute(f"""SELECT p.*, h.hist_min, h.hist_max, h.hist_days FROM products p
                                    LEFT JOIN (SELECT sku, MIN(final_price) hist_min, MAX(final_price) hist_max,
                                                      CAST(julianday('now', 'localtime') - julianday(MIN(date)) AS INTEGER) + 1 hist_days
                                               FROM price_history GROUP BY sku) h USING (sku)
                                    WHERE {' AND '.join(where)}""", args).fetchall()
            items = []
            for r in rows:
                p = dict(r)
                p['score'], p['reasons'] = worth_score(p)
                p['saving'] = max(p['price'] - p['final_price'], 0)
                items.append(p)
            key = {'discount': lambda x: (x['discount_pct'], x['score']), 'saving': lambda x: (x['saving'], x['score'])}
            items.sort(key=key.get(q.get('sort'), lambda x: (x['score'], x['discount_pct'])), reverse=True)
            limit = min(200, max(1, int(q.get('limit') or 48)))
            return self._send(200, {'total': len(items), 'items': items[:limit]})
        if path == '/api/product-categories':
            # Kategori dari produk tersimpan, 2 tingkat teratas, sesuai filter kota aktif
            tree = {}
            sql = 'SELECT category_name, COUNT(*) n FROM products WHERE category_name IS NOT NULL'
            args = []
            if q.get('city') == '1':
                sql += ' AND city_name = ? AND city_qty > 0'
                args.append(db.get_settings(con).get('city') or '')
            for r in con.execute(sql + ' GROUP BY category_name', args):
                parts = r['category_name'].split(' › ')
                top = tree.setdefault(parts[0], {'n': 0, 'children': {}})
                top['n'] += r['n']
                if len(parts) > 1:
                    top['children'][parts[1]] = top['children'].get(parts[1], 0) + r['n']
            return self._send(200, [{'name': k, 'n': v['n'], 'children': [{'name': c, 'n': n} for c, n in sorted(v['children'].items())]}
                                    for k, v in sorted(tree.items())])
        if path == '/api/city-stores':
            acc = db.get_settings(con).get('account') or {}
            stores = []
            if acc.get('store_code'):
                n = con.execute('SELECT COUNT(*) FROM products WHERE in_stock = 1').fetchone()[0]
                stores.append({'name': f"Alfamart {acc['store_code']}", 'n': n, 'address': acc.get('address')})
            return self._send(200, {'stores': stores, 'account': acc})
        if path == '/api/brands':
            rows = con.execute("SELECT brand, COUNT(*) n FROM products WHERE brand != '' GROUP BY brand ORDER BY n DESC")
            return self._send(200, [dict(r) for r in rows])
        if path == '/api/products':
            where, args, order = query_products(con, q)
            size = min(200, max(1, int(q.get('size') or 50)))
            page = max(1, int(q.get('page') or 1))
            total = con.execute(f'SELECT COUNT(*) FROM products {where}', args).fetchone()[0]
            rows = con.execute(f'SELECT * FROM products {where} {order} LIMIT ? OFFSET ?',
                               args + [size, (page - 1) * size])
            return self._send(200, {'total': total, 'page': page, 'size': size, 'items': [dict(r) for r in rows]})
        if path.startswith('/api/products/'):
            sku = urllib.parse.unquote(path.rsplit('/', 1)[1])
            p = con.execute('SELECT * FROM products WHERE sku = ?', (sku,)).fetchone()
            if not p:
                return self._send(404, {'error': 'produk tidak ditemukan'})
            hist = con.execute('SELECT date, price, final_price, in_stock FROM price_history WHERE sku = ? ORDER BY date',
                               (sku,))
            return self._send(200, {'product': dict(p), 'history': [dict(h) for h in hist]})
        if path == '/api/export.csv':
            where, args, order = query_products(con, q)
            cols = ['sku', 'plu', 'name', 'category_name', 'price', 'final_price', 'discount_pct', 'in_stock',
                    'city_name', 'city_qty', 'city_stores', 'city_checked', 'rating', 'review_count', 'url', 'image', 'first_seen', 'last_seen', 'last_change',
                    'prev_price']
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            for r in con.execute(f'SELECT {", ".join(cols)} FROM products {where} {order}', args):
                w.writerow([r[c] for c in cols])
            # BOM supaya Excel membaca UTF-8 dengan benar
            return self._send(200, '﻿' + buf.getvalue(), 'text/csv; charset=utf-8',
                              {'Content-Disposition': 'attachment; filename="alfagift-data.csv"'})
        self._send(404, {'error': 'not found'})

    def _static(self, path):
        rel = 'index.html' if path in ('', '/') else path.lstrip('/')
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self._send(404, 'Not found', 'text/plain')
        ctype = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        self._send(200, f.read_bytes(), ctype)


_start_lock = threading.Lock()


def start_crawl(trigger='manual'):
    global _crawl_thread
    with _start_lock:   # dua klik bersamaan tidak boleh memulai dua crawl
        if crawler.is_running() or (_crawl_thread and _crawl_thread.is_alive()):
            return False
        _crawl_thread = threading.Thread(target=crawler.crawl, kwargs={'trigger': trigger}, daemon=True)
        _crawl_thread.start()
        return True


# ─── Jadwal crawl ──────────────────────────────────────
GRACE = dt.timedelta(minutes=30)   # jadwal yang terlewat (mis. server restart) masih dijalankan dalam 30 menit


def due_slot(settings, now):
    """Jadwal hari ini yang sudah lewat waktunya tapi belum dijalankan, atau None."""
    if not settings.get('schedule_on'):
        return None
    for t in sorted(settings.get('schedule') or []):
        slot = dt.datetime.combine(now.date(), dt.time.fromisoformat(t))
        key = slot.strftime('%Y-%m-%d %H:%M')
        if slot <= now < slot + GRACE and key > (settings.get('last_slot') or ''):
            return key
    return None


def next_run(settings, now):
    if not settings.get('schedule_on') or not settings.get('schedule'):
        return None
    for day in (0, 1):
        for t in sorted(settings['schedule']):
            slot = dt.datetime.combine(now.date() + dt.timedelta(days=day), dt.time.fromisoformat(t))
            if slot > now:
                return slot.isoformat(timespec='minutes')
    return None


def scheduler_loop():
    while True:
        try:
            con = db.connect()
            try:
                s = db.get_settings(con)
                key = due_slot(s, dt.datetime.now())
                if key and not crawler.is_running():
                    db.set_settings(con, last_slot=key)
                    print(f'[jadwal] {key} → crawl dimulai')
                    start_crawl('scheduled')
            finally:
                con.close()
        except Exception as e:
            print(f'[jadwal] error: {e}')
        time.sleep(20)


if __name__ == '__main__':
    con = db.connect()
    if not crawler.is_running():
        con.execute("""UPDATE runs SET status='error', finished=COALESCE(finished, started),
                         message='Terputus (server berhenti saat crawl berjalan).' WHERE status='running'""")
        con.commit()
    con.close()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(('', PORT), Handler)
    print(f'Alfagift Data → http://localhost:{PORT}' + (' (dengan password)' if PASSWORD else ''))
    srv.serve_forever()
