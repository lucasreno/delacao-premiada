"""Classifica Blocos de Trabalho em (Projeto, Ticket, Atividade, Descrição)
via OpenRouter, aprendendo com o histórico do Clockify e as correções da Revisão."""

import json
import re

import httpx

from . import config, db


def _openrouter(messages, model):
    key = db.setting("openrouter_api_key")
    if not key:
        raise RuntimeError("Chave do OpenRouter não configurada (Configurações).")
    r = httpx.post(
        config.OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": messages, "temperature": 0.2},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse_json_array(text):
    text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text.strip())
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < 0:
        raise ValueError(f"resposta sem JSON: {text[:200]}")
    return json.loads(text[i : j + 1])


def examples(limit=30):
    """Correções da Revisão (mais recentes primeiro) + bootstrap do histórico Clockify."""
    ex = [
        {"evidencia": json.loads(c["evidence"]), "resposta": json.loads(c["final"])}
        for c in db.q("SELECT evidence, final FROM corrections ORDER BY id DESC LIMIT ?", (limit,))
    ]
    boot = db.cache_get("bootstrap_examples") or []
    return ex + boot[: max(0, limit - len(ex))]


def classify(blocks):
    """Recebe rows de blocks (kind=work); retorna {block_id: resultado}."""
    projects = [p["name"] for p in (db.cache_get("projects") or [])]
    tags = [t["name"] for t in (db.cache_get("tags") or [])]
    jira = db.cache_get("jira_tickets") or []
    sys_prompt = (
        "Você classifica blocos de trabalho de um programador em lançamentos de horas do Clockify.\n"
        f"Projetos disponíveis: {json.dumps(projects, ensure_ascii=False)}\n"
        f"Atividades disponíveis (escolha exatamente uma): {json.dumps(tags, ensure_ascii=False)}\n"
        "Para cada bloco de entrada, produza: projeto (da lista, ou null se impossível), "
        "ticket (padrão ABC-123 se houver, senão null), atividade (da lista), "
        "descricao (curta, em português, dizendo o assunto do trabalho) e confianca (0 a 1).\n"
        "Blocos com contexto 'call:' são reuniões: use a atividade de reunião/daily adequada "
        "e o nome da reunião na descrição.\n"
        "O campo 'migalhas_absorvidas' lista verificações rápidas (< 5 min) engolidas pelo "
        "bloco (ex.: devchecks, conferências no Jira); quando forem assunto distinto do bloco, "
        "cite-as brevemente no fim da descricao.\n"
    )
    if jira:
        sys_prompt += (
            "Tickets recentes do usuário no Jira (candidatos prováveis): "
            f"{json.dumps(jira, ensure_ascii=False)}\n"
            "Os títulos de janela raramente trazem o código do ticket: infira-o cruzando "
            "títulos (branch, arquivo, resumo) com esses candidatos. Não invente ticket "
            "fora da lista sem código explícito no título.\n"
        )
    sys_prompt += (
        f"Exemplos reais de lançamentos deste usuário:\n"
        f"{json.dumps(examples(), ensure_ascii=False)}\n"
        'Responda SOMENTE um array JSON: [{"id":1,"projeto":"...","ticket":null,'
        '"atividade":"...","descricao":"...","confianca":0.9}]'
    )
    payload = []
    for b in blocks:
        ev = json.loads(b["evidence"]) if isinstance(b["evidence"], str) else b["evidence"]
        payload.append(
            {
                "id": b["id"],
                "duracao_min": (b["end_ts"] - b["start_ts"]) // 60,
                "contexto": b["context_key"],
                "titulos": ev.get("titles", {}),
                "atividade_paralela_durante_chamada": ev.get("shadow") or None,
                "migalhas_absorvidas": ev.get("migalhas") or None,
            }
        )
    model = db.setting("model") or config.DEFAULT_MODEL
    content = _openrouter(
        [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        model,
    )
    return {r["id"]: r for r in _parse_json_array(content) if isinstance(r, dict) and "id" in r}
