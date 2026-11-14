# Roadmap: pxOS kernel → BRQ@work

## Contexto

O PRD `pxos-brq-work-prd.html` (v0.26) descreve o **kernel do pxOS** (Parte A) e sua primeira distribuição, a **BRQ@work** (Parte B), em construção por fatias verticais. Os spikes de memória, ontologia e governança, e execução e isolamento foram concluídos em 13/11/2026. O spike ADK + Temporal foi concluído em 14/11/2026; o mantenedor aprovou a adoção dos dois motores e a criação e operação de agentes e workflows dentro da plataforma.

Decisões já tomadas:
- **Entregável:** roadmap em fases, com marcos, dependências, gates e critérios de saída.
- **Primeiro marco:** **kernel completo**, ou seja, os 13 contêineres do §5 e os 13 requisitos do §4. A construção começa por uma **fatia vertical** (ver Fase 0–2) que já roda o pacote de contexto de trabalho da BRQ@work.
- **Memória:** contexto no Hindsight sobre AlloyDB; estado canônico tipado e governado próprio em PostgreSQL ([ADR 0001](docs/adr/0001-memoria-contexto-e-estado-canonico.md)). Graphiti, Neo4j, Mem0 e LangExtract rejeitados.
- **Fronteira kernel/distribuição** por natureza, com o mantenedor como árbitro ([ADR 0002](docs/adr/0002-fronteira-kernel-distribuicao.md)).
- **Arquitetura e padrões de código** em [ADR 0003](docs/adr/0003-arquitetura-e-padroes-de-codigo.md).
- **Agentes e workflows:** Google ADK para agentes; Temporal como único motor de processos duráveis. ADK roda em Activities quando participa de um workflow. Aprovação, identidade e execução autorizada permanecem no kernel; o histórico dos motores não substitui a memória corporativa. [Evidência do spike](spikes/adk-temporal/reports/ADR-001.md): 13 testes passaram, incluindo Gemini real, retomada e execução incerta. Integração e operação de produção ainda precisam ser entregues ([ADR 0004](docs/adr/0004-agentes-workflows-e-plataforma-de-construcao.md)).
- **Experiência de construção:** a plataforma é o lugar único para configurar a instalação, construir agentes, compor workflows, publicar e acompanhar ([ADR 0004](docs/adr/0004-agentes-workflows-e-plataforma-de-construcao.md)). Configuração fica na plataforma (conectores já existentes, papéis, aprovadores, autonomia, agentes, workflows); código de conectores e ferramentas e a ontologia continuam em código, escritos pelo FDE. Toda mudança feita na plataforma vira versão publicada, imutável, com digest e aprovada por um papel; o Git deixa de ser a fonte de verdade das definições de agentes e workflows. Editar a ontologia pela interface fica fora do escopo atual. APIs e MCP permitem integrar outras plataformas sob as mesmas permissões e política. A biblioteca de interface ainda não foi escolhida.
- **Experiência unificada:** uma única área web do pxOS, com login, navegação e identidade visual próprios, reúne memória corporativa, agentes, workflows, execuções e aprovações. O backend aplica permissões e integra os motores. Canais como Teams continuam disponíveis; web e canais compartilham fatos, decisões e fila de revisão.
- **Implantação:** instalação dedicada por cliente é o padrão, e dentro dela valem as permissões internas. O modo compartilhado fica adiado.
- **Time:** você e eu. O gargalo é o seu tempo de revisão e os acessos externos (TI, jurídico, GCP), não a escrita de código. Por isso o plano é sequenciado por critério de saída, sem datas.

**Risco aceito:** o próprio PRD aponta o risco "kernel com a forma da BRQ@work". Construir o kernel primeiro inverte esse risco: o kernel fica sem validação de uso diário. A mitigação é um **conjunto de fixtures de referência** que exercita o kernel como duas distribuições diferentes fariam:
- 2 instalações, com permissão restrita entre pessoas dentro de uma;
- 1 conector falso de conteúdo e 1 de registros operacionais;
- 1 canal falso;
- 1 pacote de ontologia de outro domínio (neutralidade), além do pacote real de contexto de trabalho da BRQ@work.

