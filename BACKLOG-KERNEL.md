> Linear: projeto pxOS (brq-px), https://linear.app/brq-px/project/pxos-38c291a57da8. O issue Kn deste arquivo é o PXOS-n.

# Backlog do kernel do pxOS (F0–F10), revisão 4

**Escopo:** só o kernel, ou seja, a Parte A do PRD, o §4 e o §13 no que for do kernel. A BRQ@work (F11) fica fora, no projeto BRQ@work do Linear.

**Regra:** cada issue implementa elementos do PRD, e cada elemento de kernel aparece em exatamente um issue (matriz no fim).

**Princípio de neutralidade:** o kernel não conhece tipos de domínio. Frente de trabalho, compromisso, risco e mudança de escopo vêm do pacote de ontologia, pelo mecanismo do K04.

**Decisões que orientam a revisão 3** (após os spikes, 13/11/2026):
- [ADR 0001](docs/adr/0001-memoria-contexto-e-estado-canonico.md): memória de contexto no Hindsight sobre AlloyDB; estado canônico tipado e governado próprio, em PostgreSQL. Graphiti, Neo4j, Mem0 e LangExtract rejeitados.
- [ADR 0002](docs/adr/0002-fronteira-kernel-distribuicao.md): fronteira por natureza; o kernel fica em `pxos-kernel/` e a BRQ@work em `brq-at-work/`. Itens de domínio vão para o projeto BRQ@work no Linear.
- [ADR 0003](docs/adr/0003-arquitetura-e-padroes-de-codigo.md): monólito modular, banco como guardião, núcleo funcional, adaptadores só nas bordas.
- **Instalação dedicada por cliente** é o padrão. Dentro dela continuam valendo as permissões internas. O modo compartilhado fica adiado.

**Revisão 4** (14/11/2026, PRD v0.26): [ADR 0004](docs/adr/0004-agentes-workflows-e-plataforma-de-construcao.md) adota Google ADK para agentes e Temporal como único motor de processos duráveis, e faz da área web do pxOS o lugar único para configurar a instalação, construir agentes, compor workflows, publicar e acompanhar. Toda mudança feita na plataforma vira versão publicada, imutável, com digest e aprovada por um papel; definições de agentes e workflows deixam de ficar no Git. Código de conectores e ferramentas e a ontologia continuam em código; editar a ontologia pela interface fica fora do escopo atual. K34 e K36 foram reescritos, e K65 a K68 entram na F8. Os itens novos usam o número do Linear (PXOS-65 a 68), para manter Kn = PXOS-n.

**Fatia 1** (primeira entrega de código): K01 (PXOS-1), K04 base mínima e metamodelo (PXOS-47), K05 Pydantic e JSON Schema (PXOS-48), K06 manifesto e registro de pacote (PXOS-49), RLS do K08 (PXOS-50), identidades fixas do K09 (PXOS-51) e K07 inteiro (PXOS-7); na BRQ@work, o pacote de contexto de trabalho (PXOS-46), cujo teste de integração é o critério de saída. K03 já está fechado pelo ADR 0001. Saída: o pacote de contexto de trabalho da BRQ@work e um pacote fixture de outro domínio fazem o ciclo proposta → aprovação → fato → correção, com leitura em duas datas, e nenhum tipo de domínio aparece em `pxos-kernel/`.

**Fatia 2** (escriba pós-reunião, com conector e canal falsos): partes de K18 e K19 (PXOS-52: conector falso de conteúdo, ingestão idempotente e publicador do outbox), K25 (PXOS-53: porta de contexto e retenção no Hindsight), K26 (PXOS-54: proposta com evidência literal conferida na fonte) e K23/K24 (PXOS-55: canal falso, card e encaminhamento ao aprovador); na BRQ@work, PXOS-56, cujo teste de integração é o critério de saída. A extração é do Hindsight (ADR 0001), então o K11 fica fora. No CI, o contexto usa o adaptador falso; a execução real é opt-in e local, com o Hindsight sobre o Postgres do Compose e o modelo no Vertex. Saída: um transcrito sintético vira compromisso aprovado pelo card, com o trecho literal como evidência, e um compromisso inventado sem trecho na fonte não vira proposta.

