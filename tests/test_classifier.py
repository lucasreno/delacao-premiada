import json
from datetime import datetime

import httpx
import pytest

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

    def fake_openrouter(messages, model, max_completion_tokens=None):
        captured["messages"] = messages
        captured["model"] = model
        captured["max_completion_tokens"] = max_completion_tokens
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
    assert captured["max_completion_tokens"] == 512


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


def test_terra_usa_raciocinio_medio_e_json_schema_sem_temperature(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        classifier.db,
        "setting",
        lambda key: "chave-teste" if key == "openrouter_api_key" else None,
    )

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}

    def fake_post(url, headers, json, timeout):
        captured["body"] = json
        return Response()

    monkeypatch.setattr(classifier.httpx, "post", fake_post)

    classifier._openrouter(
        [{"role": "user", "content": "[]"}],
        "openai/gpt-5.6-terra",
        max_completion_tokens=640,
    )

    body = captured["body"]
    assert "temperature" not in body
    assert body["reasoning"] == {"effort": "medium", "exclude": True}
    assert body["max_completion_tokens"] == 640
    assert body["response_format"]["type"] == "json_schema"
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["type"] == "object"
    classifications = schema["schema"]["properties"]["classificacoes"]
    assert classifications["type"] == "array"


def test_modelo_nao_gpt_56_mantem_requisicao_compativel(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        classifier.db,
        "setting",
        lambda key: "chave-teste" if key == "openrouter_api_key" else None,
    )

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}

    monkeypatch.setattr(
        classifier.httpx,
        "post",
        lambda url, headers, json, timeout: (
            captured.update({"body": json}) or Response()
        ),
    )

    classifier._openrouter(
        [{"role": "user", "content": "[]"}],
        "google/gemini-2.5-flash-lite",
        max_completion_tokens=640,
    )

    assert captured["body"]["temperature"] == 0.2
    assert "reasoning" not in captured["body"]
    assert "response_format" not in captured["body"]


def test_parser_aceita_objeto_de_saida_estruturada():
    content = json.dumps({
        "classificacoes": [{
            "id": 7,
            "projeto": "Projeto Alpha",
            "ticket": None,
            "atividade": "Daily",
            "descricao": "Daily do time",
            "confianca": 0.95,
        }]
    })

    result = classifier._parse_json_array(content)

    assert result[0]["id"] == 7


def test_erro_openrouter_inclui_mensagem_da_resposta(monkeypatch):
    monkeypatch.setattr(
        classifier.db,
        "setting",
        lambda key: "chave-teste" if key == "openrouter_api_key" else None,
    )
    request = httpx.Request("POST", classifier.config.OPENROUTER_URL)
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "Schema inválido: raiz deve ser object"}},
    )
    monkeypatch.setattr(classifier.httpx, "post", lambda *args, **kwargs: response)

    with pytest.raises(
        RuntimeError,
        match="Schema inválido: raiz deve ser object",
    ):
        classifier._openrouter(
            [{"role": "user", "content": "{}"}],
            "openai/gpt-5.6-terra",
            max_completion_tokens=640,
        )
