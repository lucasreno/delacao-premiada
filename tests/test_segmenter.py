"""Testes das regras de segmentação (funções puras — ver docstring do módulo).

Limiares reais de config: POLL_S=5, IDLE_AFTER_S=180, MIGALHA_S=300, STITCH_S=900,
NO_DATA_GAP_S=90. Os testes usam os defaults para validar o comportamento de produção.
"""

from collections import Counter

from delacao.segmenter import (
    build_spans, consolidate, context_key, fit_to_period, sample_key, segment,
)


def amostra(ts, app="pycharm", title="delacao – segmenter.py", idle=0, call=None):
    return {"ts": ts, "app": app, "title": title, "idle_ms": idle,
            "in_call": 1 if call else 0, "call_title": call}


def span(key, start, end, titles=None):
    return {"key": key, "start": start, "end": end,
            "titles": Counter(titles or {}), "shadow": Counter()}


class TestContextKey:
    def test_ticket_no_titulo_vence_tudo(self):
        assert context_key("chrome", "PROJ-123 Corrigir bug — Jira") == "ticket:PROJ-123"

    def test_jetbrains_usa_primeiro_segmento(self):
        assert context_key("pycharm", "delacao – segmenter.py") == "dev:delacao"

    def test_vscode_descarta_sufixo_do_produto(self):
        assert context_key("Code", "web.py - delacao - Visual Studio Code") == "dev:delacao"

    def test_navegador_descarta_nome_do_navegador(self):
        assert context_key("firefox", "Documentação FastAPI - Mozilla Firefox") == \
            "web:Documentação FastAPI"

    def test_terminal(self):
        assert context_key("gnome-terminal", "~/delacao") == "term:terminal"

    def test_app_desconhecido(self):
        assert context_key("blender", "untitled") == "app:blender"
        assert context_key(None, None) == "app:desconhecido"


class TestSampleKey:
    def test_chamada_vence_ociosidade(self):
        s = amostra(0, idle=10_000_000, call="Meet — abc-defg-hij")
        assert sample_key(s, 180) == "call:Meet — abc-defg-hij"

    def test_ocioso_vira_vazio(self):
        assert sample_key(amostra(0, idle=180_000), 180) == "__vazio__"

    def test_ativo_usa_context_key(self):
        assert sample_key(amostra(0), 180) == "dev:delacao"


class TestBuildSpans:
    def test_mesma_chave_emenda_num_span(self):
        spans = build_spans([amostra(0), amostra(5), amostra(10)])
        assert len(spans) == 1
        assert spans[0]["start"] == 0 and spans[0]["end"] == 15
        assert spans[0]["titles"]["delacao – segmenter.py"] == 15

    def test_buraco_sem_dados_vira_vazio(self):
        spans = build_spans([amostra(0), amostra(200)])  # buraco de 200s > 90s
        assert [sp["key"] for sp in spans] == ["dev:delacao", "__vazio__", "dev:delacao"]
        assert spans[1]["start"] == 5 and spans[1]["end"] == 200

    def test_shadow_registra_atividade_paralela_na_chamada(self):
        # Em chamada, mas com janela ativa de dev e sem ociosidade → shadow
        spans = build_spans([amostra(0, call="Meet — reunião"),
                             amostra(5, call="Meet — reunião")])
        assert len(spans) == 1
        assert spans[0]["key"] == "call:Meet — reunião"
        assert spans[0]["shadow"]["dev:delacao"] == 10


