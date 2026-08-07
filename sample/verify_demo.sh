#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

docker compose exec -T postgres \
  psql -U postgres -d governance_demo \
  -v ON_ERROR_STOP=1 \
  -f - < sample/verify_demo.sql

echo "PostgreSQL demo verification succeeded."