**Fatia 3** (ler e propor pelo MCP): partes de K29 (PXOS-57: token OIDC validado e ligado ao ator da sessão, com IdP local em dev), K30 (PXOS-58: servidor MCP com `list_*` por tipo canônico, `timeline` e uma tool de proposta por action_type, gerados do registro) e K26 (PXOS-59: campo que referencia um ator é conferido em código na proposta); na BRQ@work, PXOS-60, cujo teste de integração por HTTP com tokens é o critério de saída. Na proposta pelo MCP, a fonte da evidência é fixada pelo kernel (`mcp:<ator>`). A conferência da frente de trabalho foi para o K27, porque `WorkStream` é tipo de contexto e não há cadastro contra o qual conferir. Saída: pessoa e agente que age por ela leem o mesmo pelo MCP, com as permissões do banco; o req. 1 sai de `xfail`, e o K30 segue aberto (search, get_page, API, política e gateway).

**Fatia 4** (atores externos e credenciais por instalação): partes de K09 (PXOS-61: tipo `external_person`; o banco recusa a sessão cujo ator ou principal é externo, quaisquer que sejam os papéis, e card não chega a externo), K29 (PXOS-62: o token só entra no MCP se o banco aceitar o ator; desconhecido, externo ou agente sem delegação recebem 401) e K08 (PXOS-63: a migração revoga o `CONNECT` do `PUBLIC` e cada instalação tem logins próprios); na BRQ@work, PXOS-64, com duas instalações e um contato externo do cliente. Saída: os reqs. 4 e 5 saem de `xfail`. Ficam abertos no K08 a política da instalação (residência, pseudonimização, retenção, consentimento) e o provisionamento (K44); no K09, a entrada de externos pelos canais (K23); e a leitura de `actors`, `actor_roles` e `delegations` sem ator na sessão. Ao contrário do que se previu ao propor a fatia, o externo não recebe card, porque o card mostra contexto interno.

**Fatia 5** (escriba no Temporal): partes de K36 (PXOS-69: um workflow por proposta espera a decisão, conferida no kernel por Activity na sessão do agente; os eventos `fact.recorded` e `proposal.rejected` da outbox só avisam o workflow, e um workflow inexistente é ignorado; PXOS-70: a ingestão de uma fonte roda como workflow que recebe `source@version`, devolve ids de proposta e, se repetida depois do commit, encontra as mesmas propostas pela evidência); na BRQ@work, PXOS-71, com o worker desligado durante a espera e um novo worker terminando o workflow. Saída: o histórico do Temporal não contém o texto da fonte nem o trecho, inclusive em falhas, que chegam só com o tipo da exceção. Ficam abertos no K36 a expiração e a revogação, a etapa com ADK (depende do K11), a delegação, o isolamento por namespace, que por ora é só parâmetro, e a retomada após morte do processo do worker (testou-se o desligamento ordenado). Uma falha fora das transitórias (rede, conexão com o banco) encerra o workflow, que pode ser iniciado de novo com o mesmo id; o adaptador do Hindsight ainda não distingue falha transitória.

**Fatia 6** (gateway de modelos): partes de K11 (PXOS-72: residência `br` ou `global` gravada no bootstrap numa tabela de uma linha que a aplicação lê e não altera, rota única derivada dela e gateway que só monta cliente para essa rota; PXOS-73: o agente ADK recebe do gateway um modelo preso à rota, que recusa pedido de outro modelo antes de sair; PXOS-74: modelo de extração e embeddings do Hindsight vindos da rota; PXOS-75: validação opt-in no Vertex); na BRQ@work, PXOS-76, com instalações `br` e `global` lado a lado. Saída: uma instalação `br` não chega ao endpoint global nem pelo agente nem pelo Hindsight. O PXOS-75 segue aberto: no projeto de teste, embeddings funcionam em `southamerica-east1`, mas nenhum modelo de geração respondeu lá, e o Claude não respondeu no endpoint global ([registro](docs/validacoes/2026-11-14-rotas-vertex.md)). K12, K13 e K14 ficam fora. Um `LlmAgent` montado com o nome de um modelo ainda passa ao largo do gateway; fecha na fatia 7 (PXOS-77).

