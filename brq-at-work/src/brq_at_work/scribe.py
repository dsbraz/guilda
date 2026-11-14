"""Post-meeting scribe (PXOS-56, PXOS-80): the draft of its definition, and the Activities
a Temporal worker runs for it (PXOS-71).

The definition that runs is the version approved in the installation's database, never
this module: ``DRAFT`` is only what someone proposes as a first version (ADR 0004 §3). The
context memory extracts with its memory instructions; the kernel's ADK step reads each
fact with its instructions, and checks the quote against the transcript and the payload
against the package schema.
"""

from collections.abc import Callable
from typing import Any

import asyncpg

from pxos_kernel.context import ContextStore
from pxos_kernel.ingestion import Connector
from pxos_kernel.model_access import ModelGateway
from pxos_kernel.ontology import Registry
from pxos_kernel.workflows import IngestionActivities, ReviewActivities

SCRIBE = "scribe-agent"

MEMORY_INSTRUCTIONS = """\
Você lê transcrições de reuniões de projeto. Além dos fatos de contexto, registre cada
compromisso assumido em voz alta, um por fato, exatamente neste formato, em uma linha:

COMPROMISSO | dono=<id da pessoa> | prazo=<AAAA-MM-DD> | frente=<id da frente> | \
descrição=<o que será entregue> | trecho="<frase literal da transcrição>"

O trecho é a fala literal, copiada caractere por caractere da transcrição, incluindo o nome
de quem fala. Não resuma nem corrija o trecho. Sem fala literal, não registre o compromisso.
"""

INSTRUCTIONS = """\
Você recebe um fato extraído de uma transcrição de reunião. Se ele registra um compromisso
(uma linha COMPROMISSO), responda com o payload: description (o que será entregue), owner
(o id da pessoa), due_date (AAAA-MM-DD) e work_stream (o id da frente); e com quote, o
trecho entre aspas, copiado sem mudar nenhum caractere. Se o fato não é um compromisso, ou
não traz o trecho, responda com payload e quote nulos.
"""

DRAFT: dict[str, str] = {
    "agent": SCRIBE,
    "instructions": INSTRUCTIONS,
    "memory_instructions": MEMORY_INSTRUCTIONS,
    "proposes": "work_context.create_commitment",
}


def scribe_activities(
    pool: asyncpg.Pool,
    registry: Registry,
    store: ContextStore,
    connector: Connector,
    gateway: ModelGateway,
) -> list[Callable[..., Any]]:
    """The Activities a worker runs for the scribe: it ingests and reads decisions as itself."""
    ingestion = IngestionActivities(pool, registry, store, connector, gateway, agent=SCRIBE)
    return ingestion.registered() + ReviewActivities(pool, actor=SCRIBE).registered()
