"""Cliente somente-leitura do Jira: tickets recentes do usuário viram candidatos
para o classifier (os títulos de janela raramente trazem o código ABC-123)."""

import time

import httpx

from . import db

JQL_DEFAULT = "assignee = currentUser() AND updated >= -21d ORDER BY updated DESC"
MAX_AGE_S = 1800  # idade do cache a partir da qual o propose re-sincroniza


def _search(base, auth, headers, params):
    # Jira Cloud aposentou /rest/api/2/search em favor de /search/jql; Server/DC
    # ainda usa o antigo. Tenta o clássico e cai para o novo se não existir.
    r = httpx.get(base + "/rest/api/2/search", params=params, auth=auth,
                  headers=headers, timeout=30)
    if r.status_code in (404, 410):
        r = httpx.get(base + "/rest/api/3/search/jql", params=params, auth=auth,
                      headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def configured():
    return bool(db.setting("jira_base_url") and db.setting("jira_api_token"))


def sync():
    base = (db.setting("jira_base_url") or "").rstrip("/")
    token = db.setting("jira_api_token")
    if not base or not token:
        raise RuntimeError("Jira não configurado (Configurações).")
    email = db.setting("jira_email")
    # Cloud autentica com basic email:token; Server/DC com PAT no bearer.
    auth = (email, token) if email else None
    headers = {} if email else {"Authorization": f"Bearer {token}"}
    jql = db.setting("jira_jql") or JQL_DEFAULT

    data = _search(base, auth, headers,
                   {"jql": jql, "fields": "summary,status", "maxResults": 50})
    tickets = [
        {
            "ticket": i["key"],
            "resumo": (i.get("fields") or {}).get("summary") or "",
            "status": ((i.get("fields") or {}).get("status") or {}).get("name") or "",
        }
        for i in data.get("issues", [])
    ]
    db.cache_set("jira_tickets", tickets)
    return {"tickets": len(tickets)}


def maybe_sync(max_age_s=MAX_AGE_S):
    """Re-sincroniza em silêncio se configurado e o cache estiver velho; nunca
    derruba o propose por falha do Jira."""
    if not configured():
        return
    row = db.q("SELECT ts FROM cache WHERE key='jira_tickets'")
    if row and time.time() - row[0]["ts"] < max_age_s:
        return
    try:
        sync()
    except Exception:
        pass
