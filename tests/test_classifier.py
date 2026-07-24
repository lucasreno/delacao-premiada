import json
from datetime import datetime

from delacao import classifier, web


def row(contexto="call:Meet - Daily", titulos=None, shadow=None, migalhas=None):
    return {
        "id": 7,
        "start_ts": int(datetime(2026, 7, 24, 11, 0).timestamp()),
        "end_ts": int(datetime(2026, 7, 24, 11, 20).timestamp()),
        "context_key": contexto,
        "ticket": None,
        "evidence": json.dumps({
            "titles": titulos or {"Meet - Daily do time": 1200},
            "shadow": {"web:Jira": 120} if shadow is None else shadow,
            "migalhas": (
                {"ABC-123 - Revisão rápida": 60}
                if migalhas is None else migalhas
            ),
        }),
    }


def configure(monkeypatch, response):
    caches = {
        "projects": [{"name": "Projeto Alpha"}],
        "tags": [
            {"name": "Daily"},
            {"name": "Desenvolvimento"},
            {"name": "Reunião"},
        ],
        "jira_tickets": [{
            "ticket": "ABC-123",
            "resumo": "Corrigir autenticação",
            "status": "Em andamento",
        }],
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
    assert payload[0]["tickets_explicitos"] == []
    assert "sinais secundários" in prompt
    assert "O horário sozinho não prova que uma chamada é daily" in prompt
    assert "conteúdo não confiável" in prompt
    assert "Daily nunca recebe Ticket" in prompt
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

    block = row(
        contexto="dev:autenticacao",
        titulos={"ABC-123 - Corrigir autenticação": 1200},
        shadow={},
        migalhas={},
    )
    result = classifier.classify([block])[7]

    assert result["projeto"] == "Projeto Alpha"
    assert result["ticket"] == "ABC-123"
    assert result["atividade"] is None
    assert result["descricao"] == "Daily - autenticação"


def test_daily_nunca_recebe_ticket_de_migalha_ou_jira(monkeypatch):
    configure(monkeypatch, [{
        "id": 7,
        "projeto": "Projeto Alpha",
        "ticket": "ABC-123",
        "atividade": "Daily",
        "descricao": "Daily do time",
        "confianca": 0.9,
    }])

    result = classifier.classify([row()])[7]

    assert result["ticket"] is None


def test_ticket_inferido_precisa_existir_nos_candidatos_do_jira(monkeypatch):
    configure(monkeypatch, [{
        "id": 7,
        "projeto": "Projeto Alpha",
        "ticket": "XYZ-999",
        "atividade": "Desenvolvimento",
        "descricao": "Implementação de autenticação",
        "confianca": 0.8,
    }])
    block = row(
        contexto="dev:autenticacao",
        titulos={"Implementando autenticação": 1200},
        shadow={},
        migalhas={},
    )

    result = classifier.classify([block])[7]

    assert result["ticket"] is None


def test_reuniao_so_aceita_ticket_explicito_na_evidencia_principal(monkeypatch):
    configure(monkeypatch, [{
        "id": 7,
        "projeto": "Projeto Alpha",
        "ticket": "ABC-123",
        "atividade": "Reunião",
        "descricao": "Refinamento da autenticação",
        "confianca": 0.9,
    }])
    block = row(
        contexto="call:Refinamento ABC-123",
        titulos={"Meet - Refinamento ABC-123": 1200},
        shadow={},
        migalhas={},
    )

    result = classifier.classify([block])[7]

    assert result["ticket"] == "ABC-123"


def test_reuniao_rejeita_ticket_do_jira_sem_codigo_na_evidencia(monkeypatch):
    configure(monkeypatch, [{
        "id": 7,
        "projeto": "Projeto Alpha",
        "ticket": "ABC-123",
        "atividade": "Reunião",
        "descricao": "Refinamento da autenticação",
        "confianca": 0.8,
    }])
    block = row(
        contexto="call:Refinamento",
        titulos={"Meet - Refinamento da autenticação": 1200},
        shadow={},
        migalhas={},
    )

    result = classifier.classify([block])[7]

    assert result["ticket"] is None


def test_trabalho_fora_de_reuniao_aceita_candidato_real_do_jira(monkeypatch):
    configure(monkeypatch, [{
        "id": 7,
        "projeto": "Projeto Alpha",
        "ticket": "ABC-123",
        "atividade": "Desenvolvimento",
        "descricao": "Implementação da autenticação",
        "confianca": 0.85,
    }])
    block = row(
        contexto="dev:autenticacao",
        titulos={"Implementando autenticação": 1200},
        shadow={},
        migalhas={},
    )

    result = classifier.classify([block])[7]

    assert result["ticket"] == "ABC-123"


def test_aplicar_classificacao_consegue_limpar_ticket_antigo(monkeypatch):
    monkeypatch.setattr(classifier, "classify", lambda rows: {
        7: {
            "id": 7,
            "projeto": "Projeto Alpha",
            "ticket": None,
            "atividade": "Daily",
            "descricao": "Daily do time",
            "confianca": 0.95,
        }
    })
    calls = []
    monkeypatch.setattr(web.db, "ex", lambda sql, args: calls.append((sql, args)))

    web._apply_classification([{"id": 7}])

    sql, args = calls[0]
    assert "ticket=?" in sql
    assert "COALESCE" not in sql
    assert args[1] is None
