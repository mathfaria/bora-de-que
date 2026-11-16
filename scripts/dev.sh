#!/usr/bin/env bash
# Sobe a demo localmente.
# Usa o DATABASE_URL do .env; se ele ainda for o exemplo (ou BOCADO_LOCAL_DB=1),
# usa o Postgres embutido (scripts/pg_local.py). Na primeira subida o app carrega os recortes salvos.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${BOCADO_LOCAL_DB:-0}" = "1" ] || [ ! -f .env ] || grep -q 'usuario:senha' .env; then
  export DATABASE_URL="$(.venv/bin/python scripts/pg_local.py)"
fi
exec .venv/bin/bocado serve --port "${PORT:-8000}" "$@"