Assim cada fase tem um critério executável.

## Princípios de execução

- **Local primeiro** (CLAUDE.md): Docker Compose com portas só em loopback, Postgres 16 + pgvector local e emuladores (Pub/Sub, Firestore/Datastore se preciso). Cada serviço GCP (Vertex, Agent Gateway, eventual Agent Engine, BigQuery, Secret Manager) fica atrás de um **adaptador** com implementação local e GCP. ADK e Temporal rodam localmente; a implantação de produção será validada sem trocar o motor de workflows. Isso também atende o requisito 12 (portabilidade).
- **Monorepo:** workspace uv na raiz com `pxos-kernel/` (kernel) e `brq-at-work/` (distribuição), `docs/adr/` e `spikes/`. Outras pastas (`infra/`, `evals/`, Constituição) nascem com a issue que as usa.
- **Stack:** Python 3.12, LinkML, Pydantic, asyncpg com SQL explícito, Alembic, PostgreSQL/AlloyDB, Hindsight, Google ADK, Temporal, pytest e Terraform (ADR 0003). O MCP básico já foi entregue na fatia 3. A Fase 8 implementa a integração dos motores escolhidos; as versões do spike são evidência, não uma política de versões de produção.
- **Requisitos viram testes:** cada um dos 13 requisitos do §4 vira um teste automatizado de aceitação, que roda no CI desde que existe.
- **Medição desde o começo:** traces (OpenTelemetry) e conjuntos de avaliação começam na primeira chamada de modelo, não no fim.
- **Git:** integração direta na `main` enquanto o time for o mantenedor e o agente (AGENTS.md).

## Fases

### Fase 0: Fundação e destravamentos
- Estrutura do monorepo, Compose de desenvolvimento e CI de produto (lint, tipos, testes, contrato de importação e neutralidade).
- Fixtures de referência (descritas acima) e o esqueleto dos 13 testes de requisito, marcados como `xfail`.
- **Serviços gerenciados** (Agent Gateway com Service Extensions, eventual Agent Engine, Claude no Vertex, Gemini em southamerica-east1): a validação acontece nas issues que os usam (K11, K31, K34). O spike ADK + Temporal usou Gemini no Vertex global, sem validar esses serviços ou residência no Brasil. Hospedagem do Temporal, disponibilidade, custo e atualização dos workers ficam como gates de produção na Fase 10.
- **Memória:** decidida no ADR 0001 (concluído).
- **Abrir já as frentes externas** (prazo longo, só bloqueiam a Parte B): permissões no Entra ID e contas de serviço, API do portal de alocação e horas, modelo de acesso ao Granola, política de transcrições com cliente e residência de dados (jurídico).
- **Saída:** `docker compose up` sobe a base, o CI roda, os ADRs 0001–0003 estão aceitos e as frentes externas têm dono e status.

**Fatia 1 (atravessa F0–F2):** PXOS-1 (K01), as sub-issues PXOS-47 a 51 (partes de K04, K05, K06, K08 e K09), PXOS-7 (K07) e, na BRQ@work, o pacote de contexto de trabalho (PXOS-46). **Saída:** um compromisso é proposto com evidência, aprovado pelo papel declarado, lido em duas datas e corrigido sem perder histórico; o pacote fixture de outro domínio faz o mesmo ciclo; nenhum tipo de domínio aparece em `pxos-kernel/`.

### Fase 1: Ontologia e geração de código
- Ontologia base em LinkML (Organização, Contrato, Ator, Papel, Evento, Evidência, Métrica, Resultado e suas ações), mais o pacote de contexto de trabalho (na distribuição BRQ@work) e um pacote fixture de outro domínio.
- Camadas: base, pacote e extensão por instalação, com versionamento por camada.
- Geradores no CI produzem: schemas de extração (JSON Schema/Pydantic), tools MCP, views tipadas do banco, modelos para agentes e definições de cards.
- Formato de manifesto de módulo e de distribuição, com validador.
- **Saída:** mudar um YAML regenera os 5 artefatos de forma determinística, com testes de snapshot. Uma mudança incompatível sem migração faz o CI falhar.