## F0 · Fundação
- **K01 Monorepo, ambiente local e CI.** Workspace uv na raiz com `pxos-kernel/` e `brq-at-work/` (ADR 0002). Docker Compose com loopback (Postgres 16 + pgvector). CI de produto a partir do zero: Ruff, mypy, pytest, import-linter (a distribuição depende do kernel, nunca o contrário) e teste de neutralidade. Os spikes continuam fora do CI. As interfaces de adaptador (req. 12) nascem na issue do primeiro consumidor (ADR 0003). Aceite: `docker compose up` sobe, o CI fica verde e um import do kernel para a distribuição quebra o CI.
- **K02 Dados de referência e esqueleto da suíte de aceitação.** Dados de fixture: 2 instalações, com permissão restrita entre pessoas dentro de uma delas, reaproveitando os episódios sintéticos dos spikes; dados pessoais sintéticos, conteúdos com prompt injection e transcrições com gabarito de fatos e junções, alinhados aos conjuntos do K17. Os 13 testes de requisito (§4) nascem como `xfail`. Os módulos falsos nascem cada um no issue do seu contrato (K04, K18, K23, K34, K43). Aceite: a suíte roda no CI e cada requisito tem um teste nomeado.
- **K03 Decisão de memória.** Fechado pelo ADR 0001: contexto no Hindsight sobre AlloyDB; estado canônico próprio em PostgreSQL. Os cinco critérios (duas datas, invalidação, isolamento, expurgo, portabilidade) estão avaliados por camada no ADR.

## F1 · Ontologia e geração
- **K04 Ontologia base, metamodelo e camadas.** Tipos e ações da base (Organização, Contrato, Ator, Papel, Evento, Evidência, Métrica, Resultado; registrar evento, registrar resultado, atribuir papel). Metamodelo de action_type: creates, approver, autonomia por fase, reversibilidade e parâmetros sujeitos a regra. Mecanismo para o pacote declarar:
  - o tipo âncora de vínculo (ex.: frente de trabalho);
  - as regras de extração;
  - as regras de roteamento (tipos que sempre vão para revisão).

  Camadas base → pacote → extensão por instalação (só propriedades a mais). Versão por camada, com migração obrigatória para mudança incompatível. Aprovação pelo dono da camada (CODEOWNERS). Cada tipo declara se é canônico (autoriza ação ou alimenta medição) ou só contexto (ADR 0001). Pacote de fixture de outro domínio para neutralidade; o pacote de contexto de trabalho é da BRQ@work. Aceite: os dois pacotes estendem a base e o CI barra mudança incompatível sem migração.
- **K05 Geradores de código a partir da ontologia.** Pydantic e JSON Schema vêm dos geradores nativos do LinkML (fatia 1); só as saídas específicas são próprias. O CI gera schemas de extração, modelos para agentes, tools MCP (leitura list_* por tipo e uma tool de ação por action_type), views tipadas do banco e definições de cards. Aceite: geração determinística, com testes de snapshot.
- **K06 Manifestos de módulo e de distribuição.** Formato e validador. O módulo declara tipos, ações, agentes, conectores, aprovadores, regras de política, permissões e versão mínima do kernel. A distribuição declara módulos com versão, políticas e configurações padrão. O manifesto de pacote fixa versão e digest: mudar o pacote invalida propostas abertas (validado no spike de governança). Aceite: os manifestos de fixture validam e os inválidos são rejeitados.

