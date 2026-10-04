"""Penyimpanan SQLite untuk Alfagift Data."""
import json
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).parent
DATA_DIR = ROOT / 'data'
DB_PATH = DATA_DIR / 'alfagift.db'

DEFAULT_SETTINGS = {
    'categories': [],        # id kategori utama yang dipindai; kosong = semua
    'delay': 3.0,            # jeda antar request (detik)
    'max_requests': 150,     # batas request listing per putaran (±50 cukup untuk seluruh katalog)
    'detail_checks': 600,    # batas ambil detail (barcode & brand, 1 request/produk) per putaran
    'city': '',              # kota alamat akun (diisi crawler)
    'account': None,         # {address, city, store_code} dari akun Alfagift
    'schedule': ['06:30'],   # jam crawl otomatis setiap hari (waktu server)
    'schedule_on': True,
    'last_slot': '',         # jadwal terakhir yang sudah dijalankan, mis. "2026-10-05 06:30"
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  sku           TEXT PRIMARY KEY,
  name          TEXT,
  url           TEXT,
  brand         TEXT,
  category_id   TEXT,
  category_path TEXT,   -- "/15492/15532/15840/" untuk filter per kategori induk
  category_name TEXT,   -- "Home Living › Furnitur › Kursi"
  image         TEXT,
  price         INTEGER, -- harga normal
  final_price   INTEGER, -- harga jual (setelah diskon)
  discount_pct  REAL,
  in_stock      INTEGER,
  store_count   INTEGER,
  rating        REAL,
  review_count  INTEGER,
  first_seen    TEXT,
  last_seen     TEXT,
  last_change   TEXT,    -- terakhir harga berubah
  prev_price    INTEGER, -- harga jual sebelum perubahan terakhir
  city_name     TEXT,    -- kota alamat akun (mis. Pontianak)
  city_qty      INTEGER, -- stok di toko Alfamart akun
  city_stores   TEXT,    -- JSON [[nama toko, stok]]
  city_checked  TEXT,    -- kapan stok terakhir dicek
  plu           TEXT,    -- kode PLU Alfamart
  barcode       TEXT,    -- dari detail produk (diambil sekali per produk)
  detail_checked TEXT    -- kapan detail (barcode/brand) diambil
);
CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode);
CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_path);
CREATE INDEX IF NOT EXISTS idx_products_change ON products(last_change);
-- Satu baris per perubahan harga/stok (harga berlaku sampai baris berikutnya)
CREATE TABLE IF NOT EXISTS price_history (
  sku         TEXT NOT NULL,
  date        TEXT NOT NULL,
  price       INTEGER,
  final_price INTEGER,
  in_stock    INTEGER,
  PRIMARY KEY (sku, date)
);
CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger       TEXT,
  started       TEXT,
  finished      TEXT,
  status        TEXT,    -- running | done | limit | blocked | error
  requests      INTEGER DEFAULT 0,
  products      INTEGER DEFAULT 0,
  new_products  INTEGER DEFAULT 0,
  price_changes INTEGER DEFAULT 0,
  stock_checks  INTEGER DEFAULT 0,
  message       TEXT
);
"""


def connect():
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(SCHEMA)
    cols = {r['name'] for r in con.execute('PRAGMA table_info(products)')}
    for col, typ in (('city_name', 'TEXT'), ('city_qty', 'INTEGER'), ('city_stores', 'TEXT'), ('city_checked', 'TEXT'),
                     ('plu', 'TEXT'), ('barcode', 'TEXT'), ('detail_checked', 'TEXT')):
        if col not in cols:
            con.execute(f'ALTER TABLE products ADD COLUMN {col} {typ}')
    if 'stock_checks' not in {r['name'] for r in con.execute('PRAGMA table_info(runs)')}:
        con.execute('ALTER TABLE runs ADD COLUMN stock_checks INTEGER DEFAULT 0')
    return con


def get_settings(con):
    s = dict(DEFAULT_SETTINGS)
    for row in con.execute('SELECT key, value FROM settings'):
        s[row['key']] = json.loads(row['value'])
    return s


def set_settings(con, **values):
    for k, v in values.items():
        con.execute('INSERT INTO settings(key, value) VALUES(?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (k, json.dumps(v)))
    con.commit()
