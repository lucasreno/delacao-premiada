# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Ferramenta local que amostra **metadados de janela** (app ativo, título, ociosidade — nunca screenshots, ver `docs/adr/0001`) e propõe os Lançamentos do dia no Clockify, com o usuário como revisor final. Alvo de produção é Ubuntu/X11; Windows funciona via WinAPI e é usado para desenvolvimento.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m delacao        # coletor (thread) + UI de Revisão em http://127.0.0.1:8746
```

- `DP_DATA_DIR=/algum/dir` redireciona o SQLite (`data/data.db`) — use em dev para não sujar os dados reais.
- `python -m pytest` roda os testes (regras do `segmenter`); não há linter configurado.
- API keys (Clockify, OpenRouter) ficam na tabela `settings` do SQLite, configuradas pela UI — não em env vars ou arquivos.
- Deploy no Ubuntu: `deploy/delacao.service` (systemd user unit); `pip install -e .` dá o comando `delacao`.

## Language

Todo o projeto — código, comentários, UI, prompts — é em **português**. `CONTEXT.md` define o vocabulário do domínio e os sinônimos a evitar. Os termos centrais, usados literalmente no código:

- **Lançamento** (registro no Clockify), **Atividade** (= Tag no Clockify), **Ticket** (= Task no Clockify, padrão `ABC-123`), **Descrição**
- **Bloco de Trabalho** (intervalo contíguo dominado por um contexto; vira um Lançamento), **Migalha** (trabalho < 5 min), **Lacuna** (vazio longo que vira pergunta na Revisão)
- **Jornada/ponto** (períodos entrada/saída), **Timesheet Proposto**, **Revisão**

## Architecture

Pipeline linear, um módulo por estágio, tudo compartilhando o SQLite via `db.py`:

1. **`collector.py`** — thread daemon iniciada em `__main__.py`. Backends por plataforma (`X11Backend`/`WindowsBackend`, mesma interface: `active_window`, `idle_ms`, `all_titles`) amostram a cada `POLL_S` (5s) e inserem em `samples`. Detecção de chamada (Meet/Teams) por regex sobre **todos** os títulos de janela (`call_patterns` em settings, default em `config.py`). Em erro, zera o backend e reconecta.
2. **`segmenter.py`** — funções puras, sem I/O: amostras → spans (`build_spans`) → Blocos/Migalhas/Lacunas (`consolidate`) → recorte ao ponto (`fit_to_period`). As regras (chamada vence ociosidade; Migalha absorvida; vazio ≤ 15 min emendado) estão no docstring do módulo; os limiares em `config.py`. `context_key()` é a heurística que reduz app+título a uma chave (`ticket:`, `dev:`, `web:`, `call:`...). O campo `shadow` registra atividade paralela durante chamadas; o campo `migalhas` guarda os títulos das Migalhas absorvidas pelo bloco (a evidência de devchecks e conferências rápidas chega à IA em vez de ser descartada).
3. **`classifier.py`** — um único chamado em lote ao OpenRouter (texto, não visão) classifica os blocos do dia em (Projeto, Ticket, Atividade, Descrição). Few-shot: correções da Revisão (tabela `corrections`) + bootstrap do histórico Clockify (cache `bootstrap_examples`).
4. **`web.py`** — FastAPI, toda a API + estado do dia. Frontend é um único `delacao/static/index.html` vanilla, servido estático.
5. **`clockify.py`** — leitura (sync de Projetos/Tags e histórico para o bootstrap) + **um único caminho de escrita**: `push_day` envia os Lançamentos de um dia `aprovado` quando o usuário clica "Enviar ao Clockify" (ver `docs/adr/0002`). Ids criados ficam em `cache["clockify_entries:{date}"]`; reenvio apaga e recria só esses — a ferramenta nunca toca em lançamentos que não criou.
6. **`jira.py`** — cliente **somente leitura**: cacheia os tickets recentes do usuário (`cache["jira_tickets"]`) como candidatos para o classifier, já que títulos de janela raramente trazem o código `ABC-123`. Configurado por settings (`jira_base_url`, `jira_email`, `jira_api_token`); `maybe_sync()` roda no propose sem derrubá-lo em caso de falha.

### Fluxos que atravessam módulos

- **Proposta é regenerável**: `POST /api/day/{date}/propose` apaga e recria `blocks`/`migalhas` a partir das `samples` (fonte da verdade — quase imutável: amostras de dias aprovados são purgadas após `retention_days`, default 30, pelo coletor). Edições manuais vão via `PUT /api/day/{date}/blocks` (marcam `blocks.edited=1`) e são perdidas se repropuser — a UI pede confirmação. `POST /api/day/{date}/classify` re-roda **só** a IA: sem `ids`, os blocos não editados; com `ids`, exatamente esses (e zera `edited`).
- **Loop de aprendizado**: no `approve`, blocos cujo `final` difere do `proposed` da IA viram linhas em `corrections`, que alimentam os exemplos do `classifier` nas próximas propostas. É o mecanismo central do produto — mudanças na Revisão ou no classifier devem preservá-lo. Re-aprovar apaga e regrava as correções do dia (idempotente, não duplica). A `confianca` da IA é gravada no bloco e a Revisão destaca os duvidosos (< 0.7).
- **`db.py`**: conexão SQLite singleton com lock (o coletor e o FastAPI compartilham threads); helpers `q`/`ex`, tabelas `settings` e `cache` como key-value; `MIGRATIONS` são `ALTER TABLE` idempotentes para bancos antigos.
- **Frontend**: todo valor interpolado em HTML passa por `esc()` — títulos de janela e saídas da IA são dado hostil. Promover Migalha fatia o bloco que a absorveu (senão o tempo contaria duas vezes). A timeline usa paleta categórica fixa: cor segue o Projeto, nunca a posição.

## Decisões registradas

ADRs em `docs/adr/`. A restrição estrutural da v1: o pipeline inteiro é orientado a **eventos de texto** — adicionar visão/screenshots depois é aditivo, mas trocar essa espinha dorsal não é. Wayland não é suportado (leitura de janela ativa exige X11).
