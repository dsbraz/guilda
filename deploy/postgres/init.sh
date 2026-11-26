#!/usr/bin/env bash
# Primeira inicialização do Postgres de produção (roda só com o volume vazio).
# Cria um login por processo, cada um só com o que precisa:
# - pxos_app_login (membro de pxos_app) e pxos_publisher_login (membro de pxos_publisher)
#   conectam no banco pxos; as migrações do kernel dão as permissões aos grupos e tiram
#   CONNECT de PUBLIC, então só esses logins e o dono entram (PXOS-63);
# - temporal é dono dos bancos temporal e temporal_visibility;
# - hindsight é dono do banco hindsight, com pgvector.
# O dono (POSTGRES_USER) migra e faz o bootstrap; nunca é usado em tempo de execução.
set -euo pipefail

: "${GUILDA_DB_APP_PASSWORD:?}" "${GUILDA_DB_PUBLISHER_PASSWORD:?}"
: "${TEMPORAL_DB_PASSWORD:?}" "${HINDSIGHT_DB_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app="$GUILDA_DB_APP_PASSWORD" -v publisher="$GUILDA_DB_PUBLISHER_PASSWORD" \
  -v temporal="$TEMPORAL_DB_PASSWORD" -v hindsight="$HINDSIGHT_DB_PASSWORD" <<'SQL'
CREATE ROLE pxos_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE pxos_publisher NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE pxos_app_login LOGIN PASSWORD :'app' NOSUPERUSER NOBYPASSRLS IN ROLE pxos_app;
CREATE ROLE pxos_publisher_login LOGIN PASSWORD :'publisher'
  NOSUPERUSER NOBYPASSRLS IN ROLE pxos_publisher;
GRANT CONNECT ON DATABASE pxos TO pxos_app_login, pxos_publisher_login;

CREATE ROLE temporal LOGIN PASSWORD :'temporal';
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;

CREATE ROLE hindsight LOGIN PASSWORD :'hindsight';
CREATE DATABASE hindsight OWNER hindsight;
\connect hindsight
CREATE EXTENSION IF NOT EXISTS vector;
SQL
