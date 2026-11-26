# Validação das rotas de modelos no Vertex AI (PXOS-75)

**Data:** 14/11/2026. **Projeto:** `pxos-spikes`, pela conta de serviço `spike-runner`
(impersonação, sem chave). **Teste:** `guilda-kernel/tests/test_vertex_routes.py`, opt-in
(`GUILDA_VERTEX_PROJECT`), com prompts sintéticos e chamadas mínimas.

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

## Etapa ADK do escriba (fatia 7)

Em 14/11/2026, no mesmo projeto, o `FactReader` (PXOS-79) leu um fato sintético com o
`gemini-3.8-flash` no endpoint `global`, porque a rota `br` não tem modelo de geração em
São Paulo. O Vertex aceitou o schema derivado da ação (payload opcional mais o trecho), a
resposta foi lida com o payload e o trecho certos, e um fato que não é promessa voltou
vazio (`test_vertex_routes.py::test_the_adk_step_reads_a_fact_on_vertex`). Isso valida o
schema e a leitura, não a residência; a etapa na rota `br` continua esperando o PXOS-75.

## Rota configurada no deploy

Ainda em 14/11/2026, as rotas fixas saíram do código: o teste opt-in valida a rota que o
ambiente configura, a mesma que um deploy gravaria. Com `GUILDA_MODEL_PROVIDER=gemini`,
`gemini-3.8-flash` em `global` para o agente e a memória, e `gemini-embedding-001` em
`southamerica-east1` para os embeddings, os quatro testes passaram: o agente pelo gateway, o
modelo da memória, os embeddings e a etapa ADK. É uma rota de teste, não a escolha de um
cliente; a geração saiu do país. Qual região atende a residência de cada cliente fica com o
deploy (K44). O PXOS-75 foi fechado como substituído pela configuração de deploy (PXOS-81).