### Fase 2: Estado canônico, instalação e identidade
- Estado canônico governado: proposta imutável → aprovação presa ao digest → fato versionado com **duas datas** → correção e invalidação, sobre o contrato do spike de governança. Outbox na mesma transação.
- Instalação dedicada com a sua política; permissões internas por RLS. O contexto (Hindsight) fica isolado pela instalação e pelo escopo do servidor.
- Exclusão real (estado canônico, contexto no Hindsight, fatos derivados e páginas), com registro de que houve a exclusão. Política de retenção por instalação.
- Identidade de pessoas, agentes e atores externos: modelo de papéis e delegações. Externos ficam sem acesso às tools de leitura.
- **Saída:** passam os testes dos requisitos 3 (fonte), 4 (credencial de outra instalação recusada e leitura restrita negada pelo banco), 5 (identidade) e 11 (a parte de exclusão e retenção).

### Fase 3: Acesso a modelos e medição base
- Ponto único de chamada: pseudonimização (Sensitive Data Protection, ou um adaptador local com Presidio), roteamento por residência da instalação e inspeção (Model Armor, ou um stub local).
- Traces com custo, modelo, região e instalação (OpenTelemetry → Cloud Trace e BigQuery, ou Jaeger e Postgres em local).
- Harness de avaliação: conjuntos de referência rodam a cada mudança de prompt, modelo ou ontologia.
- **Saída:** uma instalação que exige residência no Brasil nunca chega ao endpoint global (teste). Dados pessoais saem pseudonimizados. Toda chamada gera trace.

### Fase 4: Ingestão e framework de conectores
- Contrato de conector: leitura, mapeamento, ações, acesso e instalação; para escrita, declarar suporte a idempotência, controle de versão e consulta de comprovante. O FDE implementa os detalhes do sistema de destino.
- Três modos de leitura: aviso de mudança com leitura incremental, captura de mudanças (Datastream ou um adaptador local com logical replication/Debezium) e leitura periódica com diff.
- Fila de eventos, idempotência, reconciliação periódica, carga inicial e descarte do que não tem vínculo.
- Registros operacionais vão direto para o estado vivo e para a base analítica, sem modelo.
- **Saída:** os dois conectores falsos rodam. Reenviar e embaralhar eventos não gera duplicatas (requisitos 2 e 8).

### Fase 5: Canais e fila de revisão
- Abstração de canal: entregar mensagem e card, receber pedido e aprovação. Inclui o canal falso de referência.
- Fila de revisão com cards gerados da ontologia, roteamento para o dono, prazo, substituto ou gestor e expiração.
- **Saída:** um card criado pelo kernel chega ao canal falso, é aprovado ou expira, e o resultado volta como evento.

### Fase 6: Proposta canônica e identidade
- A extração de contexto é do Hindsight (ADR 0001); não se reconstrói extrator, resolvedor ou reconciliador. Esta fase faz: consumidor com triagem e retenção no contexto, propostas canônicas com evidência literal validada em código, identidade canônica (chave determinística, aliases, restrição de candidatos, fila humana) e roteador por confiança sobre o estado canônico.
- Conjunto de avaliação com transcrições sintéticas anotadas.
- **Saída:** a avaliação mede precisão de extração e precisão de junção, e a junção fica em 99% ou mais nas fixtures. Fato sem evidência literal é descartado. Risco e mudança de escopo sempre viram card.

