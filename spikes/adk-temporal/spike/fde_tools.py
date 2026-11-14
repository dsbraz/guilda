"""Exemplo de ferramentas de domínio implementadas pelo FDE."""


def tool_registry(context: dict, used: list):
    def get_work_context() -> dict:
        """Consulta pessoas, projetos e alocações da instalação de teste."""
        used.append("get_work_context")
        return context

    def get_capacity(project_id: str) -> dict:
        """Consulta a capacidade disponível de um projeto, em percentual."""
        used.append("get_capacity")
        return {"project_id": project_id, "available": context["capacity"][project_id]}

    return {"get_work_context": get_work_context, "get_capacity": get_capacity}
