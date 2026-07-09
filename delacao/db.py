import json
import sqlite3
import threading

from . import config

_lock = threading.Lock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS samples(
  ts INTEGER, app TEXT, title TEXT, idle_ms INTEGER, in_call INTEGER, call_title TEXT);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE TABLE IF NOT EXISTS days(date TEXT PRIMARY KEY, ponto TEXT, status TEXT DEFAULT 'aberto');
CREATE TABLE IF NOT EXISTS blocks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, start_ts INTEGER, end_ts INTEGER,
  kind TEXT, context_key TEXT, evidence TEXT, proposed TEXT,
  projeto TEXT, ticket TEXT, atividade TEXT, descricao TEXT);
CREATE INDEX IF NOT EXISTS idx_blocks_date ON blocks(date);
CREATE TABLE IF NOT EXISTS migalhas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ts INTEGER, dur_s INTEGER,
  context_key TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS corrections(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, date TEXT,
  evidence TEXT, proposed TEXT, final TEXT);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT, ts INTEGER);
"""


def conn():
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def ex(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.lastrowid


def setting(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,))
    return r[0]["value"] if r else default


def set_setting(key, value):
    ex(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def cache_get(key):
    r = q("SELECT value FROM cache WHERE key=?", (key,))
    return json.loads(r[0]["value"]) if r else None


def cache_set(key, value):
    ex(
        "INSERT INTO cache(key,value,ts) VALUES(?,?,strftime('%s','now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
        (key, json.dumps(value, ensure_ascii=False)),
    )
