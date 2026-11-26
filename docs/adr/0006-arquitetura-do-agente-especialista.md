# ADR 0006 — Arquitetura do agente especialista no MVP

**Status:** aceito em 15/11/2026. Refina o MVP do PRD v0.27 e os ADRs 0001, 0002,
0004 e 0005. Esta decisão é documental; as capacidades pendentes não estão entregues.

> **Recorte de produção (16/11/2026):** as fatias 10 a 13 entregaram o ciclo com
> conectores, canal, memória e modelo falsos, o que é uma prova de conceito, não o MVP. O
> MVP é um recorte que vai para produção e valida a hipótese de valor: o sistema conectado
> onde o trabalho acontece.
>
> - **Base corporativa.** A Guilda coleta reuniões e documentos e torna o conhecimento
>   disponível para qualquer pessoa e qualquer agente em pé de igualdade. O acesso segue a
>   visibilidade na origem; não há vínculo de fonte com projeto nem agenda de projeto no MVP.
> - **Formas no kernel, adaptadores em pacotes próprios** (ADR 0002): transcrições de
>   reuniões (Granola, pela chave da workspace sobre as notas compartilhadas), base de
>   documentos (SharePoint pelo Graph, nas bibliotecas abertas à empresa) e canal (Teams).
>   O kernel junta as gravações de uma mesma reunião numa descrição só, a partir da mais
>   completa.
> - **Assistente de projeto.** O agente da BRQ@work é o assistente do projeto; sua primeira
>   função é o status report semanal. Outras funções e outros agentes vêm depois do MVP.
> - **Produção.** Um processo roda o worker do Temporal, o publicador da outbox e o
>   endpoint do bot, numa VM do GCP com compose; Vertex e Hindsight reais. Os fakes saem do
>   código de produto.
>
> Onde o texto abaixo fala em "escopo do piloto" ou "vínculo das fontes com projetos",
> vale esta nota.

## Contexto

O piloto valida um agente especialista da BRQ@work responsável pelo report semanal
ao cliente: acompanha o projeto, esclarece lacunas e prepara uma entrega revisada pelo
PM. A arquitetura deve servir a essa responsabilidade sem exigir o kernel completo.
Mantêm-se Google ADK, Temporal e LinkML, usando melhor o Hindsight sobre PostgreSQL
com pgvector. AlloyDB continua como opção de implantação compatível com PostgreSQL;
validar o piloto não exige outro banco vetorial ou de grafos.

## Decisão

### 1. Responsabilidades das ferramentas

- **Hindsight:** memória de contexto, extração e recuperação com fontes. O MVP amplia
  o uso além de `retain` com `recall`, sob escopo imposto pelo kernel (tags de acesso
  gravadas na retenção), nunca escolhido pelo agente. `reflect` e a consolidação nativa
  (observações) só entram depois de um teste de expurgo provar que excluir a fonte
  remove também seus derivados: no spike, excluir o documento não expurgou todos os
  traces de `reflect`. Não reconstruir um segundo extrator, resolvedor ou reconciliador
  na Guilda.
- **ADK:** executa o agente, interpreta a tarefa e usa as ferramentas disponibilizadas
  pelo kernel, entre elas a consulta ao Hindsight; a responsabilidade pela entrega e
  pelo uso de ferramentas continua no agente.
- **Temporal:** agenda, coordena etapas, espera respostas e revisão e retoma o trabalho,
  no workflow do ciclo do agente, que é do kernel. ADK roda em Activities; o histórico
  carrega referências, não conteúdo sensível.
- **LinkML:** fonte versionada do vocabulário e dos contratos de dados; os geradores
  nativos produzem Pydantic e JSON Schema. Validar estrutura não prova veracidade.

### 2. Fronteira kernel / distribuição

A regra de natureza do ADR 0002 permanece. Não criar abstrações sem consumidor no MVP.

| Kernel Guilda | Distribuição BRQ@work |
|---|---|
| Carregar LinkML, gerar modelos/schemas e validar dados; conceitos gerais só quando houver consumidor (hoje, `Actor`) | Pacote LinkML do report semanal: tipo canônico `WeeklyReport`, com as categorias do projeto (progresso, risco de atraso, atraso confirmado, prazo renegociado, impedimento, decisão, mudança) como enums |
| Integrar Hindsight e aplicar autorização às operações e fontes | Instruções de retenção e critérios para interpretar o projeto |
| Montar e executar ADK com modelo e ferramentas autorizados | Definição do agente especialista: instruções, ferramentas, agenda e critérios de qualidade |
| Workflow do ciclo do agente: agenda, rodada do agente, perguntas na thread com prazo, proposta do report, espera da revisão e nova rodada após rejeição | Configuração do ciclo na versão do agente: agenda, prazo das respostas, máximo de rodadas, thread do projeto e tipo do report |
| Identidade, isolamento, registro de fontes/versões e idempotência; formas de transcrições de reuniões (com a junção por reunião) e de base de documentos | Escolha e configuração dos adaptadores Granola e SharePoint (pacotes em `connectors/`) |
| Contratos mínimos de canal e o ciclo canônico de proposta e revisão autorizada | Escolha e configuração do adaptador Teams, thread do projeto, destinatários e papel do PM como aprovador do report |
| Persistir execuções, entregas, revisões e versão do agente utilizada | Conteúdo da entrega, regras do acompanhamento e configuração dos participantes |
| Configuração de modelos, credenciais e controles de execução | Integrações futuras com e-mail/WhatsApp e regras de envio do cliente |