### Fase 7: MCP, API e gateway de ação
- Autenticação com OIDC genérico (Entra ID fica na Parte B; em local, um IdP falso como Keycloak).
- Catálogo de tools gerado: leitura (search, get_context, timeline, list_commitments, get_page) e uma tool por ação.
- Serviço de política: ação × papel × parâmetros × fase de autonomia × regras do módulo.
- Encaminhador para a fila, executor como único caminho de escrita, caminho síncrono para ações pré-aprovadas, log de auditoria e credenciais (Secret Manager ou um adaptador local).
- Conectores externos e servidores MCP registrados com escopo e política; agentes de outras plataformas acessam contexto e solicitam ações pelas mesmas APIs/MCP. Credenciais de escrita permanecem no caminho autorizado do gateway.
- Execução confere conteúdo aprovado, autorização vigente e versão do recurso. Repetição só conforme o contrato do destino; resultado incerto exige consulta de comprovante ou reconciliação, sem registrar sucesso prematuro.
- **Saída:** passam os testes dos requisitos 1 (o mesmo MCP serve pessoa e agente), 6 (ação irreversível sempre aprovada) e 7 (latência p95 do caminho síncrono dentro da meta definida no ADR).

### Fase 8: Construção e operação de agentes e workflows
Uma única área web do pxOS, construída nesta ordem, cada etapa útil sozinha:

1. **Aprovações e execuções:** navegação e autenticação comuns; a fila de aprovações é a mesma dos canais, com decisão única e estado consistente nas duas superfícies; a memória permite consultar contexto, fatos, fontes e histórico; o painel lista execuções por instalação, função, período e estado e mostra etapa, versão, evidência, ferramentas, aprovação, resultado, custo e erro, correlacionando IDs Temporal/ADK com proposta e ação do gateway. Intervenções só as autorizadas pelo processo (esclarecer, aprovar, cancelar, reconciliar); nada de retry cego para efeito incerto.
2. **Agentes por formulário:** função, instruções, contexto, modelo, ferramentas do catálogo, saída esperada, responsável e autonomia; teste com exemplos e resultados esperados; publicação como versão imutável com digest, aprovada por um papel. Identidade própria e leitura com a delegação da pessoa.
3. **Workflows:** gatilho, etapas de agente, ações, condições, aprovações, prazos e falhas, por formulário ou modelo pronto sobre blocos registrados pelo FDE; composição visual só se a necessidade aparecer. Mesma regra de publicação; a execução referencia versões publicadas e rascunhos não alteram processos em andamento.
4. **Configuração da instalação:** conectores já existentes (credencial, escopo, mapeamento para a entidade âncora, estado da ingestão), papéis, aprovadores e fases de autonomia, também publicados como versão aprovada.

Edição da ontologia pela interface fica fora desta fase e do marco do kernel; a ontologia continua em pacotes LinkML por pull request.

- **Motores:** ADK executa interpretação e ferramentas; Temporal controla processos duráveis, espera, prazos e retomada. Chamadas ADK ficam em Activities. Não duplicar o ciclo de aprovação nos dois motores nem construir motor próprio. Sessões e histórico de execução não substituem Hindsight e estado canônico.
- **Regras de integração (ADR 0004):** chamadas de modelo do ADK passam pelo acesso a modelos (Fase 3); workflows carregam só referências, nunca conteúdo ou dados pessoais, para a exclusão continuar efetiva; namespace Temporal e credenciais de worker por instalação; o worker abre a sessão no banco como o agente, agindo pela pessoa; a decisão de revisão chega ao workflow como sinal a partir da outbox, sem segunda fila.
- **Gatilhos:** Temporal Schedules para agenda e entrada de eventos do estado vivo. Integração com fila de revisão e política do kernel, incluindo expiração e revogação. ADK Web e Temporal Web UI apoiam desenvolvimento e diagnóstico; o painel de produto aplica as permissões do pxOS.
- **Issues:** K34 (runtime e versão publicada do agente), K35 (gatilhos), K36 (Temporal), K65 (área web: aprovações, execuções, memória), K66 (agentes por formulário), K67 (workflows compostos), K68 (configuração da instalação).
- **Saída:** o FDE registra uma ferramenta e configura um agente/workflow sem alterar o mecanismo comum; publica uma versão, aciona por agenda e evento e acompanha a execução no painel. O agente consulta contexto via MCP e age via gateway em fase de autonomia 1. Testes de produto demonstram espera por esclarecimento/aprovação, retomada após queda, aprovação exata, resultado incerto sem duplicação e retorno de confirmação ao estado vivo. Uma função de referência produz uma entrega baseada no contexto e identifica lacunas sem exigir que a pessoa reconstrua todo o histórico. A progressão de autonomia só acontece por métrica.

