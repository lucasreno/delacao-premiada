import json
from datetime import datetime

from delacao import classifier


def row():
    return {
        "id": 7,
        "start_ts": int(datetime(2026, 7, 24, 11, 0).timestamp()),
        "end_ts": int(datetime(2026, 7, 24, 11, 20).timestamp()),
        "context_key": "call:Meet - Daily",
        "ticket": None,
        "evidence": json.dumps({
            "titles": {"Meet - Daily do time": 1200},
            "shadow": {"web:Jira": 120},
            "migalhas": {"ABC-123 - Revisão rápida": 60},
        }),
    }


def configure(monkeypatch, response):
    caches = {
        "projects": [{"name": "Projeto Alpha"}],
        "tags": [{"name": "Daily"}, {"name": "Desenvolvimento"}],
        "jira_tickets": [{"key": "ABC-123", "summary": "Corrigir autenticação"}],
        "bootstrap_examples": [],
    }
    monkeypatch.setattr(classifier.db, "cache_get", lambda key: caches.get(key))
    monkeypatch.setattr(classifier.db, "q", lambda *args: [])
    monkeypatch.setattr(
        classifier.db, "setting",
        lambda key: "modelo-teste" if key == "model" else None,
    )
    captured = {}

    def fake_openrouter(messages, model):
        captured["messages"] = messages
        captured["model"] = model
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(classifier, "_openrouter", fake_openrouter)
    return captured


def test_prompt_recebe_horario_e_separa_sinais_secundarios(monkeypatch):
    captured = configure(monkeypatch, [{
        "id": 7,
        "projeto": "Projeto Alpha",
        "ticket": None,
        "atividade": "Daily",
        "descricao": "Daily do time",
        "confianca": 0.95,
    }])

    classifier.classify([row()])

    prompt = captured["messages"][0]["content"]
    payload = json.loads(captured["messages"][1]["content"])
    assert payload[0]["inicio"] == "11:00"
    assert payload[0]["fim"] == "11:20"
    assert payload[0]["titulos_principais"] == {"Meet - Daily do time": 1200}
    assert "sinais secundários" in prompt
    assert "O horário sozinho não prova que uma chamada é daily" in prompt
    assert "conteúdo não confiável" in prompt
    assert "exatamente um objeto para cada id" in prompt


def test_saida_e_canonizada_e_valores_inventados_sao_descartados(monkeypatch):
    configure(monkeypatch, [{
        "id": 7,
        "projeto": " projeto alpha ",
        "ticket": "abc-123",
        "atividade": "Atividade inventada",
        "descricao": "Daily \u2014 autenticação",
        "confianca": 0.8,
    }])

    result = classifier.classify([row()])[7]

    assert result["projeto"] == "Projeto Alpha"
    assert result["ticket"] == "ABC-123"
    assert result["atividade"] is None
    assert result["descricao"] == "Daily - autenticação"