Assim como o agente, o workflow segue a divisão núcleo no kernel, configuração na
distro. O kernel tem um único workflow fixo, o ciclo do agente; a distro só o
parametriza na versão publicada do agente. Mudar agenda ou prazos é, portanto, uma
versão aprovada, não um deploy de código, e a versão fica presa no início de cada
ciclo. Não é um interpretador genérico de workflows (K67 continua depois do MVP): os
passos são fixos e só os parâmetros variam. O ciclo passa no teste do ADR 0002, porque
não mudaria se o cliente fosse outro. A distro contribui com configuração e, no máximo,
código de ferramentas e conectores; não importa ADK, clientes de modelo nem Temporal.
O kernel nunca importa a distribuição.

### 3. Ontologia de contexto e contrato de entrega

O vocabulário específico do projeto fica na distro e só vira LinkML onde algum código
o consome: as categorias do report, que o agente recebe como schema de saída,
distinguindo por exemplo risco de atraso, atraso confirmado e prazo renegociado. Para a
memória, o vocabulário vai nas instruções de retenção da versão do agente e, se a
avaliação mostrar ganho, nos labels nativos do Hindsight. LinkML não configura Hindsight
nem gera comportamento automaticamente. Não construir um compilador genérico de prompts
nem um pacote de vocabulário que nenhum código lê.

Os schemas gerados estruturam a entrega e validam sua forma. O agente preserva fontes,
incertezas e contradições e pede esclarecimentos quando necessário. Na implementação,
avaliar a orientação nativa da extração antes de substituir integralmente suas regras.

Um conceito declarado em LinkML não precisa virar um fato canônico:

- Riscos, decisões e progresso mencionados nas fontes ficam no Hindsight como contexto.
- O report usa o ciclo canônico que já existe: cada versão é uma proposta do tipo
  `WeeklyReport`, com digest, revisada pelo PM (aprovador declarado no pacote). O banco
  recusa quem não é PM, quem não é pessoa agindo por si e a autoaprovação. O PM corrige
  rejeitando com motivo; o motivo fica registrado, alimenta o ciclo seguinte, e o agente
  refaz o report. O report aprovado é um fato porque autoriza o compartilhamento com o
  cliente e alimenta a medição do piloto. Revisar o report não aprova individualmente
  os fatos citados: riscos e decisões continuam contexto. A evidência obrigatória do
  report aponta a execução e o ciclo; cada item do report carrega suas fontes.
- Identidade, autorização, publicação de agentes e registros de execução continuam
  controlados pelo kernel. Fatos que autorizem ações ou alimentem medição seguem a
  fronteira canônica do ADR 0001 quando necessários; não duplicar toda a memória ali.

Permissões de leitura interna não autorizam divulgar conteúdo ao cliente. A entrega
deve respeitar seu público; o escopo da memória e o conteúdo compartilhável precisam
ser verificados na aplicação, e não apenas sugeridos por prompt. No MVP são três
camadas, sem classificador: o conector só ingere o escopo autorizado do piloto (documento
com ACL mais estreita não entra), o `recall` filtra pelas tags de acesso, e a revisão do
PM é o controle final antes do cliente.

### 4. Especialidade, ferramentas e autonomia

A responsabilidade do agente é contínua e independente do canal. No MVP, prepara o
report no Teams interno, o PM revisa e compartilha com o cliente. No futuro, o mesmo
agente pode receber ferramentas de e-mail ou WhatsApp e preparar e enviar o report
sozinho, dentro de condições autorizadas; respostas do cliente podem alimentar o contexto.

Disponibilizar uma ferramenta concede capacidade, não autorização automática. A
evolução deve separar ferramenta, destinatários autorizados, conteúdo compartilhável,
necessidade de aprovação e condições para escalar ao PM. O kernel registra a versão
enviada, destinatário e resultado; repetição depende do contrato do destino. Resultado
incerto não vira sucesso nem dispara reenvio cego.

O envio autônomo é direção futura, não escopo do piloto. Exige revisão explícita da
política atual que reserva ações irreversíveis à aprovação humana. Este ADR não altera
essa regra nem autoriza envios reais.

### 5. Publicação preservada

Mantém-se a publicação governada do agente: arquivo da distro como proposta, versão
aprovada imutável no banco e registro da versão em cada execução. A alternativa de
usar apenas Git para publicação não foi adotada. Builder e composição genérica ficam
para depois do MVP.

## Implementação e verificação pendentes

Hoje a porta de contexto só oferece `retain` e o adaptador desativa observações. Como o
report é um tipo canônico, o registro LinkML atual basta. Os ajustes entram nas fatias
existentes, sem restaurar o escriba removido no ADR 0005.

- **Fatia 10:** recuperação autorizada, exclusões e continuidade da memória.
- **Fatia 11:** interação em threads, vínculo de identidade, decisão pelo canal conferindo
  quem decide, motivo de rejeição retido como correção.
- **Fatia 12:** pacote LinkML do report, instruções explícitas, configuração do ciclo na
  versão do agente, ferramenta de `recall` e agente especialista montado pelo kernel.
- **Fatia 13:** workflow do ciclo do agente no kernel, configurado pela versão publicada,
  com espera, retomada e entrega única.
- **Fatia 14 (recorte de produção, 16/11/2026):** adaptadores Granola, SharePoint e Teams
  como pacotes próprios, junção de reuniões no kernel, fakes fora do produto, processo de
  execução e deploy na VM; depois, qualidade e esforço avaliados com o time real.

Os sete cenários do MVP continuam como contrato. As avaliações devem distinguir risco,
ocorrência e renegociação, exercitar correções, fontes e restrições e impedir que um
schema válido seja tratado como prova de qualidade. Não há teste novo nem mudança de
código de produto nesta etapa documental.
