# Captura por metadados de janela, sem screenshots na v1

O projeto nasceu como "ferramenta que tira prints da tela e interpreta com IA a cada 15s" — mas ~2.000 imagens/dia num modelo de visão custaria caro (motivando até assinatura da Featherless) e carregaria conteúdo sensível de tela para a nuvem. Decidimos que a fonte primária de sinal são **metadados do sistema** (aplicativo ativo, título da janela, ociosidade, via X11), que carregam ~95% do contexto do trabalho do usuário a custo zero; a IA (via OpenRouter, provedor trocável) só entra em lote, sobre texto, para classificar Blocos de Trabalho e redigir Descrições — centavos por dia, tornando os créditos existentes do OpenRouter suficientes.

## Considered Options

- **Print + modelo de visão a cada 15s** — rejeitado: custo proibitivo, privacidade pior, e redundante quando o título da janela já entrega o contexto.
- **Modelo local (Ollama)** — rejeitado para a v1: qualidade inferior nas Descrições; fica como fallback trocável se a política de dados mudar.

## Consequences

- Screenshots seletivos (só em contexto ambíguo) podem entrar numa v2, **guiados por evidência das Revisões**, não por palpite.
- O pipeline inteiro é orientado a eventos de texto; adicionar visão depois é aditivo, mas trocar a espinha dorsal não é.
