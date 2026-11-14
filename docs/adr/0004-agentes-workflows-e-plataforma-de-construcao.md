# ADR 0004 — Agentes, workflows e plataforma de construção

**Status:** aceito em 14/11/2026. Substitui a nota sobre motores na seção "Fora deste ADR" do
ADR 0003. Responde ao PRD v0.26 (§5 Agentes, Gatilhos e fluxos; seção "Construção e
acompanhamento na plataforma").

## Contexto

O PRD previa agentes em ADK e "um único motor de fluxos (Cloud Workflows ou Temporal)", com
definições de agentes no Git. O spike ADK + Temporal
([relatório](../../spikes/adk-temporal/reports/ADR-001.md)) passou 13 testes, com Gemini real,
retomada de worker e execução incerta. O relatório recomendou manter a combinação como
candidata; ele não aprovava infraestrutura de produto e listou cinco gates: integração ao
kernel real, contrato do portal, qualidade, implantação e revogação de aprovação. O mantenedor
decidiu adotar os dois motores e fazer da plataforma o lugar de construção e operação.

## Decisão

### 1. Motores

- **Google ADK** executa agentes: interpretação, ciclo modelo/ferramenta, sessão.
- **Temporal** é o único motor de processos duráveis: etapas, prazos, espera, retomada e
  agendas (Schedules). Quando um agente participa de um processo, a chamada ADK roda numa
  Activity.
- O ciclo de aprovação não é modelado nos dois motores, e não há motor próprio.
- Identidade, evidência, aprovação e autorização continuam no kernel. Toda escrita externa
  passa pelo gateway de ação (K32).

### 2. Regras de integração com o kernel

1. **Modelos:** as chamadas de modelo do ADK passam pelo acesso a modelos (K11–K14), como
   qualquer outra; o ADK não chama o provedor diretamente.
2. **Só referências no workflow:** entradas e saídas de Activities carregam ids (proposta,
   fonte, entidade, versão), nunca conteúdo ingerido ou dados pessoais. O histórico do
   Temporal não se edita; assim a exclusão do K10 continua efetiva. O mesmo vale para as
   sessões do ADK que forem persistidas.
3. **Isolamento:** cada instalação tem namespace Temporal e credenciais de worker próprios
   (req. 4).
4. **Identidade:** o worker abre a sessão no banco como o agente, agindo pela pessoa,
   pela delegação do K09. O histórico do motor não é fonte de autorização.
5. **Aprovação:** a decisão é registrada no kernel (K24); o evento da outbox chega ao
   workflow como sinal. Expiração e revogação são conferidas no kernel antes de executar.
6. **Resultado incerto:** repetição só conforme o contrato do destino (idempotência,
   versão do recurso, comprovante). Sem confirmação, o resultado fica incerto e vai para
   reconciliação; nunca vira sucesso.

### 3. Plataforma de construção

A área web do pxOS é o lugar único para configurar a instalação, construir agentes, compor
workflows, publicar e acompanhar execuções e aprovações. É kernel pela regra do ADR 0002:
não mudaria se o cliente fosse outro.

| Na plataforma (configuração) | Em código, registrado na plataforma |
|---|---|
| Conectores já existentes: credencial, escopo, mapeamento, estado | Código de conectores e ferramentas, com testes (FDE) |
| Papéis, aprovadores e fases de autonomia | Ontologia em pacotes LinkML, por pull request |
| Agentes e workflows | — |
| Execuções e aprovações | — |

- **Publicação governada:** toda mudança feita na plataforma vira uma versão publicada,
  imutável, com digest, aprovada por um papel, pelo mesmo ciclo de proposta e aprovação do
  kernel. Rascunho não afeta o publicado; cada execução registra a versão que rodou.
- **Fonte de verdade:** as definições de agentes e workflows vivem no estado da plataforma,
  não no Git. Exportação serve como cópia.
- **Agentes desde o primeiro:** a definição do primeiro agente de produto (K34) já nasce
  como versão publicada no banco. Antes da interface, publica-se pela API ou pelo MCP; o
  formulário usa a mesma API depois.
- **Ontologia fora da interface:** mudar um tipo regenera schemas, tools e permissões e
  invalida propostas abertas. Editar a ontologia pela interface fica fora do escopo atual e
  do marco do kernel.
- **Ordem de construção:** (1) aprovações e execuções; (2) agentes por formulário, com
  teste e publicação; (3) workflows por formulário ou modelo, com composição visual só se
  a necessidade aparecer; (4) configuração de conectores e da instalação.

## Consequências

- O K34 deixa de prever definições no Git e Agent Engine como padrão; o K36 deixa de
  decidir o motor. Itens novos cobrem a área web, a publicação governada, a composição de
  workflows e a configuração da instalação (K65–K68 no backlog, PXOS-65 a 68).
- Hospedagem do Temporal e dos workers, disponibilidade, residência, custo, retenção do
  histórico e atualização compatível com processos em andamento são gates de produção
  (K44, Fase 10). A escolha dos motores não define o provedor de hospedagem.
- A biblioteca de interface é escolhida no primeiro item da área web.
- As versões usadas no spike são evidência, não política de versões de produção.