## F2 · Estado canônico, instalação e identidade
- **K07 Estado canônico governado.** Contrato do spike de governança promovido a produto: proposta imutável (payload validado pelo pacote, autor, entidade, início de vigência, evidência `source`/`quote`/`context_ref`, versão-base); aprovação presa ao digest, por aprovador declarado pelo pacote e diferente do autor, gravando decisão e fato na mesma transação; rejeição sem publicar; propostas concorrentes com um vencedor; consulta por data de validade e instante de conhecimento. A construir além do spike: intervalos de validade finitos, invalidação com data e a tabela outbox gravada na mesma transação (o publicador fica no K19). A tabela de aliases fica no K27 e a função de expurgo no K10. Imutabilidade garantida pelo banco (ADR 0003). Aceite: o req. 3 fica verde, nenhuma alteração destrutiva acontece fora do expurgo e cada mudança grava um evento na outbox.
- **K08 Instalação, política da instalação e permissões internas.** Provisionamento de uma instalação dedicada (serviços, credenciais e dados próprios) com a sua política: residência, pseudonimização, retenção, consentimento e IdP. Dentro da instalação, permissões internas por RLS para pessoas e agentes (validado no spike de execução). O Hindsight não tem RLS: o isolamento entre clientes vem da instalação, e o escopo interno vem do servidor. Aceite: o req. 4 fica verde (credencial de outra instalação é recusada e leitura restrita é negada pelo banco mesmo com bug no código).
- **K09 Identidade de atores.** Pessoas, agentes e atores externos; papéis e delegações; delegação (o agente lê como a pessoa, e o acesso amplo fica restrito à ingestão). Externos sem acesso às tools de leitura internas. Aceite: o req. 5 fica verde.
- **K10 Consentimento, exclusão, retenção e saída do cliente.** Consentimento por instalação controla o que pode ser ingerido. A exclusão, seja apagada na origem ou pedida pelo titular, remove de fato no índice, nos fatos derivados e nas páginas, usando uma função de expurgo restrita e auditada, criada aqui, no estado canônico e a exclusão nativa no Hindsight (com persistência de traces de modelo desligada), e deixando um registro da exclusão. O histórico do Temporal e as sessões do ADK não se editam, por isso só carregam referências (ADR 0004) e ficam fora do expurgo. Retenção por instalação. Exportação dos dados da instalação em formato aberto e remoção da instalação. Aceite: os testes de exclusão, retenção e exportação ficam verdes.

## F3 · Acesso a modelos, medição base e busca
- **K11 Gateway de modelos e roteamento por residência.** Ponto único para toda chamada a modelo de linguagem, inclusive as do ADK (ADR 0004). Escolhe modelo e região pela política da instalação (Gemini em São Paulo quando exige residência, Claude no endpoint global nos demais casos). A disponibilidade de regiões é validada aqui. Aceite: uma instalação com residência BR nunca chega ao endpoint global.
- **K12 Pseudonimização.** Troca dados pessoais por marcadores antes da chamada e restaura na resposta, conforme a política da instalação (SDP, ou adaptador local). Aceite: nenhum dado pessoal das fixtures sai sem marcador. O req. 11 fica verde, junto com o K10 e o K11.
- **K13 Inspeção nas chamadas a modelo.** Model Armor (ou stub local): instruções escondidas em conteúdo ingerido, vazamento de dado sensível e resposta fora da política. Aceite: as fixtures de prompt injection são bloqueadas ou sinalizadas.
- **K14 Telemetria de chamadas.** Trace com custo, modelo, região e instalação (OpenTelemetry → Cloud Trace e base analítica). Aceite: 100% das chamadas têm trace completo.
- **K15 Base analítica: armazenamento.** Histórico operacional em volume, séries e destino dos traces e da medição (BigQuery, ou DuckDB/Postgres em local), por instalação. Aceite: traces e registros das fixtures chegam à base da instalação.
- **K16 Busca autorizada sobre o contexto.** Recuperação pelo Hindsight (recall), com escopo de leitura aplicado no servidor pela identidade do ator e link para a origem. Sem índice pgvector próprio (ADR 0001). Aceite: um trecho restrito não aparece para quem não tem permissão, inclusive em respostas, listagens e grafos.
- **K17 Harness de avaliação contínua.** Conjuntos de referência rodam a cada mudança de prompt, modelo ou ontologia, com limiares que quebram o CI. Aceite: uma regressão proposital falha o CI.

