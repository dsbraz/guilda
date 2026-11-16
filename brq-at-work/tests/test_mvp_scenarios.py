"""Acceptance scenarios of the MVP (PRD v0.27, §MVP): the weekly project follow-up agent.

None is delivered yet. Each stays a strict expected failure until the slice that delivers
it replaces it with the real test, run with fakes for SharePoint, Granola and Teams.
"""

import pytest


def pending(slice_: str, scope: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(raises=NotImplementedError, strict=True, reason=f"{slice_}: {scope}")


@pending("fatia 10", "fontes novas ou alteradas atualizam o contexto, sem duplicar")
def test_update_without_duplication() -> None:
    raise NotImplementedError


@pending("fatia 11", "lacuna vira pergunta na thread; a resposta entra na entrega")
def test_clarification() -> None:
    raise NotImplementedError


@pending("fatia 11", "a correção desta semana vale no ciclo seguinte")
def test_continuity() -> None:
    raise NotImplementedError


@pending("fatia 10", "indisponibilidade sinalizada; exclusão e perda de acesso respeitadas")
def test_sources_and_access() -> None:
    raise NotImplementedError


@pending("fatia 13", "reinício durante a espera preserva respostas e não duplica entregas")
def test_resumption() -> None:
    raise NotImplementedError


@pending("fatia 11", "o PM revisa o status antes de compartilhar com o cliente")
def test_pm_reviews_the_deliverable() -> None:
    raise NotImplementedError


@pending("fatia 13", "cada execução registra uso e custo para medir o esforço total")
def test_pm_total_effort_is_measurable() -> None:
    raise NotImplementedError
