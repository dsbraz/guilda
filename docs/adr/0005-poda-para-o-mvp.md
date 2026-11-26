# ADR 0005 — Poda do código para o MVP

**Status:** aceito em 15/11/2026. Responde ao PRD v0.27 (§MVP). Complementa o ADR 0004 (nota
de escopo de 15/11/2026).

> **Refinamento do MVP (15/11/2026):** o [ADR 0006](0006-arquitetura-do-agente-especialista.md)
> mantém ADK, Temporal e LinkML, amplia o uso do Hindsight com `recall` e distingue
> contexto de estado canônico: o report semanal usa o ciclo canônico, revisado pelo PM.
> O kernel monta o agente e tem o workflow do ciclo do agente; a distro os configura na
> versão publicada. Envio autônomo é evolução futura.

## Contexto

O primeiro marco passou a ser o piloto do agente de acompanhamento de projeto. A avaliação
do código contra o MVP mostrou partes entregues que o piloto não usa e que aumentavam a
superfície de brechas: o MCP com OIDC, a política de ação de agentes com fases de autonomia
e o escriba pós-reunião, que transformava cada fato extraído em proposta canônica. O
mantenedor decidiu manter só o código necessário e suficiente para o MVP, podando inclusive
o que já tinha sido entregue.

## Decisão

1. **Sai do produto:**
   - o servidor MCP e a autenticação OIDC (fatias 3 e 8, K29/K30), com as dependências
     `mcp` e `pyjwt`;
   - a política de ação, a autonomia por ator, a auditoria de execução e os campos
     `reversible` e `rule_parameters` do metamodelo (K31 parcial);
   - o escriba: o leitor de fatos ADK, a conferência do trecho literal, a proposta por fato,
     a espera de revisão no Temporal e seus sinais, a leitura do que a memória extraiu, o
     pacote `work_context` da BRQ@work e o campo `proposes` da definição de agente (fatias
     2 e 5, partes de K24, K26 e K34);
   - no estado canônico: a invalidação de fatos (com a coluna `operation`, que ficaria com
     um único valor), a leitura de uma entidade em duas datas, o histórico de uma entidade,
     a busca de propostas por contexto e a leitura de revisão;
   - na ontologia base: `Organization`, `Role`, `Event` e `Evidence`. Fica só `Actor`, e o
     tipo de contexto (`guilda.kind: context`) sai;
   - os atores e as delegações sem uso nas identidades da distro e do fixture.
2. **Fica:** o ciclo proposta → revisão por pessoa → fato, o registro e a publicação de
   versões de agente, o registro da versão em cada execução, a ingestão idempotente por
   versão da fonte, agora só de contexto, a porta de contexto (só `retain`), o gateway de
   modelos, a outbox e o canal de revisão, o isolamento por instalação e a recusa de
   externos.
3. **Ingestão só de contexto:** ingerir uma fonte retém o texto na memória de contexto com
   as instruções da versão do agente em vigor, registra a execução e não propõe nada. Por
   enquanto, a ingestão ainda roda sob o ator do agente.
4. **Agente como configuração:** a definição do agente semanal será um arquivo da
   distribuição, com instruções, ferramentas e agenda, publicado pelo ciclo governado. A
   distribuição configura também o ciclo do agente (refinamento do ADR 0006), mas não monta o agente nem escreve o workflow: o kernel o monta a partir da versão aprovada, com o
   modelo do gateway. Um teste impede a distribuição de importar ADK ou clientes de
   provedor de modelo. Não há builder no MVP.
5. **Migrações editadas:** como não há instalação de produção, a cadeia foi editada em vez
   de ganhar migrações de remoção. As migrações 0009 e 0011 saíram, a 0010 virou 0009 e
   passou a criar sozinha a regra "só uma pessoa agindo por si revisa" (PX018), e as
   migrações 0002, 0003 e 0007 perderam a coluna `operation`. Bancos locais anteriores
   precisam ser recriados.

## Consequências

- Os requisitos 1 (mesmo MCP para pessoas e agentes) e 6 (ação com política e fase de
  autonomia) voltaram a ser falha esperada estrita. Seguem verdes os requisitos 3
  (evidência), 4 (isolamento por instalação) e 5 (externo sem contexto interno).
- Os sete cenários de aceitação do MVP estão em `brq-at-work/tests/test_mvp_scenarios.py`
  como falha esperada estrita. Cada fatia troca os seus pelo teste real.
- Hoje nenhum caminho do produto publica uma versão de agente: o MCP fazia isso. O comando
  da distribuição que publica o arquivo do agente vem na fatia 12.
- Desvios do plano: as saídas das fatias 1 e 7 (`test_slice_exit.py`,
  `test_slice7_exit.py`) foram apagadas, porque o ciclo de varejo e a publicação de versão
  já estão cobertos nos testes do kernel. A Dora continua nas identidades, porque o teste
  de isolamento da fatia 4 a usa.
- Fatia 9b (15/11/2026, depois do ADR 0006): saíram o conteúdo guardado em
  `guilda.sources`, que ninguém lia e dobrava o que a exclusão de uma fonte precisa expurgar,
  e o fim de vigência (`valid_to`) de propostas e fatos, que nem a versão de agente nem o
  report semanal usam. A anotação `guilda.refers_to` fica: o campo `agent` da versão de
  agente a usa para exigir um ator existente.
- O MCP, a política e as ações externas voltam depois do MVP, junto de K30–K33, com testes
  novos. A história das fatias continua no backlog e no Git.
