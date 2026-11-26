---
name: guilda-quality
description: Validar mudanças da Guilda com a política de qualidade, revisar Clean Code com evidências ou analisar tendências dos relatórios locais e do CI.
---

# Qualidade da Guilda

Leia `quality.policy.json` e `docs/quality.md` na raiz do repositório. A política é a fonte
única de limites e exclusões; não altere seus valores para aprovar uma mudança.

## Validar uma mudança

Execute `uv run python -m quality fast` durante desenvolvimento. Antes de integração,
execute `uv run python -m quality all` sobre o commit limpo. PostgreSQL real precisa estar
disponível; os testes recriam bancos, portanto use servidor de testes e não compartilhe
esse servidor entre execuções simultâneas. `ci` e `mutation` também podem ser executados
separadamente, seguidos por `all --aggregate`.

Leia JSON, Markdown e logs em `quality-reports/`. Confirme SHA, hash da política, workspace,
conclusão de todos os checks e aprovação por área. Falta de evidência, infraestrutura
indisponível, cancelamento, timeout ou mutação incompleta não aprovam. Skips externos e
requisitos `xfail` estritos são pendências explícitas, não funcionalidades entregues.
Não trate mutação Python como teste de regras SQL.

Apresente falhas com arquivo, evidência, impacto e ação sugerida. Corrigir o produto
requer autorização de escopo; executar a infraestrutura não autoriza refatorações.

## Revisar Clean Code

Revise o diff solicitado à luz dos contratos e das evidências. Procure duplicação,
nomes ambíguos, responsabilidades misturadas e abstrações sem necessidade concreta.
Cada achado deve indicar arquivo/linha, exemplo verificável, consequência e correção
mínima sugerida. Não produza nota agregada nem confunda essa avaliação com aprovação
automatizada. Preserve APIs, payloads, digests e transações; não refatore código adjacente.

## Analisar tendências

Consulte `history`, `metrics`, duração e resultados dos testes nos relatórios. Para obter
artefatos disponíveis da main, configure `GITHUB_REPOSITORY` e execute
`uv run python -m quality.history_fetch` com o `gh` autenticado. Compare somente políticas
compatíveis, usando a última execução aprovada e até dez execuções disponíveis.

Aponte mudanças por área e suas evidências: cobertura de linhas/branches, sobreviventes,
mutantes sem testes, inválidos/timeouts, tamanhos, complexidade, duração, skips e xfails.
Histórico ausente gera aviso; nunca dispensa limites absolutos. Não altere automaticamente
política ou código ao analisar tendências.