## F4 · Ingestão e conectores
- **K18 Contrato e runtime de conectores.** Declaração de leitura, mapeamento, ações, acesso e instalação. Escopo mínimo por instalação. Restrição de permissão da origem carregada para o pxOS, ou o conteúdo não é ingerido. Amarração à entidade âncora declarada pelo pacote. Conectores falsos de conteúdo e de registros operacionais. Aceite: os conectores falsos rodam pelo contrato.
- **K19 Fila de eventos confiável.** Pub/Sub, publicador idempotente da outbox do K07, processamento idempotente, eventos repetidos ou fora de ordem, reconciliação periódica, carga inicial do histórico e marcação de evento sem vínculo. Aceite: o replay embaralhado não gera duplicatas.
- **K20 Modo aviso de mudança e leitura incremental.** Receptor de avisos, busca do delta desde a última leitura, normalização e envio para a fila. Aceite: o req. 2 fica verde com o conector falso de conteúdo.
- **K21 Modo captura de mudanças.** Inserção, alteração ou exclusão viram eventos estruturados, mapeados direto para a ontologia sem modelo, e seguem para o estado vivo e a base analítica (Datastream, ou CDC local). Aceite: o req. 8 fica verde.
- **K22 Modo leitura periódica.** Leitura agendada com diff contra a anterior, emitindo só mudanças. Aceite: duas leituras iguais geram zero eventos.

## F5 · Canais e revisão
- **K23 Abstração de canais.** Entregar mensagens e cards; receber pedidos e aprovações; atores externos entram só pelos canais, com identidade separada. Canal falso de referência. Aceite: ida e volta pelo canal falso.
- **K24 Fila de revisão e encaminhamento.** Fila genérica, para fatos e para ações, com cards gerados da ontologia. Encaminha ao dono certo (aprovador do action_type ou dono do fato), com prazo, escalonamento para substituto ou gestor e expiração (ação expirada não executa). Toda correção é emitida como evento de aprendizado. A decisão é presa ao digest do que foi revisado. Aceite: os cenários aprovar, corrigir, rejeitar, escalar e expirar ficam verdes.

## F6 · Proposta canônica e identidade

A extração de contexto é do Hindsight (ADR 0001). Esta fase não reconstrói extrator, resolvedor ou reconciliador: ela transforma contexto em propostas canônicas verificáveis.
- **K25 Consumidor e retenção no contexto.** Lê e prioriza eventos; evento sem vínculo vai para triagem; os vinculados vão ao Hindsight com fonte estável, versão e escopo de acesso. Inclui subir o Hindsight na instalação: adaptador (porta de contexto), extensão de escopo de leitura e persistência de traces de modelo desligada. Aceite: um evento sem vínculo cai na triagem e o replay da mesma fonte não duplica memórias.
- **K26 Proposta canônica e validador de evidência.** Para os tipos canônicos, gera propostas tipadas a partir do contexto, com trecho e minuto. Regras vêm do pacote, mais o descarte de assunto pessoal ou de RH. Validação em código: schema, IDs, prazos, dono e evidência literal conferida na fonte. Inclui o conjunto de avaliação. Aceite: uma proposta sem evidência literal é descartada e a precisão é medida.
- **K27 Identidade canônica.** Chave determinística, tabela de aliases e restrição de candidatos são próprias; a sugestão por similaridade vem do contexto (Hindsight) e a dúvida vai para a fila humana. A identidade nunca é decidida só por modelo; na dúvida não junta; junções reversíveis; correção de card vira alias. Inclui o conjunto de avaliação de junção. Aceite: precisão de junção de 99% ou mais nas fixtures, e uma correção vira alias.
- **K28 Roteador de propostas.** Decide criar, atualizar ou invalidar sobre o estado canônico do K07 (nunca apagar; contradição invalida com data). Confiança por tipo com limiares calibrados e regras de revisão obrigatória vindas do pacote; abaixo do limiar vira card. Correções recalibram limiares. Aceite: uma contradição invalida o fato anterior e o tipo de revisão obrigatória vira card.