### Fase 9: Manutenção, base analítica e medição de resultado
- Job diário: deduplicação, conflitos, envelhecimento, páginas narrativas regeneradas com citação dos fatos, propostas de PR à Constituição.
- Base analítica (BigQuery, ou DuckDB/Postgres em local): histórico em volume, um modelo analítico de referência chamado como tool.
- Medição de resultado: linha de base, indicadores por contrato e trilha auditável.
- **Saída:** passam os testes dos requisitos 9 e 10. A página narrativa é regenerada quando um fato muda.

### Fase 10: Apps e implantação → **Marco: kernel completo**
- Hospedagem de apps: banco próprio por app, espelhamento de fatos no estado vivo e acesso só por MCP, API e canais. Kit de construção de apps com um app de referência.
- Ciclo de vida de módulos e distribuições por instalação (instalar, atualizar, remover, regenerar), testes de distribuição contra uma versão nova do kernel e permissões por módulo.
- Terraform para a instalação dedicada (projeto GCP por cliente). O modo compartilhado fica adiado.
- Implantação dos workers ADK e serviço Temporal (próprio ou gerenciado): validar residência, isolamento, disponibilidade, recuperação, custo, retenção do histórico e atualização compatível com workflows em andamento. A escolha dos motores não define antecipadamente o provedor de hospedagem.
- **Saída:**
  - os 13 testes de requisito passam;
  - a "distribuição de referência" instala do zero em local e em um projeto GCP de homologação;
  - o requisito 13 é verificado: nenhum código de domínio no kernel.

### Fase 11+: BRQ@work (Parte B, sequência grossa)
Para começar, precisa dos gates de TI e jurídico da Fase 0 resolvidos.
1. Pacote de projetos e operação, conector do Microsoft 365 (Graph: SharePoint/OneDrive delta, canais, calendário, renovação de assinaturas) e cadastro de projeto.
2. Conector do Granola (conforme o modelo de acesso decidido) e mapeamento de reunião para projeto por calendário e série.
3. Canal Teams (bot e cards) e Claude Enterprise via MCP, com Entra ID.
4. Escriba pós-reunião, em fase 1. **Piloto com 1 ou 2 projetos.**
5. Conector do Bud e conector do portal pelo FDE (se houver API; sem API, o agente só sugere). Validar idempotência, controle de versão e comprovantes antes de autorizar movimentações de alocação.
6. Configurar e publicar agentes/workflows de briefing do dia e pré-reunião, status semanal e programação de alocação; acompanhar suas execuções na plataforma. Validar o status semanal com os acontecimentos do período e esclarecimento de lacunas; validar movimentação com proposta, aprovação, escrita no portal e confirmação no contexto.
7. App de visão de carteira. Expansão para cerca de 250 pessoas de PX, acompanhando as métricas do §14.

## Rastreabilidade

**Contêineres (§5) → fase**

| Contêiner | Fase |
|---|---|
| Repositório Git (ontologia, CI) | 1 |
| Estado vivo | 2 |
| Acesso a modelos | 3 |
| Ingestão e conectores | 4 |
| Canais | 5 |
| Extração e identidade | 6 |
| MCP e API | 7 |
| Gateway de ação | 7 |
| Agentes | 8 |
| Gatilhos e fluxos | 8 |
| Manutenção | 9 |
| Base analítica | 9 |
| Medição e resultado | 3 (base) → 9 (resultado) |
| Apps | 10 |

