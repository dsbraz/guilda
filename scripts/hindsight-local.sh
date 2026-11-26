#!/usr/bin/env bash
# Verifica a instalação de memória compartilhada pelo usuário desta estação.
set -euo pipefail
systemctl --user is-active --quiet hindsight.service
curl -fsS http://127.0.0.1:8888/health
