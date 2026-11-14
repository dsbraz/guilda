# Spike ADK + Temporal

Experimento da plataforma de agentes e workflows do pxOS. O cenário é uma decisão de
movimentação de designer entre projetos: contexto → proposta → aprovação → portal →
confirmação. Toda informação é sintética. Não altera kernel, distribuição, CI ou nuvem.

**ADK executa o agente; Temporal controla o processo.** O agente só recebe ferramentas de
leitura. O conector de escrita é uma Activity separada, chamada depois da aprovação.
Sessões ADK são efêmeras; a proposta aceita fica no histórico do Temporal e não é
recalculada quando o worker volta. O diário SQLite representa o retorno ao contexto,
sem integração com Hindsight ou o estado canônico de produto.

## Hipóteses e prova

| Hipótese | Verificação |
|---|---|
| Separação entre agente e processo | ADK Runner e ferramentas reais em Activity; workflow determinístico |
| Sobreviver à queda do worker | SIGKILL durante espera; aprovação enviada offline; novo processo retoma |
| Aprovação do conteúdo exato | Digest inclui operação, ação, payload, evidência e aprovador; alterações recusadas |
| Falha após efeito no portal | HTTP 504 depois do commit: retry com chave idempotente produz um efeito |
| Portal sem idempotência | Uma tentativa de escrita; reconciliação pelo comprovante ou parada explícita |
| Lacunas no contexto | Data ausente e pessoa ambígua exigem esclarecimento antes da proposta |
| Extensão pelo FDE | Registro de `get_capacity` e configuração do agente, sem mudança no workflow |
| Contexto fiel ao resultado | Proposta, aprovação e confirmação são eventos separados; incerteza nunca é sucesso |
| Replay | Replayer valida o histórico sem executar modelo ou portal novamente |

## Execução local, sem modelo remoto

Requisitos: Linux, Python 3.12, uv e acesso à rede na primeira instalação. Não exige
Docker nem conta Temporal Cloud. O SDK baixa o Temporal CLI **1.10.1** em `.runtime/`;
o lockfile fixa ADK **2.14.1** e SDK Temporal **1.36.1**. Serviços usam apenas loopback.

Nesta pasta:

```sh
uv sync --locked
uv run --frozen pytest -q --junitxml=.runtime/validation.xml
uv run --frozen ruff check spike tests
uv run --frozen ruff format --check spike tests
uv run --frozen python -m spike.demo
uv run --frozen python -m spike.demo --no-idempotency
uv run --frozen python -m spike.demo --no-idempotency --hide-receipt
```

O modo padrão usa ADK real e `FixtureModel`: respostas roteirizadas de um adaptador
`BaseLlm`, sem inferência. Ele prova integração e falhas, **não qualidade de interpretação**.
Os três testes de modelo remoto são pulados explicitamente sem `SPIKE_LIVE_MODEL`.

A demonstração imprime a proposta, simula a aprovação do delivery manager, mata e
reinicia o worker e imprime o resultado. Aprovação é automatizada pela fixture; não há
uma pessoa ou UI aprovando durante a demonstração. Cada execução guarda banco, logs,
histórico Temporal e `result.json` numa pasta própria `.runtime/demo-<uuid>/`.
Os processos são encerrados ao terminar; os arquivos ficam para inspeção.

## Modelo real: Vertex no projeto isolado de spikes

Envie apenas as fixtures sintéticas; esta execução consome tokens. O projeto existente
é `pxos-spikes`, com impersonação de `spike-runner@pxos-spikes.iam.gserviceaccount.com`.
É necessário acesso à conta e Vertex. O helper abaixo lê o ADC de usuário e grava uma
configuração de impersonação em `.secrets/adc.json` com permissão 0600, sem chave de
conta de serviço. Não altera o login padrão do gcloud.

```sh
CLOUDSDK_CONFIG=/tmp/pxos-spikes-auth gcloud auth application-default login
uv run --frozen python -m spike.auth \
  --source /tmp/pxos-spikes-auth/application_default_credentials.json
export GOOGLE_APPLICATION_CREDENTIALS="$PWD/.secrets/adc.json"
export GOOGLE_GENAI_USE_ENTERPRISE=true
export GOOGLE_CLOUD_PROJECT=pxos-spikes
export GOOGLE_CLOUD_LOCATION=global
export SPIKE_LIVE_MODEL=gemini-3.8-flash
uv run --frozen pytest -q tests/test_live.py --junitxml=.runtime/live-validation.xml
uv run --frozen python -m spike.demo --model "$SPIKE_LIVE_MODEL"
```

