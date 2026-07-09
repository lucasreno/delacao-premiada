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
(systemd user service):

```ini
# ~/.config/systemd/user/delacao.service
[Unit]
Description=Delacao Premiada

[Service]
WorkingDirectory=%h/delacao-premiada
ExecStart=%h/delacao-premiada/.venv/bin/python -m delacao
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now delacao
```

## Fluxo diário

1. Trabalhe. (O coletor observa sozinho.)
2. No fim do expediente: informe o **ponto** ("09:00-12:00, 13:00-18:00"), clique **Gerar proposta**.
3. Revise: corrija projeto/atividade/descrição, resolva **Lacunas**, promova **Migalhas** se merecerem.
4. **Aprovar dia** → copie o resumo para o Clockify. Suas correções ensinam as próximas propostas.

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
- Wayland não é suportado na v1 (a leitura da janela ativa exige X11).