class TestConsolidate:
    def test_migalha_absorvida_e_delatada(self):
        spans = [span("dev:a", 0, 600), span("web:x", 600, 660), span("dev:a", 660, 1260)]
        blocks, migalhas = consolidate(spans)
        assert len(blocks) == 1  # interrupção não quebra o bloco
        assert blocks[0]["start"] == 0 and blocks[0]["end"] == 1260
        assert len(migalhas) == 1 and migalhas[0]["key"] == "web:x"

    def test_vazio_curto_emendado_ao_anterior(self):
        spans = [span("dev:a", 0, 600), span("__vazio__", 600, 900), span("dev:b", 900, 1500)]
        blocks, _ = consolidate(spans)
        assert [b["kind"] for b in blocks] == ["work", "work"]
        assert blocks[0]["end"] == 900  # a pausa curta pertence ao bloco anterior

    def test_vazio_longo_vira_lacuna(self):
        spans = [span("dev:a", 0, 600), span("__vazio__", 600, 1600), span("dev:b", 1600, 2200)]
        blocks, _ = consolidate(spans)
        assert [b["kind"] for b in blocks] == ["work", "lacuna", "work"]

    def test_migalha_no_inicio_adianta_o_proximo_bloco(self):
        spans = [span("web:x", 0, 60), span("dev:a", 60, 660)]
        blocks, migalhas = consolidate(spans)
        assert len(blocks) == 1 and blocks[0]["start"] == 0
        assert len(migalhas) == 1

    def test_migalha_absorvida_deixa_titulos_na_evidencia(self):
        spans = [span("dev:a", 0, 600),
                 span("dev:b", 600, 660, {"outro-repo – Devcheck.java": 60}),
                 span("dev:a", 660, 1260)]
        blocks, _ = consolidate(spans)
        assert len(blocks) == 1
        assert blocks[0]["migalhas"] == Counter({"outro-repo – Devcheck.java": 60})

    def test_migalha_no_inicio_deixa_titulos_no_bloco_seguinte(self):
        spans = [span("web:x", 0, 60, {"PROJ board - Jira": 60}), span("dev:a", 60, 660)]
        blocks, _ = consolidate(spans)
        assert blocks[0]["migalhas"] == Counter({"PROJ board - Jira": 60})

    def test_blocos_contiguos_da_mesma_chave_se_fundem(self):
        spans = [span("dev:a", 0, 600, {"t1": 600}), span("__vazio__", 600, 700),
                 span("dev:a", 700, 1300, {"t2": 600})]
        blocks, _ = consolidate(spans)
        assert len(blocks) == 1
        assert blocks[0]["titles"] == Counter({"t1": 600, "t2": 600})


class TestFitToPeriod:
    def blocos(self):
        return [{"kind": "work", "key": "a", "start": 100, "end": 200,
                 "titles": Counter(), "shadow": Counter()},
                {"kind": "work", "key": "b", "start": 300, "end": 400,
                 "titles": Counter(), "shadow": Counter()}]

    def test_estica_bordas_e_gruda_buracos_internos(self):
        fitted = fit_to_period(self.blocos(), 50, 450)
        assert fitted[0]["start"] == 50      # primeiro estica até a entrada
        assert fitted[0]["end"] == 300       # buraco interno gruda no anterior
        assert fitted[-1]["end"] == 450      # último estica até a saída

    def test_recorta_e_descarta_fora_do_periodo(self):
        fitted = fit_to_period(self.blocos(), 150, 250)
        assert len(fitted) == 1
        assert (fitted[0]["start"], fitted[0]["end"]) == (150, 250)

    def test_vazio_retorna_vazio(self):
        assert fit_to_period([], 0, 100) == []


class TestSegment:
    def test_sem_amostras(self):
        assert segment([], []) == ([], [])

    def test_dia_sem_ponto_usa_intervalo_das_amostras(self):
        samples = [amostra(t) for t in range(0, 600, 5)]
        blocks, _ = segment(samples, [])
        assert len(blocks) == 1
        assert blocks[0]["start"] == 0 and blocks[0]["end"] == 600

    def test_amostras_fora_do_ponto_sao_ignoradas(self):
        samples = [amostra(t) for t in range(0, 600, 5)]
        samples += [amostra(t, app="chrome", title="YouTube") for t in range(5000, 5600, 5)]
        blocks, _ = segment(samples, [(0, 600)])
        assert len(blocks) == 1
        assert blocks[0]["end"] == 600
