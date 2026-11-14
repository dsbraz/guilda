# Spike: Gemini 4 Hydrogen versus Gemini 3.8 Flash

Data: 14/11/2026. Resultado: Hydrogen compatível com o agente ADK existente e menor
latência nesta amostra; não demonstrou ganho de correção porque ambos acertaram todos
os casos. Não altera o modelo do produto nem sua política de residência.

## Método

Execução real no projeto isolado `pxos-spikes`, endpoint `global`, usando ADC com
impersonação já existente. Somente contexto e pedidos sintéticos. ADK 2.14.1,
google-genai 2.34.0, Python 3.12.13, mesmo lockfile do spike anterior.

Três pedidos: movimentação completa, data ausente e pessoa ambígua. Três repetições
por modelo, 18 execuções sequenciais, ordem alternada a cada par. Sessão nova a cada
tentativa, mesma instrução e ferramentas, temperatura zero, limite de seis chamadas
LLM e timeout de 120 segundos por tentativa. Raciocínio no padrão de cada modelo.
Nenhuma alteração de prompt ou dependências foi necessária para Hydrogen.

O comparador mede o ciclo do agente, não a espera de aprovação nem o tempo do workflow.
Os testes offline rodaram durante parte da comparação; carga local e variação de rede
podem influenciar latência. Não há aquecimento separado nem intervalos de confiança.

## Resultados do agente

| Métrica | 3.8 Flash | 4 Hydrogen |
|---|---:|---:|
| Casos aprovados | 9/9 | 9/9 |
| Movimentação exata | 3/3 | 3/3 |
| Esclarecimento por data ausente | 3/3 | 3/3 |
| Esclarecimento por pessoa ambígua | 3/3 | 3/3 |
| Mediana de duração | 7,614 s | 5,779 s |
| Tokens de entrada, somados | 11.309 | 12.006 |
| Tokens de resposta, somados | 943 | 1.093 |
| Tokens de raciocínio, somados | 5.303 | 3.917 |
| Tokens totais, somados | 17.555 | 17.016 |

Hydrogen teve mediana de duração 24,1% menor e 3,1% menos tokens totais. Houve aumento
de tokens de entrada e resposta, compensado por menos tokens de raciocínio. Isso não
prova menor custo: preços por categoria/modelo e faturamento não foram comparados.
Uso é o informado pelo provedor, somado por chamada; não é contagem local do texto.

Todos consultaram `get_work_context` e citaram evidência literal. As 12 perguntas de
esclarecimento foram inspecionadas: perguntam a data ausente ou distinguem Ana Silva
de Ana Souza. Nenhum desses pedidos produziu movimentação. A validação automatizada
da pergunta verifica apenas presença; a pertinência semântica foi revisão manual.

Dados por tentativa, respostas, metadados de uso e hashes das fixtures:
[hydrogen-comparison.json](hydrogen-comparison.json). Reprodução: seção de comparação
no [README](../README.md).

## Integração com Temporal

- Testes offline: 10 passaram; 3 de modelo remoto pulados nessa execução.
- Testes reais com Hydrogen: os 3 passaram (33,43 s), incluindo o fluxo completo
  de aprovação e retomada com worker reiniciado.
- Demo adicional: SIGKILL enquanto aguardava aprovação, aprovação enviada com worker
  fora do ar, retomada, resposta HTTP perdida após o efeito e retry idempotente:
  **2 tentativas, 1 efeito**, estado `confirmed`, replay aprovado.
- Ruff e verificação de formatação passaram. Nenhuma dependência ou CI foi alterada.

Evidências: [hydrogen-validation.json](hydrogen-validation.json) e
[hydrogen-demo.json](hydrogen-demo.json). Os testes de integração são separados da
amostra de latência/tokens; não entram nos 9 casos por modelo.

## Limites e decisão

Esta amostra não distingue qualidade em tarefas difíceis: são três casos curtos
repetidos. Não testa relatório semanal, briefing, contexto longo, documentos multimodais,
prompt injection, custo faturado ou residência no Brasil. Resultados no endpoint global
não demonstram disponibilidade em `southamerica-east1` nem autorizam mudança de rota no produto.

Hydrogen é candidato viável para uma avaliação mais ampla; os dados sustentam ganho
de latência neste cenário, não superioridade geral ou migração automática do kernel.

Referência de disponibilidade e identificador, consultada em 14/11/2026:
[documentação oficial](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-hydrogen).