## F7 · MCP, API e gateway de ação
- **K29 Autenticação.** Token do IdP configurado na instalação (OIDC genérico; IdP local em dev). Identifica pessoa ou agente com papel e delegações. Aceite: um token inválido ou de ator externo é recusado nas tools internas.
- **K30 MCP e API: catálogo de tools.** Leitura genérica (search, get_context, timeline e get_page), list_* geradas por tipo e uma tool de ação por action_type (proposta → K31). Constituição servida com fonte. Mesmas permissões para pessoas, agentes e apps. Registro de uso das leituras (consumido pelo K37). MCP exposto pelo Agent Gateway, com Model Armor no tráfego dos agentes. Aceite: o req. 1 fica verde.
- **K31 Serviço de política e autonomia.** Ação × papel × parâmetros × fase de autonomia × regras do módulo dão permitir, aprovar ou negar. Fases assistido, supervisionado e autônomo; a regra do módulo limita a fase; ação irreversível sempre exige aprovação. Integração com o Agent Gateway via Service Extensions (validada aqui). Sem motor de política externo (OPA, OpenFGA) até haver lacuna demonstrada. Aceite: o req. 6 fica verde.
- **K32 Executor de ações, credenciais e auditoria.** Único caminho de escrita, via conectores ou servidor MCP do fornecedor atrás do Agent Gateway. Consome aprovações do K24. Credenciais OAuth de saída por agente (Secret Manager). Log de quem pediu, o que pediu, decisão, aprovação e resultado. Validado localmente no spike de execução e mantido como aceite: executa só o conteúdo exatamente aprovado, confere revogação na hora de executar, reentrega concorrente gera um único efeito. Aceite: toda ação das fixtures tem auditoria completa.
- **K33 Caminho síncrono.** Ação pré-aprovada avaliada e executada na mesma chamada, sem fila; nada que exija aprovação entra nesse caminho. A meta de p95 é definida e medida aqui. Aceite: o req. 7 fica verde.

