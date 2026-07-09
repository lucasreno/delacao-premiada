"""API + UI da Revisão."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import classifier, clockify, config, db, segmenter

app = FastAPI(title="Delação Premiada")
STATIC = Path(__file__).parent / "static"


def _ts(date, hm):
    return int(datetime.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M").timestamp())


def _hm(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _day_row(date):
    r = db.q("SELECT * FROM days WHERE date=?", (date,))
    return r[0] if r else None


def _periods(date):
    d = _day_row(date)
    if not d or not d["ponto"]:
        return []
    return [(_ts(date, a), _ts(date, b)) for a, b in json.loads(d["ponto"])]


def _day_payload(date):
    d = _day_row(date)
    blocks = []
    for b in db.q("SELECT * FROM blocks WHERE date=? ORDER BY start_ts", (date,)):
        blocks.append(
            {
                "id": b["id"],
                "kind": b["kind"],
                "start": _hm(b["start_ts"]),
                "end": _hm(b["end_ts"]),
                "dur_min": (b["end_ts"] - b["start_ts"]) // 60,
                "contexto": b["context_key"],
                "evidence": json.loads(b["evidence"] or "{}"),
                "projeto": b["projeto"],
                "ticket": b["ticket"],
                "atividade": b["atividade"],
                "descricao": b["descricao"],
            }
        )
    migalhas = [
        {"id": m["id"], "hora": _hm(m["ts"]), "dur_min": max(1, m["dur_s"] // 60),
         "contexto": m["context_key"], "title": m["title"]}
        for m in db.q("SELECT * FROM migalhas WHERE date=? ORDER BY ts", (date,))
    ]
    return {
        "ponto": json.loads(d["ponto"]) if d and d["ponto"] else None,
        "status": d["status"] if d else "aberto",
        "blocks": blocks,
        "migalhas": migalhas,
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    today = datetime.now().strftime("%Y-%m-%d")
    n = db.q("SELECT COUNT(*) c FROM samples WHERE ts>=?", (_ts(today, "00:00"),))[0]["c"]
    last = db.q("SELECT ts, app, title FROM samples ORDER BY ts DESC LIMIT 1")
    return {
        "samples_hoje": n,
        "ultima": {**last[0], "hora": _hm(last[0]["ts"])} if last else None,
        "clockify_ok": bool(db.setting("clockify_api_key")),
        "openrouter_ok": bool(db.setting("openrouter_api_key")),
    }


@app.get("/api/settings")
def get_settings():
    return {
        "clockify_api_key": bool(db.setting("clockify_api_key")),
        "openrouter_api_key": bool(db.setting("openrouter_api_key")),
        "model": db.setting("model") or config.DEFAULT_MODEL,
    }


@app.post("/api/settings")
def post_settings(body: dict = Body(...)):
    for k in ("clockify_api_key", "openrouter_api_key", "model"):
        v = (body.get(k) or "").strip()
        if v:
            db.set_setting(k, v)
    return get_settings()


@app.post("/api/clockify/sync")
def clockify_sync():
    try:
        return clockify.sync()
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/options")
def options():
    return {
        "projetos": [p["name"] for p in (db.cache_get("projects") or [])],
        "atividades": [t["name"] for t in (db.cache_get("tags") or [])],
    }


@app.get("/api/day/{date}")
def get_day(date: str):
    return _day_payload(date)


@app.post("/api/day/{date}/ponto")
def set_ponto(date: str, body: dict = Body(...)):
    ponto = body.get("ponto") or []
    for a, b in ponto:  # valida HH:MM
        _ts(date, a), _ts(date, b)
    db.ex(
        "INSERT INTO days(date, ponto, status) VALUES(?,?,'aberto') "
        "ON CONFLICT(date) DO UPDATE SET ponto=excluded.ponto",
        (date, json.dumps(ponto)),
    )
    return _day_payload(date)


@app.post("/api/day/{date}/propose")
def propose(date: str):
    d = _day_row(date)
    if d and d["status"] == "aprovado":
        raise HTTPException(400, "Dia já aprovado.")
    day_start, day_end = _ts(date, "00:00"), _ts(date, "00:00") + 86400
    samples = db.q(
        "SELECT ts, app, title, idle_ms, in_call, call_title FROM samples "
        "WHERE ts>=? AND ts<? ORDER BY ts", (day_start, day_end))
    if not samples:
        raise HTTPException(400, "Nenhuma amostra capturada nesse dia.")

    blocks, migalhas = segmenter.segment(samples, _periods(date))
    db.ex("DELETE FROM blocks WHERE date=?", (date,))
    db.ex("DELETE FROM migalhas WHERE date=?", (date,))
    ids = []
    for b in blocks:
        ev = {
            "titles": dict(b["titles"].most_common(6)),
            "shadow": dict(b["shadow"].most_common(3)),
        }
        ticket = b["key"].split(":", 1)[1] if b["key"].startswith("ticket:") else None
        bid = db.ex(
            "INSERT INTO blocks(date, start_ts, end_ts, kind, context_key, evidence, ticket) "
            "VALUES(?,?,?,?,?,?,?)",
            (date, b["start"], b["end"], b["kind"], b["key"],
             json.dumps(ev, ensure_ascii=False), ticket),
        )
        if b["kind"] == "work":
            ids.append(bid)
    for m in migalhas:
        db.ex(
            "INSERT INTO migalhas(date, ts, dur_s, context_key, title) VALUES(?,?,?,?,?)",
            (date, m["ts"], m["dur_s"], m["key"], m["title"]),
        )
    db.ex(
        "INSERT INTO days(date, status) VALUES(?, 'proposto') "
        "ON CONFLICT(date) DO UPDATE SET status='proposto'", (date,))

    warning = None
    if ids:
        try:
            rows = db.q(
                f"SELECT * FROM blocks WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
            results = classifier.classify(rows)
            for bid, r in results.items():
                proposed = {
                    "projeto": r.get("projeto"),
                    "ticket": r.get("ticket"),
                    "atividade": r.get("atividade"),
                    "descricao": r.get("descricao"),
                }
                db.ex(
                    "UPDATE blocks SET projeto=?, ticket=COALESCE(?, ticket), atividade=?, "
                    "descricao=?, proposed=? WHERE id=?",
                    (proposed["projeto"], proposed["ticket"], proposed["atividade"],
                     proposed["descricao"], json.dumps(proposed, ensure_ascii=False), bid),
                )
        except Exception as e:
            warning = f"Classificação IA falhou: {e}"

    payload = _day_payload(date)
    payload["warning"] = warning
    return payload


@app.put("/api/day/{date}/blocks")
def put_blocks(date: str, body: dict = Body(...)):
    incoming = body.get("blocks") or []
    existing = {b["id"] for b in db.q("SELECT id FROM blocks WHERE date=?", (date,))}
    kept = set()
    for b in incoming:
        start, end = _ts(date, b["start"]), _ts(date, b["end"])
        fields = (start, end, b.get("kind") or "work", b.get("projeto"), b.get("ticket"),
                  b.get("atividade"), b.get("descricao"))
        if b.get("id") in existing:
            db.ex(
                "UPDATE blocks SET start_ts=?, end_ts=?, kind=?, projeto=?, ticket=?, "
                "atividade=?, descricao=? WHERE id=?", fields + (b["id"],))
            kept.add(b["id"])
        else:
            db.ex(
                "INSERT INTO blocks(start_ts, end_ts, kind, projeto, ticket, atividade, "
                "descricao, date, context_key, evidence) VALUES(?,?,?,?,?,?,?,?,'manual','{}')",
                fields + (date,))
    for bid in existing - kept:
        db.ex("DELETE FROM blocks WHERE id=?", (bid,))
    return _day_payload(date)


@app.post("/api/day/{date}/approve")
def approve(date: str):
    rows = db.q("SELECT * FROM blocks WHERE date=? ORDER BY start_ts", (date,))
    if not rows:
        raise HTTPException(400, "Nada para aprovar — gere a proposta primeiro.")
    now = int(time.time())
    for b in rows:
        if b["kind"] != "work" or not b["proposed"]:
            continue
        final = {"projeto": b["projeto"], "ticket": b["ticket"],
                 "atividade": b["atividade"], "descricao": b["descricao"]}
        if json.loads(b["proposed"]) != final:
            evidence = json.dumps(
                {"contexto": b["context_key"], **json.loads(b["evidence"] or "{}")},
                ensure_ascii=False)
            db.ex(
                "INSERT INTO corrections(ts, date, evidence, proposed, final) VALUES(?,?,?,?,?)",
                (now, date, evidence, b["proposed"], json.dumps(final, ensure_ascii=False)))
    db.ex("UPDATE days SET status='aprovado' WHERE date=?", (date,))

    total = sum(b["end_ts"] - b["start_ts"] for b in rows if b["kind"] == "work")
    jornada = sum(pe - ps for ps, pe in _periods(date))
    linhas = [
        f"{_hm(b['start_ts'])}–{_hm(b['end_ts'])}  "
        f"{(b['end_ts'] - b['start_ts']) // 3600}h{((b['end_ts'] - b['start_ts']) % 3600) // 60:02d}  "
        f"{b['projeto'] or '?'} | {b['ticket'] or '-'} | {b['atividade'] or '?'} | {b['descricao'] or ''}"
        for b in rows if b["kind"] == "work"
    ]
    return {
        "resumo": linhas,
        "total_min": total // 60,
        "jornada_min": jornada // 60,
        "divergencia_min": abs(total - jornada) // 60 if jornada else None,
    }
