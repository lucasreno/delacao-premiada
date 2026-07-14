"""API + UI da Revisão."""

import csv
import io
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from . import classifier, clockify, collector, config, db, jira, segmenter

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
                "confianca": b["confianca"],
                "edited": bool(b["edited"]),
            }
        )
    migalhas = [
        {"id": m["id"], "hora": _hm(m["ts"]), "dur_min": max(1, m["dur_s"] // 60),
         "contexto": m["context_key"], "title": m["title"]}
        for m in db.q("SELECT * FROM migalhas WHERE date=? ORDER BY ts", (date,))
    ]
    payload = {
        "ponto": json.loads(d["ponto"]) if d and d["ponto"] else None,
        "status": d["status"] if d else "aberto",
        "blocks": blocks,
        "migalhas": migalhas,
    }
    if d and d["status"] == "aprovado":
        rows = db.q("SELECT * FROM blocks WHERE date=? ORDER BY start_ts", (date,))
        payload.update(_resumo(date, rows))
    return payload


def _resumo(date, rows):
    """Linhas de resumo do dia aprovado (usado no approve e ao recarregar a página)."""
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
        "coletor": dict(collector.health),
    }


@app.get("/api/settings")
def get_settings():
    return {
        "clockify_api_key": bool(db.setting("clockify_api_key")),
        "openrouter_api_key": bool(db.setting("openrouter_api_key")),
        "model": db.setting("model") or config.DEFAULT_MODEL,
        "jira_base_url": db.setting("jira_base_url") or "",
        "jira_email": db.setting("jira_email") or "",
        "jira_api_token": bool(db.setting("jira_api_token")),
    }


@app.post("/api/settings")
def post_settings(body: dict = Body(...)):
    for k in ("clockify_api_key", "openrouter_api_key", "model",
              "jira_base_url", "jira_email", "jira_api_token"):
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


