"""Contrato de domínio que o FDE fornece; sem dependência do motor de workflows."""

import hashlib
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Move(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    person_id: str
    source_project: str
    target_project: str
    percentage: int = Field(strict=True, ge=1, le=100)
    start_date: date
    expected_version: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def different_projects(self):
        if self.source_project == self.target_project:
            raise ValueError("origem e destino devem ser diferentes")
        return self


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["proposed", "needs_clarification"]
    move: Move | None = None
    question: str = ""
    evidence: str

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "proposed" and self.move is None:
            raise ValueError("proposta exige movimentação")
        if self.status == "needs_clarification" and (self.move is not None or not self.question):
            raise ValueError("esclarecimento exige pergunta e nenhuma movimentação")
        return self


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
