from __future__ import annotations

import re
import sys
from datetime import (
    date,
    datetime,
    time
)
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    ClassVar,
    Literal,
    Optional,
    Union
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer
)


metamodel_version = "1.12.0"
version = "0.1.0"


class ConfiguredBaseModel(BaseModel):
    model_config = ConfigDict(
        serialize_by_alias = True,
        validate_by_name = True,
        validate_assignment = True,
        validate_default = True,
        extra = "forbid",
        arbitrary_types_allowed = True,
        use_enum_values = True,
        strict = False,
    )





class LinkMLMeta(RootModel):
    root: dict[str, Any] = {}
    model_config = ConfigDict(frozen=True)

    def __getattr__(self, key:str):
        return getattr(self.root, key)

    def __getitem__(self, key:str):
        return self.root[key]

    def __setitem__(self, key:str, value):
        self.root[key] = value

    def __contains__(self, key:str) -> bool:
        return key in self.root


linkml_meta = None

class ActorKind(str, Enum):
    pessoa_interna = "internal_person"
    pessoa_externa = "external_person"
    agente = "agent"


class AgentTool(str, Enum):
    consulta_à_memória_de_contexto = "recall"



class Actor(ConfiguredBaseModel):
    """
    Pessoa interna, pessoa externa ou agente.
    """
    name: str = Field(default=..., title="nome")
    actor_kind: ActorKind = Field(default=..., title="tipo de ator")


class AgentVersion(ConfiguredBaseModel):
    """
    Definição publicada de um agente e do seu ciclo. O modelo não faz parte dela: vem da rota da residência da instalação.
    """
    agent: str = Field(default=..., title="agente")
    instructions: str = Field(default=..., title="instruções", description="""O que o agente faz.""")
    memory_instructions: str = Field(default=..., title="instruções para a memória de contexto", description="""O que a memória de contexto extrai de cada fonte.""")
    tools: Optional[list[AgentTool]] = Field(default=None, title="ferramentas", description="""Ferramentas que o kernel monta para o agente.""")
    recall_tags: list[str] = Field(default=..., title="escopo da consulta à memória", description="""Tags de acesso que o kernel impõe à consulta e às retenções do ciclo; nunca vêm do modelo. A consulta só alcança o que foi retido com uma delas.""", min_length=1)
    schedule: str = Field(default=..., title="agenda", description="""Quando o ciclo começa, em cron de cinco campos.""")
    answer_deadline_hours: int = Field(default=..., title="prazo das respostas em horas", ge=1)
    max_rounds: int = Field(default=..., title="máximo de rodadas", ge=1)
    thread: str = Field(default=..., title="thread", description="""Onde o agente pergunta e entrega.""")
    deliverable: str = Field(default=..., title="entrega", description="""Ação que propõe a entrega do ciclo, como pacote.ação.""")

    @field_validator('schedule')
    def pattern_schedule(cls, v):
        pattern=re.compile(r"^\S+( \S+){4}$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.search(element):
                    err_msg = f"Invalid schedule format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.search(v):
            err_msg = f"Invalid schedule format: {v}"
            raise ValueError(err_msg)
        return v

    @field_validator('deliverable')
    def pattern_deliverable(cls, v):
        pattern=re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.search(element):
                    err_msg = f"Invalid deliverable format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.search(v):
            err_msg = f"Invalid deliverable format: {v}"
            raise ValueError(err_msg)
        return v


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
Actor.model_rebuild()
AgentVersion.model_rebuild()