## F8 · Construção e operação de agentes e workflows
- **K34 Runtime de agentes.** Google ADK (ADR 0004). A definição do agente é uma versão publicada no banco da plataforma, imutável, com digest e aprovada por um papel: função, instruções, contexto permitido, modelo, ferramentas, saída esperada, responsável e autonomia. Antes da interface (K66), publica-se pela API ou pelo MCP; as definições não ficam no Git. A execução registra a versão que rodou. Identidade própria por agente, fase de autonomia, leitura com delegação, chamadas de modelo pelo K11 e métricas de progressão (aprovação sem ajuste, incidentes) para decisão do sponsor. Agente falso de referência. Aceite: o agente de referência, a partir de uma versão publicada, lê via MCP e age via gateway na fase 1.
- **K35 Gatilhos por agenda e por evento.** Agenda por Temporal Schedules, no horário de cada pessoa ou papel; evento a partir das mudanças publicadas pelo K07 (outbox). Aceite: os dois gatilhos acionam o agente de referência.
- **K36 Fluxos de longa duração.** Temporal como único motor (ADR 0004; sem motor próprio). A chamada ADK roda numa Activity. A decisão de revisão é registrada no kernel (K24) e chega ao workflow como sinal a partir da outbox, sem segunda fila; expiração e revogação são conferidas no kernel. O workflow carrega só referências, nunca conteúdo ou dados pessoais. Namespace Temporal e credenciais de worker por instalação; o worker abre a sessão no banco como o agente, agindo pela pessoa. Aceite: um fluxo de fixture com etapa humana completa quando aprovado, expira no prazo e retoma após queda do worker sem repetir etapas concluídas.
- **K65 Área web: aprovações, execuções e memória.** Primeiro passo da plataforma (ADR 0004). Login pelo IdP da instalação. Aprovações sobre a mesma fila do K24, com decisão única entre web e canais. Painel de execuções por instalação, função, período e estado, com etapa, versão, evidência, ferramentas, aprovação, resultado, custo e erro, correlacionando Temporal e ADK com proposta e ação do gateway; só intervenções autorizadas, sem retry cego para efeito incerto. Consulta à memória: contexto, fatos vigentes, fontes e histórico. A biblioteca de interface é escolhida aqui. Aceite: uma decisão tomada no canal falso aparece na web, e vice-versa; um usuário não vê execução ou fato que o banco não lhe permite.
- **K66 Agentes por formulário.** Formulário sobre o catálogo, teste com exemplos e resultados esperados, publicação pela API de versões do K34. Aceite: um agente é configurado, testado e publicado sem código; editar o rascunho não muda a versão publicada.
- **K67 Workflows compostos na plataforma.** Gatilho, etapas de agente, ações, condições, aprovações, prazos e falhas por formulário ou modelo sobre blocos registrados pelo FDE; composição visual só se a necessidade aparecer. Mesma publicação governada; a execução referencia a versão publicada. Aceite: um workflow publicado roda no K36, e uma versão nova não altera processos em andamento.
- **K68 Configuração da instalação na plataforma.** Conectores já existentes (credencial no cofre, escopo, mapeamento para a entidade âncora, estado da ingestão), papéis, aprovadores e fases de autonomia, publicados como versão aprovada. Código de conectores fica com o FDE; editar a ontologia pela interface fica fora. Aceite: instalar um conector falso e mudar um aprovador pela plataforma gera versões aprovadas que o kernel passa a usar.

## F9 · Manutenção, analíticos e resultado
- **K37 Job de manutenção.** Execução diária: deduplicação, detecção de conflitos e envelhecimento pelo registro de uso, com fila de revisão. Aceite: o req. 9 fica verde.
- **K38 Páginas narrativas.** Uma página por entidade do tipo âncora do pacote, regenerada do estado vivo, citando os fatos, sem edição manual. Servida por get_page. Aceite: corrigir um fato regenera a página.
- **K39 Constituição e propostas de mudança.** Estrutura da Constituição em Markdown (método, playbooks, papéis, rituais, glossário). A manutenção abre PRs de proposta. Aceite: um PR de proposta é gerado a partir das fixtures.
- **K40 Modelos analíticos como tool.** Previsão, classificação e anomalia na base analítica (BigQuery ML, Vertex, ou local), expostas como tool ao agente. Aceite: o agente de referência chama um modelo de referência como tool.
- **K41 Medição de resultado.** Custo, esforço e qualidade por agente, por entidade âncora e por contrato; linha de base e indicadores de resultado; trilha auditável pelo cliente. Aceite: o req. 10 fica verde.

## F10 · Ciclo de vida, apps e implantação → marco kernel completo
- **K42 Ciclo de vida de módulos e distribuições.** Instalar, atualizar e remover por instalação, com regeneração de schemas, tools e views. Permissões por módulo: ler tipos de outro módulo exige permissão no manifesto. Versões compatíveis com aviso e período de transição. Aceite: o req. 13 fica verde (nenhum tipo de domínio no código do kernel; a distribuição de referência é feita só de módulos).
- **K43 Apps e kit de construção.** Banco transacional por app (Cloud SQL), fatos e referências espelhados no estado vivo, acesso só por MCP, API e canais, componentes de interface, fluxos sobre o K36. App falso de referência. Aceite: o app roda e seus fatos aparecem no MCP.
- **K44 Infraestrutura como código: instalação dedicada.** Terraform para os serviços do kernel em modo dedicado (projeto GCP por cliente), a partir do mesmo código e de imagens versionadas, sem fork por cliente; dados nunca circulam entre instalações. Inclui a hospedagem do Temporal (própria ou gerenciada) e dos workers ADK, com residência, disponibilidade, recuperação, custo, retenção do histórico e atualização compatível com processos em andamento. O modo compartilhado fica adiado. Aceite: plan válido; apply em homologação só mediante pedido explícito.
- **K45 Atualização do kernel e marco.** Suíte por distribuição rodada contra a versão nova do kernel antes da atualização, com o mesmo processo para instalações dedicadas. A distribuição de referência é instalada do zero. Auditoria de portabilidade: LinkML, Postgres, OpenTelemetry, e serviços gerenciados só atrás de adaptadores. Aceite: os 13 requisitos verdes (o req. 12 fica verde aqui) e um upgrade simulado aprovado.

