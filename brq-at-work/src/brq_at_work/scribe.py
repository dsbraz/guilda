"""Post-meeting scribe (PXOS-56): what BRQ@work asks the context memory to extract from a
transcript, and how it reads each extracted fact back into a commitment candidate.

The kernel checks the quote against the transcript and the payload against the package
schema; this module parses, and wires the scribe's Activities for a Temporal worker
(PXOS-71).
"""

import re
from collections.abc import Callable
from typing import Any

import asyncpg

from pxos_kernel.context import ContextStore
from pxos_kernel.ingestion import Candidate, Connector
from pxos_kernel.ontology import Registry
from pxos_kernel.workflows import IngestionActivities, ReviewActivities

SCRIBE = "scribe-agent"
PACKAGE = "work_context"

INSTRUCTIONS = """\
Você lê transcrições de reuniões de projeto. Além dos fatos de contexto, registre cada
compromisso assumido em voz alta, um por fato, exatamente neste formato, em uma linha:

COMPROMISSO | dono=<id da pessoa> | prazo=<AAAA-MM-DD> | frente=<id da frente> | \
descrição=<o que será entregue> | trecho="<frase literal da transcrição>"

O trecho é a fala literal, copiada caractere por caractere da transcrição, incluindo o nome
de quem fala. Não resuma nem corrija o trecho. Sem fala literal, não registre o compromisso.
"""

_FACT = re.compile(
    r"COMPROMISSO \| dono=(?P<owner>[^|\s]+) \| prazo=(?P<due>[^|\s]+)"
    r" \| frente=(?P<stream>[^|\s]+) \| descrição=(?P<description>[^|]+?)"
    r' \| trecho="(?P<quote>[^"]+)"'
)


def read_commitment(fact: str) -> Candidate | None:
    match = _FACT.search(fact)
    if match is None:
        return None
    return Candidate(
        action="create_commitment",
        payload={
            "description": match["description"].strip(),
            "owner": match["owner"],
            "due_date": match["due"],
            "work_stream": match["stream"],
        },
        quote=match["quote"],
    )


def scribe_activities(
    pool: asyncpg.Pool, registry: Registry, store: ContextStore, connector: Connector
) -> list[Callable[..., Any]]:
    """The Activities a worker runs for the scribe: it ingests and reads decisions as itself."""
    ingestion = IngestionActivities(
        pool,
        registry,
        store,
        connector,
        package=PACKAGE,
        instructions=INSTRUCTIONS,
        reader=read_commitment,
        actor=SCRIBE,
    )
    return ingestion.registered() + ReviewActivities(pool, actor=SCRIBE).registered()
