# Delação Premiada

Ferramenta local que observa **metadados de janela** (nunca o conteúdo da tela — ver `docs/adr/0001`)
e propõe os Lançamentos do dia no Clockify para sua Revisão. Domínio em `CONTEXT.md`.

## Rodando (Ubuntu / X11)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m delacao
```

Abra **http://127.0.0.1:8746**, entre em ⚙ Configurações e informe:

- **Clockify API key** (Perfil → Advanced → API key) — somente leitura: projetos, atividades e
  histórico para aprendizado inicial. Clique em **Sincronizar Clockify**.
- **OpenRouter API key** — classificação dos blocos (texto, em lote; centavos/dia).

O coletor amostra a janela ativa a cada 5s enquanto o processo roda. Deixe-o iniciar com a sessão
usando a unit versionada em `deploy/delacao.service`:

```bash
cp deploy/delacao.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now delacao
```

Alternativa ao `pip install -r requirements.txt`: `pip install -e .` instala o comando `delacao`
(e `pip install -e .[dev]` traz o pytest).

## Fluxo diário

1. Trabalhe. (O coletor observa sozinho.)
2. No fim do expediente: informe o **ponto** ("09:00-12:00, 13:00-18:00"), clique **Gerar proposta**.
3. Revise: a timeline mostra o dia de relance; blocos com **⚠ baixa confiança da IA** vêm
   destacados — comece por eles. Corrija projeto/atividade/descrição, resolva **Lacunas**,
   promova **Migalhas** se merecerem (o bloco que as absorveu é fatiado, sem dupla contagem).
   **Reclassificar IA** re-roda só a classificação, preservando horários e blocos editados (✎);
   o 🔁 de cada linha reclassifica um bloco só.
4. **Aprovar dia** → copie o resumo (por linha ou tudo) ou **Baixar CSV** e lance no Clockify.
   Suas correções ensinam as próximas propostas; re-aprovar substitui as correções do dia.

## Calibração nos primeiros dias de uso real

1. **Detecção de reunião**: os padrões de título do Meet/Teams são um palpite inicial
   (`DEFAULT_CALL_PATTERNS` em `delacao/config.py`). Após um dia com reuniões, confira na
   Revisão se elas apareceram com contexto `call:`. Se não, anote o título real da janela
   da reunião — o padrão precisa ser ajustado.
2. **Ociosidade no X11**: usa a extensão screensaver do X, com fallback para `xprintidle`.
   Garantia: `sudo apt install xprintidle`. Se Lacunas não aparecerem em pausas longas,
   a detecção de ociosidade é a suspeita.
3. **Classificação IA**: as primeiras propostas virão erradas — é esperado e é o mecanismo.
   Corrija na Revisão (não direto no Clockify): cada correção vira exemplo para as próximas.
   A qualidade deve melhorar visivelmente na primeira semana.

## Notas

- Windows também funciona (coletor via WinAPI) — útil para desenvolvimento.
- Dados ficam em `data/data.db` (SQLite). Nada sai da máquina além de: chamadas à API do Clockify
  e títulos de janela enviados ao OpenRouter para classificação.
- **Retenção**: amostras (títulos de janela são dado sensível) de dias aprovados são apagadas
  após 30 dias. Ajuste com a setting `retention_days` (0 = nunca apagar).
- Se a detecção de ociosidade quebrar no X11, o cabeçalho da Revisão avisa
  (⚠ "detecção de ociosidade indisponível") em vez de falhar calado.
- Wayland não é suportado na v1 (a leitura da janela ativa exige X11).
- Testes: `python -m pytest` (regras de segmentação).
