# pxOS

Monorepo do kernel pxOS e suas distribuições, incluindo BRQ@work.
O kernel está em construção, em fatias verticais (ver o roadmap); spikes são experimentos isolados.

## Conteúdo

- [PRD](pxos-brq-work-prd.html)
- [Roadmap](ROADMAP.md) e [backlog](BACKLOG-KERNEL.md)
- [Decisões de arquitetura](docs/adr/)
- [Ontologia e governança](spikes/ontology-governance/README.md)
- [Graphiti](spikes/graphiti-memory/README.md) e [Hindsight](spikes/hindsight-memory/README.md)

## Desenvolvimento

O código de produto fica em `pxos-kernel/` (kernel) e `brq-at-work/` (distribuição),
num workspace uv com Python 3.12. A fronteira entre os dois está nos ADRs 0002 e 0003.

```sh
uv sync
docker compose up -d --wait   # Postgres 16 + pgvector em 127.0.0.1:25432
uv run ruff check pxos-kernel brq-at-work
uv run ruff format --check pxos-kernel brq-at-work
uv run mypy
uv run lint-imports          # o kernel não importa a distribuição
uv run pytest                # neutralidade e integração; exige o Postgres do Compose
```

Os testes de workflow sobem o servidor de desenvolvimento do Temporal (`temporal_testing.py`,
versão fixa). O SDK baixa o binário na primeira execução, que por isso precisa de rede.

Modelos Pydantic e JSON Schema são gerados do LinkML de cada pacote (`generated.py` e
`generated.schema.json`, ao lado do `ontology.yaml`). Depois de mudar uma ontologia:

```sh
uv run python -m pxos_kernel.ontology.generate pxos-kernel/tests/retail_fixture \
  pxos-kernel/src/pxos_kernel/agents/package brq-at-work/src/brq_at_work/ontology/work_context
```

O teste de snapshot e o CI (`--check`) falham se o gerado divergir do LinkML.

Para ver o ciclo canônico funcionando (proposta, recusas, aprovação, correção e leitura
em duas datas) num banco local `pxos_demo`:

```sh
PYTHONPATH=. uv run python brq-at-work/examples/slice1_demo.py
```

No CI, a memória de contexto é o adaptador falso. A execução real é opt-in e local, com o
Hindsight sobre o Postgres do Compose e o modelo no Vertex (gasta tokens no projeto
indicado):

```sh
GOOGLE_APPLICATION_CREDENTIALS=... PXOS_VERTEX_PROJECT=... scripts/hindsight-local.sh
PXOS_HINDSIGHT_URL=http://127.0.0.1:18888 uv run pytest -k hindsight
```

Cada instalação declara a residência (`br` ou `global`) no bootstrap; a BRQ@work usa `br`
por padrão. Dela sai a única rota de modelos da instalação: o gateway entrega ao agente ADK
um modelo preso a essa rota, e o script do Hindsight deriva da mesma rota o modelo, os
embeddings e as regiões (`PXOS_RESIDENCY`, padrão `br`). A validação das rotas no Vertex é
opt-in e gera cobrança; o resultado de 14/11/2026 está em
[docs/validacoes](docs/validacoes/2026-11-14-rotas-vertex.md):

```sh
GOOGLE_APPLICATION_CREDENTIALS=... PXOS_VERTEX_PROJECT=... uv run pytest pxos-kernel/tests/test_vertex_routes.py
```

Para usar o contexto de trabalho por MCP (fatia 3), suba o servidor local. Ele recria o banco
`pxos_mcp` com um compromisso aprovado e usa um IdP de desenvolvimento, cuja chave fica em
`.secrets/`:

```sh
PYTHONPATH=. uv run python brq-at-work/examples/mcp_local.py serve
```

Em outro terminal, emita o token de um ator e registre o servidor no Claude Code. Um agente
agindo por uma pessoa usa `token bruno --agent meeting-agent`:

```sh
TOKEN=$(PYTHONPATH=. uv run python brq-at-work/examples/mcp_local.py token celia)
claude mcp add --transport http brq-at-work http://127.0.0.1:18000/mcp \
  --header "Authorization: Bearer $TOKEN"
```

O token vale 12 horas; depois disso, emita outro e registre o servidor de novo (`claude mcp
remove brq-at-work` antes do `add`). As tools são `list_commitments`, `timeline` e
`create_commitment`, além de `list_agent_versions` e `publish_agent_version` (fatia 7). A
proposta de compromisso segue para a gerente de projeto; enquanto não há canal real, a
aprovação do card é feita com `mcp_local.py approve <proposal_id> --as ana`.

O MCP só aceita um token se o banco aceitar o ator dele (fatia 4). A Dora é contato do
cliente, uma atora externa: o token dela (`token dora`) é assinado pelo IdP da instalação e
mesmo assim recebe 401, assim como o de um ator desconhecido ou de um agente sem delegação.
Cada instalação tem banco, logins e IdP próprios; o login de uma não abre o banco de outra.

O escriba também roda como workflow no Temporal (fatia 5): o workflow recebe só a referência
do transcrito, a Activity ingere como o agente e devolve ids de proposta, e o workflow espera a
decisão da gerente de projeto. O publicador da outbox avisa o workflow quando a proposta é
aprovada ou rejeitada; a decisão vale quando lida no banco. O histórico do Temporal guarda ids,
nunca o que foi dito.

O escriba roda a partir de uma versão publicada (fatia 7). A definição dele (instruções do
agente ADK, instruções para a memória de contexto e a ação que propõe) é uma proposta de
`publish_agent_version`, aprovada por quem tem o papel `platform-admin`; na BRQ@work, a Eva.
Uma instalação nova não roda o escriba até haver versão aprovada: o bootstrap não cria
nenhuma, e `brq_at_work.scribe.DRAFT` é só o texto proposto como primeira versão. Uma versão
nova muda a mesma entidade (`entity_id` e `base_revision` na tool). Na ingestão, um agente
ADK montado pelo kernel, com o modelo do gateway, lê cada fato extraído; cada execução
registra a versão que rodou.

O CI de produto (`.github/workflows/ci.yml`) roda os mesmos checks. Os experimentos em
`spikes/` ficam fora do workspace e do CI; seus procedimentos estão nas respectivas pastas.

Veja [contribuição](CONTRIBUTING.md), [segurança](SECURITY.md) e [Apache-2.0](LICENSE).