@app.post("/api/jira/sync")
def jira_sync():
    try:
        return jira.sync()
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
            "migalhas": dict(b["migalhas"].most_common(4)) if b.get("migalhas") else {},
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
        jira.maybe_sync()  # candidatos a Ticket frescos para o classifier (não-fatal)
        try:
            rows = db.q(
                f"SELECT * FROM blocks WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
            _apply_classification(rows)
        except Exception as e:
            warning = f"Classificação IA falhou: {e}"

    payload = _day_payload(date)
    payload["warning"] = warning
    return payload


def _apply_classification(rows):
    """Roda o classifier sobre as rows e grava resultado + proposta + confiança.
    Zera `edited`: o conteúdo volta a ser proposta da IA."""
    results = classifier.classify(rows)
    for bid, r in results.items():
        proposed = {
            "projeto": r.get("projeto"),
            "ticket": r.get("ticket"),
            "atividade": r.get("atividade"),
            "descricao": r.get("descricao"),
        }
        try:
            conf = min(1.0, max(0.0, float(r.get("confianca"))))
        except (TypeError, ValueError):
            conf = None
        db.ex(
            "UPDATE blocks SET projeto=?, ticket=COALESCE(?, ticket), atividade=?, "
            "descricao=?, proposed=?, confianca=?, edited=0 WHERE id=?",
            (proposed["projeto"], proposed["ticket"], proposed["atividade"],
             proposed["descricao"], json.dumps(proposed, ensure_ascii=False), conf, bid),
        )
    return len(results)


@app.post("/api/day/{date}/classify")
def classify_day(date: str, body: dict = Body(default={})):
    """Reclassifica via IA sem re-segmentar: preserva horários e blocos manuais.
    Sem `ids`, pega os blocos de trabalho não editados; com `ids`, exatamente esses."""
    d = _day_row(date)
    if d and d["status"] == "aprovado":
        raise HTTPException(400, "Dia já aprovado.")
    ids = body.get("ids") or []
    if ids:
        rows = db.q(
            f"SELECT * FROM blocks WHERE date=? AND kind='work' "
            f"AND id IN ({','.join('?' * len(ids))})", (date, *ids))
    else:
        rows = db.q(
            "SELECT * FROM blocks WHERE date=? AND kind='work' AND edited=0 "
            "AND context_key!='manual'", (date,))
    if not rows:
        raise HTTPException(400, "Nenhum bloco para reclassificar.")
    try:
        n = _apply_classification(rows)
    except Exception as e:
        raise HTTPException(400, f"Classificação IA falhou: {e}")
    payload = _day_payload(date)
    payload["reclassificados"] = n
    return payload


@app.put("/api/day/{date}/blocks")
def put_blocks(date: str, body: dict = Body(...)):
    incoming = body.get("blocks") or []
    existing = {b["id"]: b for b in db.q("SELECT * FROM blocks WHERE date=?", (date,))}
    kept = set()
    for b in incoming:
        start, end = _ts(date, b["start"]), _ts(date, b["end"])
        fields = (start, end, b.get("kind") or "work", b.get("projeto"), b.get("ticket"),
                  b.get("atividade"), b.get("descricao"))
        old = existing.get(b.get("id"))
        if old:
            # Marca como editado se a classificação mudou (bloco sai do alvo do
            # "Reclassificar IA" e a divergência vira correção no approve).
            edited = old["edited"] or any(
                (b.get(f) or None) != (old[f] or None)
                for f in ("projeto", "ticket", "atividade", "descricao"))
            db.ex(
                "UPDATE blocks SET start_ts=?, end_ts=?, kind=?, projeto=?, ticket=?, "
                "atividade=?, descricao=?, edited=? WHERE id=?",
                fields + (1 if edited else 0, b["id"]))
            kept.add(b["id"])
        else:
            db.ex(
                "INSERT INTO blocks(start_ts, end_ts, kind, projeto, ticket, atividade, "
                "descricao, date, context_key, evidence, edited) "
                "VALUES(?,?,?,?,?,?,?,?,'manual','{}',1)",
                fields + (date,))
    for bid in set(existing) - kept:
        db.ex("DELETE FROM blocks WHERE id=?", (bid,))
    return _day_payload(date)


@app.post("/api/day/{date}/approve")
def approve(date: str):
    rows = db.q("SELECT * FROM blocks WHERE date=? ORDER BY start_ts", (date,))
    if not rows:
        raise HTTPException(400, "Nada para aprovar - gere a proposta primeiro.")
    now = int(time.time())
    # Re-aprovar substitui as correções do dia em vez de duplicá-las (elas alimentam
    # o few-shot do classifier; duplicata envenena o aprendizado).
    db.ex("DELETE FROM corrections WHERE date=?", (date,))
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
    return _resumo(date, rows)


@app.post("/api/day/{date}/clockify")
def push_clockify(date: str):
    """Envia os Lançamentos do dia aprovado ao Clockify (ADR-0002). Reenvio
    substitui os lançamentos criados anteriormente pela ferramenta."""
    d = _day_row(date)
    if not d or d["status"] != "aprovado":
        raise HTTPException(400, "Aprove o dia antes de enviar ao Clockify.")
    try:
        return clockify.push_day(date)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/day/{date}/export.csv")
def export_csv(date: str):
    rows = db.q(
        "SELECT * FROM blocks WHERE date=? AND kind='work' ORDER BY start_ts", (date,))
    if not rows:
        raise HTTPException(400, "Nada para exportar.")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Início", "Fim", "Duração", "Projeto", "Ticket",
                "Atividade", "Descrição"])
    for b in rows:
        dur = b["end_ts"] - b["start_ts"]
        w.writerow([date, _hm(b["start_ts"]), _hm(b["end_ts"]),
                    f"{dur // 3600:02d}:{dur % 3600 // 60:02d}:00",
                    b["projeto"] or "", b["ticket"] or "", b["atividade"] or "",
                    b["descricao"] or ""])
    return Response(
        "﻿" + buf.getvalue(),  # BOM: Excel pt-BR abre UTF-8 corretamente
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="delacao-{date}.csv"'},
    )
