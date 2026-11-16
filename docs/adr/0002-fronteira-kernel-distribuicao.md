# ADR 0002 — Fronteira entre kernel e distribuição

**Status:** aceito em 13/11/2026. Responde à questão do PRD (§16) "governança da fronteira do kernel".

> **Refinamento do MVP (15/11/2026):** o [ADR 0006](0006-arquitetura-do-agente-especialista.md)
> mantém ADK, Temporal e LinkML, amplia o uso do Hindsight com `recall` e distingue
> contexto de estado canônico: o report semanal usa o ciclo canônico, revisado pelo PM.
> O kernel monta o agente e tem o workflow do ciclo do agente; a distro os configura na
> versão publicada. Envio autônomo é evolução futura.
>
> **Formas e materializações (16/11/2026):** o kernel oferece três formas: obter
> transcrições de reuniões, obter uma base de documentos e um canal. Cada adaptador que as
> materializa (Granola, SharePoint, Teams, e depois Meet, Drive, Zoom, Dropbox, Slack) é um
> pacote próprio em `connectors/`, distribuído igual para toda distro. A distro escolhe e
> configura os adaptadores; o FDE escreve só os que faltam. Um marketplace de conectores
> virá depois. O gate de qualidade mantém cada adaptador fora do kernel, da distro e dos
> outros adaptadores.

## Contexto

O PRD propõe que uma capacidade entre no kernel quando duas ou mais distribuições
precisam dela. Com uma distribuição só (BRQ@work), esse critério nunca dispara.
Precisamos de uma regra que funcione agora.

## Decisão

**Regra de natureza.** Pergunta: "esse código mudaria se o cliente fosse uma rede de
varejo em vez da BRQ?"

- **Não mudaria → kernel** (`pxos-kernel/`). Mecanismos sem vocabulário de domínio.
- **Mudaria → distribuição** (`brq-at-work/`). Tudo que fala a língua de um domínio ou de
  um sistema específico.

| Kernel | Distribuição BRQ@work |
|---|---|
| Ontologia base do PRD e metamodelo de ação | Pacotes de domínio (contexto de trabalho, projetos e operação) |
| Estado canônico, governança, bitemporalidade | Quem aprova o quê, como configuração |
| Mecanismo de extração e validação de evidência | Regras e prompts de extração |
| Contrato de conector, fila, idempotência | Conectores M365, Granola, Bud, portal |
| Abstração de canal, fila de revisão, cards gerados | Canal Teams, texto e UX dos cards |
| MCP, política, executor, auditoria, identidade | Agentes, Constituição BRQ, app de carteira |
| Adaptador do Hindsight, gateway de modelos | — |

**Desempate:** na dúvida, vai para a distribuição. Promover para o kernel depois é
barato; remover do kernel é caro.

**Árbitro:** o mantenedor. Cada caso cinzento decidido vira uma linha na tabela abaixo.

**Revisão:** quando existir a segunda distribuição, o critério do PRD (duas ou mais
distribuições) volta a valer para promoções.

## Garantias automáticas

- `brq-at-work` depende de `pxos-kernel`; nunca o contrário (contrato de importação no CI).
- Teste de neutralidade: o código e a ontologia do kernel não contêm termos de domínio.
- Um pacote fixture de outro domínio passa pelos mesmos fluxos nos testes do kernel.

## Casos decididos

| Data | Item | Lado | Motivo |
|---|---|---|---|
| 13/11/2026 | Pacote de contexto de trabalho | Distribuição | Vocabulário de domínio |
| 14/11/2026 | Tipos de regra da política (`max`, `within_days`, `roles`), efeito `approval` ou `deny`, avaliação na política e no banco | Kernel | Mecanismo comum a qualquer domínio |
| 14/11/2026 | Regras da política por ação (limites, quem pede, motivos para negar) | Distribuição, substituíveis no deploy | Regra de negócio do cliente, definida pelo FDE |
| 14/11/2026 | Fases de autonomia: fase 2 age direto só em ação reversível com regras e dentro delas; fase 3 também sem regras, e as regras sempre limitam; irreversível sempre por uma pessoa | Kernel | Requisito do PRD (§4 req. 6, §6.3); as regras definem "baixo risco" |
| 14/11/2026 | Fase de autonomia de cada agente | Instalação (deploy) | Decisão do cliente |
| 14/11/2026 | Aprovação sempre por uma pessoa agindo por si; delegação não vale para revisar | Kernel | Um único caminho de autonomia: fase mais regras |
| 16/11/2026 | Formas de obter transcrições de reuniões, base de documentos e canal; junção das gravações de uma mesma reunião numa descrição só; acesso seguindo a visibilidade na origem | Kernel | Mecanismo comum a qualquer cliente; "reunião" deixa de ser termo de domínio |
| 16/11/2026 | Adaptadores de ferramenta (Granola, SharePoint, Teams…) | Pacote próprio em `connectors/`, reutilizado por toda distro | Evita que cada FDE reescreva o mesmo adaptador; prepara o marketplace |
| 16/11/2026 | Escolha e configuração dos adaptadores (site, workspace, canal, marcas de acesso) | Distribuição e deploy | Decisão do cliente, feita pelo FDE |
