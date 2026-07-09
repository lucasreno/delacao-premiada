# Delação Premiada

Ferramenta local, multiplataforma, que observa o trabalho do usuário na tela e propõe os lançamentos de horas do dia no Clockify, mantendo o usuário como revisor final.

## Language

**Lançamento**:
Um registro de tempo no Clockify: intervalo de tempo + Projeto + Atividade + Ticket (quando existe) + Descrição.
_Avoid_: entrada, registro, apontamento

**Descrição**:
Texto livre do Lançamento que diz o assunto do trabalho (ex.: o tema da reunião, o que foi feito no ticket).

**Projeto**:
A dimensão do Clockify à qual todo Lançamento pertence.

**Atividade**:
O tipo de trabalho sendo feito (dev, devcheck, daily, reunião interna, treinamento...), vindo de uma lista fechada da empresa; todo Lançamento tem exatamente uma. No Clockify é representada pela *Tag*.
_Avoid_: tag, tarefa, categoria

**Ticket**:
Identificador de tarefa rastreada (ex.: ZG-1234 no Jira) anexado ao Lançamento; opcional, pois parte do trabalho acontece sem ticket. No Clockify é representado pela *Task* dentro do Projeto.
_Avoid_: task, tarefa

**Bloco de Trabalho**:
Intervalo contíguo do dia com horários reais de início e fim, dominado por um mesmo trabalho (Projeto + Ticket + Atividade). Micro-interrupções curtas são absorvidas pelo bloco; interrupções longas o quebram em dois. Cada Bloco de Trabalho origina um Lançamento.
_Avoid_: sessão, período

**Jornada**:
Os períodos de trabalho do dia definidos pelos pares entrada/saída do ponto da empresa (normalmente 2 períodos, às vezes 3). O Timesheet Proposto deve preencher a Jornada por completo: a soma dos Lançamentos bate com o ponto, com divergência mínima.
_Avoid_: expediente, dia de trabalho

**Lacuna**:
Período dentro da Jornada sem atividade observada no computador. Lacunas curtas são emendadas ao Bloco de Trabalho anterior; lacunas longas viram pergunta na Revisão (trabalho fora do PC, pausa ou almoço).
_Avoid_: buraco, gap, ociosidade

**Migalha**:
Interrupção mais curta que o limiar de absorção (padrão: 5 minutos), engolida pelo Bloco de Trabalho onde caiu. As Migalhas do dia são dedatadas na Revisão, onde o usuário pode promovê-las a Lançamento próprio.
_Avoid_: fragmento, micro-tarefa

**Timesheet Proposto**:
O conjunto de Lançamentos do dia gerado pela ferramenta, aguardando Revisão.
_Avoid_: relatório, sugestão

**Revisão**:
O ato do usuário corrigir e aprovar o Timesheet Proposto. As correções feitas na Revisão são a matéria-prima do aprendizado da ferramenta.