**Requisitos (§4) → fase do teste verde**

| Requisitos | Fase |
|---|---|
| 3, 4, 5 | 2 |
| 11 | 2 (exclusão) + 3 (pseudonimização e residência) |
| 2, 8 | 4 |
| 1, 6, 7 | 7 |
| 9, 10 | 9 |
| 12, 13 | 10 (auditoria de adaptadores e de fronteira) |

**Questões em aberto (§16) e pontos jurídicos (§13) → gate**

| Questão | Começa | Bloqueia |
|---|---|---|
| Construir ou adotar a memória | resolvida (ADR 0001) | — |
| Residência de inferência | F0 (spike + jurídico) | F3 (desenho), F11 (produção) |
| Compartilhada vs. dedicada | dedicada como padrão; compartilhada adiada | F10 (só se surgir demanda) |
| Governança da fronteira do kernel | resolvida (ADR 0002) | — |
| Motores de agentes e workflows | ADK + Temporal adotados em 14/11/2026 | F8 (integração, construção e operação) |
| Hospedagem Temporal e workers ADK | F8 (local e desenho) | F10 (produção: disponibilidade, residência, custo e upgrades) |
| Interface de criação e acompanhamento | escopo decidido (ADR 0004); tecnologia na F8 | saída da F8 |
| Edição da ontologia pela interface | fora do escopo atual | depois do marco do kernel, se houver demanda |
| Permissões do Entra ID e contas de serviço | F0 (TI) | F11.3 |
| API do portal e contrato de execução/reconciliação | F0 (TI), detalhamento F4/F7 | F11.5 |
| Modelo de acesso ao Granola | F0 | F11.2 |
| Política de transcrições com cliente (LGPD) | F0 (jurídico) | F11.2 / piloto |
| Camada conversacional de atendimento | adiada | fora deste plano (outra distribuição) |
| Modelo de contrato das distribuições de cliente | adiada | fora deste plano (só define a linha de base da F9) |

## Verificação

- O CI roda lint, tipos, testes unitários e a **suíte dos 13 requisitos** contra as fixtures, a cada commit.
- Avaliação contínua: os conjuntos de referência de extração e junção rodam a cada mudança de prompt, modelo ou ontologia, com limiares.
- Ponta a ponta local: `docker compose up`, injetar eventos dos conectores falsos e verificar fatos no estado vivo, card no canal falso, aprovação, ação executada e auditoria registrada.
- Agentes/workflows: testes de produto para publicação, contexto suficiente, esclarecimento, retomada, permissão no painel e execução segura. O spike fica isolado em `spikes/adk-temporal/`; seus 13 testes não substituem esses critérios nem entram no CI de produto.
- O marco do kernel é validado instalando a distribuição de referência num projeto GCP de homologação. Isso só acontece com o seu pedido explícito.

## Próximo passo

A fatia 1 foi concluída em 13/11/2026: PXOS-1, PXOS-47 a 51, PXOS-7 e PXOS-46, com o teste de
saída verde no CI contra Postgres. A suíte de aceitação (`pxos-kernel/tests/acceptance/`) tem
um teste por requisito do §4; o req. 3 está verde e os demais são falhas esperadas que nomeiam
a issue que os entrega.