O ADK/GenAI atual usa `GOOGLE_GENAI_USE_ENTERPRISE`; o alias
`GOOGLE_GENAI_USE_VERTEXAI` ainda funciona, mas gera aviso de depreciação. Outros
ambientes podem fornecer seu próprio ADC, projeto e modelo, sem usar `spike.auth`.
Os testes reais verificam esclarecimento para duas entradas e uma movimentação completa
com payload esperado e reinício do worker. São amostras de viabilidade, não um benchmark.

## Comparação Hydrogen × 3.8 Flash

Com as mesmas variáveis de autenticação da seção anterior:

```sh
uv run --frozen python -m spike.compare_models --repetitions 3
SPIKE_LIVE_MODEL=gemini-4-hydrogen uv run --frozen pytest -q tests/test_live.py
uv run --frozen python -m spike.demo --model gemini-4-hydrogen
```

O comparador executa os três pedidos originais, três vezes por modelo (18 execuções),
alternando a ordem. Cada execução abre uma sessão nova, com o mesmo contexto, instrução,
ferramenta e temperatura zero. O nível de raciocínio permanece no padrão de cada modelo;
comparamos a substituição direta no agente, não budgets de raciocínio equivalentes.
A duração inclui o ciclo ADK, chamadas de modelo e ferramentas; não inclui Temporal.
O JSON em `reports/hydrogen-comparison.json` guarda cada resposta, resultado da validação,
duração e uso informado pelo provedor em cada chamada, além das versões e hashes das fixtures.
Timeouts e erros contam como falhas, não são descartados nem substituídos por respostas offline.
Tokens de raciocínio são registrados separadamente; não representam preço por si mesmos.

O critério para o pedido completo é igualdade exata com a movimentação esperada. Nos
outros dois, exige pedido de esclarecimento, pergunta não vazia e nenhuma movimentação.
O próprio Proposer exige consulta ao contexto, evidência literal e compatibilidade dos
campos com o contexto. A pertinência das perguntas deve ser revisada nas respostas salvas.
São três casos pequenos repetidos, sem significância estatística, avaliação de relatórios,
briefings, contexto longo ou residência de dados no Brasil. Ver [resultado](reports/hydrogen.md).

## O que o FDE constrói

- `spike/domain.py`: contrato Pydantic de movimentação, exemplo mínimo de schema de domínio.
- `fixtures/context.json`: contexto sintético; numa integração, viria do pxOS e do portal.
- `spike/fde_tools.py`: ferramentas somente de leitura disponíveis ao agente.
- `fixtures/agent.json`: instruções e seleção de ferramentas.
- `spike/activities.py`: conector HTTP, execução, reconciliação e registro do resultado.

Para adicionar uma ferramenta, o FDE implementa a função, registra seu nome e a habilita
na configuração. Isso exige código confiável; não há carregador genérico de plugins.
O spike não gera ontologia LinkML nem ferramentas MCP. API/MCP e editor visual ficam
para uma implementação de produto, após a decisão sobre os motores.

## Limites que importam

- Aprovação usa identidade declarada por um cliente de teste confiável. Não demonstra
  OIDC, RBAC, isolamento de clientes, resistência a falsificação de identidade ou sandbox.
- Digest comprova igualdade, não autenticação. A fronteira de produto precisa autenticar
  o aprovador e preservar a autoridade do gateway.
- Portal é HTTP real com SQLite, mas seus comportamentos são controlados. O portal BRQ
  real pode não oferecer chave idempotente nem consulta de comprovante; nesse caso,
  execução incerta exige reconciliação humana. Temporal sozinho não garante exactly-once.
- Não há atualização concorrente de capacidade, expiração de aprovações ou revalidação
  de versão do portal antes da escrita. `expected_version` demonstra o contrato, mas o
  simulador não implementa controle otimista de alocações. Isso é gate de produto.
- Proposta em linguagem natural continua sujeita a erro semântico. Schema e evidência
  literal não provam que todos os parâmetros foram inferidos corretamente; há aprovação.
- Reinício de worker e replay estão cobertos; não foi provada alta disponibilidade ou
  recuperação do cluster Temporal após perda de banco. A demo usa banco local persistente.
- Não prova implantação, custo, escala, migração de versões de workflow ou segurança de
  ferramentas externas. O protocolo de aprovação do spike não substitui o do kernel.
- Não compara experimentalmente ADK sozinho ou LangGraph. A combinação passar não prova
  que seja a alternativa de menor custo operacional.

## Fontes

- [ADK: agentes e ferramentas](https://google.github.io/adk-docs/agents/llm-agents/)
- [Temporal: teste e replay em Python](https://docs.temporal.io/develop/python/testing-suite)
- [Temporal: execução durável](https://docs.temporal.io/ai)
- [Resultado e decisão do experimento](reports/ADR-001.md)