## Matriz de cobertura (PRD → issue)
| Elemento do PRD | Issue |
|---|---|
| §5 Repositório Git, CI; req. 12 (adaptadores) | K01 |
| Verificabilidade do §4 | K02 |
| §16 construir ou adotar a memória (ADR 0001) | K03 |
| §7 ontologia, camadas, versão, aprovação; metamodelo de ação | K04 |
| §7 geração (schemas, tools, views, cards, modelos) | K05 |
| §8 manifestos | K06 |
| §5 estado vivo, duas datas; §5 mudanças → Pub/Sub | K07 |
| req. 4 RLS; política por instalação | K08 |
| req. 5; §13 externos, delegação | K09 |
| req. 11 consentimento e retenção; §13 exclusão, ativo vs. dado do cliente | K10 |
| §6.4 roteamento por residência | K11 |
| §6.4 pseudonimização | K12 |
| §6.4 inspeção | K13 |
| §6.4 registro; §5 medição (traces) | K14 |
| §5 base analítica (armazenamento) | K15 |
| §5 índice de busca (busca autorizada sobre o contexto) | K16 |
| §5 avaliação contínua | K17 |
| §6.1 contrato, escopo mínimo, permissões da origem | K18 |
| §6.1 idempotência, reconciliação, carga inicial, sem vínculo | K19 |
| §6.1 aviso de mudança | K20 |
| §6.1 captura de mudanças; req. 8 | K21 |
| §6.1 leitura periódica | K22 |
| §5 canais | K23 |
| §6.3 encaminhador; §8 revisão humana; §16 correção vira aprendizado | K24 |
| §6.2 consumidor; retenção no contexto e adaptador do Hindsight | K25 |
| §6.2 proposta canônica e validador de evidência | K26 |
| §6.2 identidade canônica | K27 |
| §6.2 roteador de propostas (gravação no K07) | K28 |
| §6.3 autenticação | K29 |
| §6.3 catálogo, tools de leitura e de ação; §8 Constituição pelo MCP; §13 Model Armor no Agent Gateway | K30 |
| §6.3 política e autonomia | K31 |
| §6.3 executor, credenciais, auditoria; §6.1 MCP do fornecedor | K32 |
| §6.3 caminho síncrono; req. 7 | K33 |
| §5 agentes; §8 como nasce um agente; versão publicada do agente (ADR 0004) | K34 |
| §5 gatilhos | K35 |
| §5 fluxos (Temporal) | K36 |
| §5 manutenção; req. 9 | K37 |
| §3 páginas narrativas | K38 |
| §3 Constituição e propostas | K39 |
| §6.4 modelos analíticos | K40 |
| §5 medição e resultado; req. 10 | K41 |
| §8 ciclo de vida, versões, permissões; req. 13 | K42 |
| §8 apps, kit, dados do app | K43 |
| §5 implantação dedicada (compartilhada adiada) | K44 |
| §8 atualização do kernel; req. 12 (verificação) | K45 |
| Construção e acompanhamento: área web, aprovações, execuções, memória | K65 |
| Construção: agentes por formulário, teste e publicação | K66 |
| Construção: workflows compostos e publicados | K67 |
| Construção: configuração da instalação | K68 |
