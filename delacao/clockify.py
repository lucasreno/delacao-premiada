"""Cliente somente-leitura do Clockify: espaço de opções (Projetos, Atividades)
e histórico de Lançamentos como material de aprendizado inicial."""

import httpx

from . import db
from .segmenter import TICKET_RE

BASE = "https://api.clockify.me/api/v1"


def _get(key, path, params=None):
    r = httpx.get(BASE + path, headers={"X-Api-Key": key}, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


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
