"""Cliente do Clockify: leitura do espaço de opções (Projetos, Atividades) e do
histórico para o bootstrap; escrita restrita ao envio de dias aprovados (ADR-0002 —
só cria Lançamentos e só apaga os que a própria ferramenta criou)."""

from datetime import datetime, timezone

import httpx

from . import db
from .segmenter import TICKET_RE

BASE = "https://api.clockify.me/api/v1"


def _get(key, path, params=None):
    r = httpx.get(BASE + path, headers={"X-Api-Key": key}, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(key, path, body):
    r = httpx.post(BASE + path, headers={"X-Api-Key": key}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _delete(key, path):
    r = httpx.delete(BASE + path, headers={"X-Api-Key": key}, timeout=30)
    if r.status_code != 404:  # já não existe = objetivo atingido
        r.raise_for_status()


def _iso_utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync():
    key = db.setting("clockify_api_key")
    if not key:
        raise RuntimeError("Chave do Clockify não configurada (Configurações).")
    me = _get(key, "/user")
    ws, uid = me["activeWorkspace"], me["id"]

    projects = _get(key, f"/workspaces/{ws}/projects", {"page-size": 200, "archived": "false"})
    tags = _get(key, f"/workspaces/{ws}/tags", {"page-size": 200})

    entries = []
    for page in (1, 2, 3):
        batch = _get(key, f"/workspaces/{ws}/user/{uid}/time-entries",
                     {"page-size": 200, "page": page})
        entries += batch
        if len(batch) < 200:
            break

    pmap = {p["id"]: p["name"] for p in projects}
    tmap = {t["id"]: t["name"] for t in tags}
    boot = []
    for e in entries:
        desc = e.get("description") or ""
        proj = pmap.get(e.get("projectId") or "")
        etags = [tmap[t] for t in (e.get("tagIds") or []) if t in tmap]
        if not proj:
            continue
        m = TICKET_RE.search(desc)
        boot.append(
            {
                "evidencia": {"descricao_historica": desc},
                "resposta": {
                    "projeto": proj,
                    "ticket": m.group(1) if m else None,
                    "atividade": etags[0] if etags else None,
                    "descricao": desc,
                },
            }
        )

    db.cache_set("projects", [{"id": p["id"], "name": p["name"]} for p in projects])
    db.cache_set("tags", [{"id": t["id"], "name": t["name"]} for t in tags])
    db.cache_set("bootstrap_examples", boot[:40])
    return {"projetos": len(projects), "atividades": len(tags), "lancamentos_lidos": len(entries)}


def push_day(date):
    """Cria no Clockify os Lançamentos do dia aprovado. Reenvio substitui: os ids
    criados ficam em cache (`clockify_entries:{date}`) e são apagados antes de
    recriar — a ferramenta nunca toca em lançamentos que não criou."""
    key = db.setting("clockify_api_key")
    if not key:
        raise RuntimeError("Chave do Clockify não configurada (Configurações).")
    rows = db.q("SELECT * FROM blocks WHERE date=? AND kind='work' ORDER BY start_ts", (date,))
    if not rows:
        raise RuntimeError("Nenhum Lançamento para enviar.")
    ws = _get(key, "/user")["activeWorkspace"]
    projects = {p["name"]: p["id"] for p in (db.cache_get("projects") or [])}
    tags = {t["name"]: t["id"] for t in (db.cache_get("tags") or [])}
    if not projects:
        raise RuntimeError("Sincronize o Clockify antes de enviar (Configurações).")

    cache_key = f"clockify_entries:{date}"
    substituidos = 0
    for eid in db.cache_get(cache_key) or []:
        _delete(key, f"/workspaces/{ws}/time-entries/{eid}")
        substituidos += 1

    created, erros = [], []
    tasks_by_project = {}  # Ticket = Task do Clockify; busca preguiçosa por Projeto
    for b in rows:
        rotulo = f"{_hm_local(b['start_ts'])}–{_hm_local(b['end_ts'])}"
        pid = projects.get(b["projeto"] or "")
        if not pid:
            erros.append(f"{rotulo}: projeto '{b['projeto'] or '?'}' não existe no Clockify")
            continue
        task_id = None
        if b["ticket"]:
            if pid not in tasks_by_project:
                tasks_by_project[pid] = _get(
                    key, f"/workspaces/{ws}/projects/{pid}/tasks", {"page-size": 200})
            task_id = next(
                (t["id"] for t in tasks_by_project[pid]
                 if b["ticket"].lower() in (t.get("name") or "").lower()), None)
        desc = b["descricao"] or ""
        if b["ticket"] and not task_id:  # sem Task correspondente, o código vai na Descrição
            desc = f"{b['ticket']} — {desc}".strip(" —")
        body = {"start": _iso_utc(b["start_ts"]), "end": _iso_utc(b["end_ts"]),
                "projectId": pid, "description": desc}
        if task_id:
            body["taskId"] = task_id
        if b["atividade"] and b["atividade"] in tags:
            body["tagIds"] = [tags[b["atividade"]]]
        try:
            created.append(_post(key, f"/workspaces/{ws}/time-entries", body)["id"])
        except httpx.HTTPStatusError as e:
            erros.append(f"{rotulo}: {e.response.status_code} {e.response.text[:120]}")
    db.cache_set(cache_key, created)
    return {"criados": len(created), "substituidos": substituidos, "erros": erros}


def _hm_local(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")