A fatia 2 foi concluída em 13/11/2026: o escriba pós-reunião leva um transcrito sintético, por conector
falso, até um compromisso aprovado pelo card em um canal falso, com o trecho literal como
evidência (PXOS-52 a 56). A extração é do Hindsight; o gateway de modelo fica fora. O teste de
saída também passou contra o Hindsight real com o modelo no Vertex, em execução local opt-in.
A fatia 3 foi concluída em 14/11/2026: pessoas e agentes leem e propõem pelo mesmo MCP, com
token OIDC e as permissões do banco (PXOS-57 a 60). O req. 1 saiu de `xfail`; o PXOS-30
continua aberto (busca, páginas, API, política e gateway). A proposta pelo MCP tem como fonte
quem chamou, e o dono de um compromisso precisa ser um ator conhecido. A conferência da frente
de trabalho foi para o K27.
A fatia 4 foi concluída em 14/11/2026: ator externo sem acesso ao contexto interno, token
recusado no MCP quando o banco recusa o ator, e logins próprios por instalação (PXOS-61 a 64).
Os reqs. 4 e 5 saíram de `xfail`; PXOS-8, 9 e 29 continuam abertos. O externo não recebe card,
porque o card mostra contexto interno.
A fatia 5 foi concluída em 14/11/2026: o escriba roda como workflow no Temporal, só com
referências, e sobrevive ao desligamento do worker enquanto espera a decisão no card (PXOS-69 a
71). A decisão é lida no kernel; o sinal da outbox só avisa. Ficaram fora o ADK, que exige o
gateway de modelos (PXOS-11), a delegação, a expiração e a revogação de aprovação, o teste de
isolamento entre namespaces e a retomada após morte do processo do worker (testou-se o
desligamento ordenado); o PXOS-36 continua aberto.
A fatia 6 entregou o gateway de modelos em 14/11/2026 (PXOS-72 a 74 e 76): cada instalação
grava a residência (`br` ou `global`) no bootstrap, o kernel deriva dela uma única rota, o
gateway só monta cliente para essa rota e o agente ADK recebe um modelo preso a ela; o
Hindsight usa a mesma rota. K12, K13 e K14 ficaram fora. A validação real no Vertex (PXOS-75)
segue aberta: embeddings funcionam em São Paulo, mas nenhum modelo de geração respondeu em
`southamerica-east1`, e o Claude não respondeu no endpoint global do projeto de teste. Um
`LlmAgent` ainda pode ser montado com o nome de um modelo, sem o gateway; isso fecha na fatia 7
(PXOS-77).
A fatia 7 foi concluída em 14/11/2026 (PXOS-78 a 80): a definição do escriba é uma versão
publicada no banco, aprovada pelo papel `platform-admin` pelo mesmo ciclo de proposta, e o
escriba só roda a partir da versão vigente. Na ingestão, um agente ADK montado pelo kernel,
com o modelo do gateway, lê cada fato extraído, e cada execução registra a versão que rodou.
A distribuição não importa o ADK.
Ainda em 14/11/2026, a rota de modelos deixou de ser fixa no código: provedor, modelos e
regiões vêm da configuração de deploy de cada instalação (`.env.example`), o bootstrap os
grava e um novo deploy os substitui. As rotas `br` e `global` e o padrão `br` da BRQ@work
saíram; qual região atende a residência de cada cliente é decisão do deploy (K44), com os
achados do PXOS-75 como insumo. Com isso o PXOS-75 foi fechado (PXOS-81) e a fatia 6
está concluída.
A fatia 8 foi concluída em 14/11/2026 (PXOS-82 a 86): a chamada de um agente a uma tool de
ação passa pela política, que permite (o fato é gravado na mesma chamada), encaminha para
aprovação ou nega, a partir da ação, dos papéis de quem o agente representa, dos parâmetros,
da fase de autonomia do agente (na instalação, padrão 1) e das regras do módulo (`max` e
`within_days` sobre os `rule_parameters`). A fase 2 só age direto em ação cujo módulo declara
regras; a fase 3 também nas demais; as regras declaradas sempre limitam. O banco recusa a
execução direta fora disso, a revisão de proposta já executada e a aprovação de ação
irreversível por agente com delegação. Toda chamada fica na auditoria; pessoas continuam
propondo. O req. 6 saiu de `xfail`. Ficaram fora o executor em sistemas externos, cofre de
credenciais e Agent Gateway (PXOS-32), o caminho síncrono com meta de latência (PXOS-33,
req. 7) e expiração e delegação de aprovação.
O escopo da próxima fatia é decidido com o mantenedor, e cada fase começa com confirmação
explícita.
