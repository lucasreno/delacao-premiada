# ADR 0002 — Escrita no Clockify pós-aprovação

Data: 2026-07-13. Status: aceita. Revisa a decisão original da v1 (cliente
Clockify somente-leitura).

## Contexto

Na v1 o Clockify era somente-leitura: o usuário copiava o resumo aprovado e
transcrevia os Lançamentos à mão. Na prática essa transcrição se mostrou o
maior atrito do fluxo — a Revisão já é o ponto de controle humano, e repetir
os mesmos dados manualmente no Clockify não adiciona segurança, só custo.

## Decisão

A ferramenta passa a **escrever no Clockify, com escopo mínimo e gatilho
explícito**:

- só envia dias com status `aprovado`, e só quando o usuário clica
  **Enviar ao Clockify** (nunca automaticamente);
- só **cria** Lançamentos; os ids criados ficam registrados em
  `cache["clockify_entries:{date}"]`;
- reenvio **substitui**: apaga exatamente os ids que a própria ferramenta
  criou para aquele dia (404 é ignorado) e recria — idempotente, sem duplicar;
- a ferramenta **nunca edita nem apaga** lançamentos que não criou;
- Ticket vira `taskId` quando existe Task com o código no nome dentro do
  Projeto; sem Task correspondente, o código vai no início da Descrição
  (mesmo lugar onde o histórico do usuário o carrega);
- Atividade vira a Tag homônima; Projeto inexistente no Clockify não derruba
  o envio: o bloco é pulado e reportado em `erros`.

## Consequências

- O resumo em texto e o CSV continuam existindo como saídas alternativas.
- A API key do Clockify agora precisa de permissão de escrita.
- O bootstrap e o sync de opções continuam somente-leitura; o único caminho
  de escrita é `clockify.push_day`.
