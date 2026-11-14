# Qualidade de código

`quality.policy.json` é a fonte dos limites, escopos, áreas de mutação e exclusões.
O executor gera a configuração de Ruff, Coverage.py, import-linter e Mutmut em execução.
As versões resolvidas estão em `uv.lock`. Spikes não participam destes comandos.

```sh
uv sync --locked
uv run python -m quality fast
uv run python -m quality ci
uv run python -m quality mutation
uv run python -m quality all
```

`fast` verifica lint, formatação, tipagem, tamanho e arquitetura. `ci` acrescenta geração
determinística, testes do produto e dos gates, cobertura por área e classificação dos
resultados dos testes. `mutation` executa Mutmut em uma cópia temporária. `all` exige ambos
os relatórios aprovados para o mesmo commit limpo e a mesma política.

PostgreSQL real é obrigatório para `ci` e `mutation`. Use `docker compose up -d postgres`
ou `PXOS_TEST_DATABASE_URL` com um servidor de testes. Os testes recriam seus bancos;
não execute duas suítes simultâneas no mesmo servidor. No CI cada job tem seu serviço
PostgreSQL. Temporal usa o servidor de desenvolvimento já utilizado pelos testes.

## Evidências e aprovação

JSON, Markdown e logs ficam em `quality-reports/` (ignorados pelo Git); `--output` permite
outra pasta. O relatório registra SHA, estado e hash do workspace, política, versões,
escopo, resultados, duração e métricas. Uma execução com alterações pode orientar o
desenvolvimento, mas não aprova integração. Uma edição durante a execução invalida a
evidência. `all --aggregate` verifica relatórios existentes sem repetir campanhas.

Falha, cancelamento, infraestrutura indisponível, arquivo não medido, área ausente,
relatório inválido e mutação incompleta reprovam. Mutantes mortos, sobreviventes, sem
testes, inválidos, pendentes e timeouts são separados. Erros internos do pytest são
inválidos, mesmo quando o Mutmut os classifica como mortos. O denominador inclui todos
os mutantes gerados; timeout não é morte. A campanha usa um processo de teste por vez
para evitar disputa pelos bancos e não limita seleção por profundidade de chamada ou
linhas cobertas. A mutação Python não comprova invariantes SQL.

Os testes externos opt-in permanecem identificados na política. Skips inesperados e
retries automáticos reprovam. Requisitos pendentes são `xfail` estritos, aparecem
separadamente e não representam funcionalidades entregues.

CI publica os artefatos mesmo em falha e os mantém por 90 dias. O agregador consulta até
dez artefatos disponíveis da main, compara com a última aprovação de política compatível
e registra ausência de histórico como aviso. Localmente, `GITHUB_REPOSITORY=owner/repo
uv run python -m quality.history_fetch` usa a autenticação do `gh` para obter esse histórico.

## Fitness functions e evidências existentes

| Propriedade | Verificação |
| --- | --- |
| Kernel independente da distribuição; APIs públicas; ausência de ciclos | Contratos gerados do import-linter; fixtures em `tests/quality/test_configuration.py` |
| Outro domínio percorre o mesmo fluxo | `test_neutrality.py`, `brq-at-work/tests/test_slice_exit.py` |
| Isolamento, permissões, imutabilidade por SQL como aplicação | `test_database_guard.py`, `test_identities.py`, `test_installation.py`, `test_slice4_exit.py` |
| Aprovação antes de fatos; estado e outbox atômicos | `test_canonical_state.py`, `test_outbox.py` |
| Reexecução sem duplicação | `test_ingestion.py`, `test_ingestion_workflow.py`, `test_review_workflow.py` |
| Histórico de workflows sem conteúdo sensível | `test_ingestion_workflow.py`, `test_review_workflow.py`, `test_slice5_exit.py` |
| Rota de modelos autorizada pela instalação | `test_model_access.py`, `test_adk_gateway.py`, `test_hindsight_settings.py`, `test_slice6_exit.py` |
| Ontologia e schemas sincronizados | `test_generation.py` e comando `ontology.generate --check` |

Os nomes acima referem-se a `pxos-kernel/tests/`, exceto os testes de saída da distribuição.
O mapeamento identifica evidências, não declara que requisitos pendentes foram entregues.

## Interpretação e manutenção

Cobertura é calculada por subpacote imediato do kernel, módulos da raiz agrupados em
serviços, distribuição e automação. Arquivos nunca importados entram no denominador.
Testes e migrações não têm limites de tamanho nem cobertura Python; testes continuam
sob lint, tipagem, complexidade e aninhamento. Docstrings e comentários não contam para
tamanho; SQL embutido conta. A lista de fontes é descoberta novamente em cada execução.

O verificador de fronteiras distingue `from pacote import função` de acesso a um módulo
interno com o mesmo nome. Essa desambiguação deriva dos imports e definições presentes
na API pública; não é uma lista de exceções para dívida existente.

Duplicação, nomes, coesão e abstrações exigem revisão qualitativa com arquivo, evidência,
impacto e ação sugerida. Não há nota agregada. Mudanças de política exigem uma alteração
dedicada e justificada, separada de funcionalidades.

Nesta entrega, por orientação do mantenedor, o trabalho se restringe à infraestrutura de
qualidade. Violações existentes no produto são reportadas e bloqueiam aprovação; sua
correção não faz parte desta alteração. Não reduzir limites para obter resultado verde.
