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


class ReportCategory(str, Enum):
    progresso = "progress"
    risco_de_atraso = "delay_risk"
    atraso_confirmado = "confirmed_delay"
    prazo_renegociado = "renegotiated_deadline"
    impedimento = "impediment"
    decisão = "decision"
    mudança = "change"



class Actor(ConfiguredBaseModel):
    """
    Pessoa interna, pessoa externa ou agente.
    """
    name: str = Field(default=..., title="nome")
    actor_kind: ActorKind = Field(default=..., title="tipo de ator")


class WeeklyReport(ConfiguredBaseModel):
    summary: str = Field(default=..., title="resumo")
    items: list[ReportItem] = Field(default=..., title="itens")
    uncertainties: Optional[list[str]] = Field(default=None, title="incertezas", description="""O que o agente não conseguiu confirmar.""")
    unavailable_sources: Optional[list[str]] = Field(default=None, title="fontes indisponíveis", description="""Fontes que o ciclo não conseguiu ler.""")


class ReportItem(ConfiguredBaseModel):
    category: ReportCategory = Field(default=..., title="categoria")
    text: str = Field(default=..., title="texto")
    sources: list[str] = Field(default=..., title="fontes", min_length=1)


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
Actor.model_rebuild()
WeeklyReport.model_rebuild()
ReportItem.model_rebuild()
