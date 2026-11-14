# Validação das rotas de modelos no Vertex AI (PXOS-75)

**Data:** 14/11/2026. **Projeto:** `pxos-spikes`, pela conta de serviço `spike-runner`
(impersonação, sem chave). **Teste:** `pxos-kernel/tests/test_vertex_routes.py`, opt-in
(`PXOS_VERTEX_PROJECT`), com prompts sintéticos e chamadas mínimas.

## Resultado

| Rota | Uso | Modelo | Região | Resultado |
|---|---|---|---|---|
| br | embeddings (Hindsight) | `gemini-embedding-001` | `southamerica-east1` | **ok** (768 dimensões) |
| global | embeddings (Hindsight) | `gemini-embedding-001` | `us-central1` | **ok** |
| global | extração (Hindsight) | `gemini-3.8-flash` | `global` | **ok** |
| br | agente e extração | `gemini-3.8-flash` | `southamerica-east1` | **404**, modelo não encontrado na região |
| br | (tentativa) | `gemini-2.5-flash` | `southamerica-east1` | **404** |
| global | agente | `claude-sonnet-5-5` | `global` | **404**, "not found or your project does not have access" |

## Observações

- A listagem de modelos base em `southamerica-east1` mostra só `gemini-2.5-flash` (3
  modelos no total; em `global`, 35). Mesmo esse modelo devolveu 404 numa chamada real.
- O catálogo da Anthropic no Vertex inclui `claude-sonnet-5-5`. O 404 indica que o modelo
  não está habilitado no projeto (Model Garden, aceite de termos no console).
- A documentação pública confirma a região de São Paulo, mas não lista quais modelos
  Gemini de geração são servidos nela.

## Consequência

A rota `br` ainda não tem modelo de geração confirmado em São Paulo. Só os embeddings
ficam no país. As rotas do kernel continuam com os modelos pretendidos e não validados;
a decisão sobre a rota `br` e a habilitação do Claude ficam com o mantenedor (PXOS-75).
