#!/usr/bin/env bash
# Hindsight local para a execução real e opt-in da fatia 2 (PXOS-53, PXOS-56).
# Banco: o Postgres do Compose (pgvector). Modelo e embeddings: Vertex no projeto do
# desenvolvedor. As credenciais vêm de GOOGLE_APPLICATION_CREDENTIALS; nada é gravado aqui.
# Modelo, embeddings e regiões vêm da rota da instalação (PXOS-74), definida no ambiente
# ou no .env da raiz (modelo em .env.example); nada de modelo ou região é fixo aqui.
# Uso: cp .env.example .env  # e ajuste
#      GOOGLE_APPLICATION_CREDENTIALS=... scripts/hindsight-local.sh
set -euo pipefail

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${GOOGLE_APPLICATION_CREDENTIALS:?defina GOOGLE_APPLICATION_CREDENTIALS}"
: "${PXOS_VERTEX_PROJECT:?defina PXOS_VERTEX_PROJECT}"
port="${PXOS_HINDSIGHT_PORT:-18888}"
db_port="${PXOS_DB_PORT:-25432}"

docker compose exec -T postgres psql -U pxos -d postgres -tc \
  "SELECT 1 FROM pg_database WHERE datname = 'hindsight_local'" | grep -q 1 ||
  docker compose exec -T postgres psql -U pxos -d postgres -c "CREATE DATABASE hindsight_local"
docker compose exec -T postgres psql -U pxos -d hindsight_local -qc \
  "CREATE EXTENSION IF NOT EXISTS vector"

export GOOGLE_CLOUD_PROJECT="$PXOS_VERTEX_PROJECT"
export HINDSIGHT_API_DATABASE_URL="postgresql://pxos:pxos-local@127.0.0.1:${db_port}/hindsight_local"
eval "$(uv run python -c 'from pxos_kernel.context.hindsight import main; main()')"
export HINDSIGHT_API_LLM_PROMPT_CACHE_ENABLED=false
export HINDSIGHT_API_EMBEDDINGS_GEMINI_OUTPUT_DIMENSIONALITY=768
export HINDSIGHT_API_RERANKER_PROVIDER=rrf
export HINDSIGHT_API_VECTOR_EXTENSION=pgvector
export HINDSIGHT_API_TEXT_SEARCH_EXTENSION=native
export HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE=portuguese
export HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION=false
# Traces de modelo guardam conteúdo restrito; ficam desligados (ADR 0001).
export HINDSIGHT_API_LLM_TRACE_ENABLED=false
export HINDSIGHT_API_OTEL_TRACES_ENABLED=false
export HINDSIGHT_API_MCP_ENABLED=false

exec uvx --python 3.12 --from hindsight-api-slim==1.2.1 \
  uvicorn hindsight_api.server:app --host 127.0.0.1 --port "$port"
