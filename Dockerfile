# Imagem do app: kernel, distribuição e adaptadores, sem dependências de desenvolvimento.
# O processo roda como usuário sem privilégios. Uso: deploy/compose.yaml.
FROM ghcr.io/astral-sh/uv:0.13.8 AS uv

FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY guilda-kernel guilda-kernel
COPY brq-at-work brq-at-work
COPY connectors connectors
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
# /var/lib/pxos é do usuário do app, para que o volume montado ali herde o dono.
RUN useradd --system --uid 10001 --no-create-home pxos \
    && mkdir -p /var/lib/pxos && chown pxos /var/lib/pxos
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER pxos
EXPOSE 8000
CMD ["python", "-m", "brq_at_work.serve"]
