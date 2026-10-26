#!/usr/bin/env bash
# One local HTTPS origin for web + API; run after bootstrap-local.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ ! -f infra/dev/.env.local || ! -f infra/dev/tls/local.crt || ! -f infra/dev/tls/local.key ]]; then
  echo "Run bash infra/dev/bootstrap-local.sh first" >&2
  exit 1
fi
set -a
source infra/dev/.env.local
set +a
export ROBOport_DATABASE_URL="postgresql+psycopg://roboport:${ROBO_DEV_DB_PASSWORD}@127.0.0.1:5432/roboport"
export ROBOport_SYNTHETIC_AUTH_API=1 ROBOport_SYNTHETIC_ACCESS_API=1
export ROBOport_OIDC_ENABLED=1
export ROBOport_OIDC_ISSUER=https://localhost:9443/realms/roboport-demo
export ROBOport_OIDC_AUTHORIZE="${ROBOport_OIDC_ISSUER}/protocol/openid-connect/auth"
export ROBOport_OIDC_TOKEN="${ROBOport_OIDC_ISSUER}/protocol/openid-connect/token"
export ROBOport_OIDC_JWKS="${ROBOport_OIDC_ISSUER}/protocol/openid-connect/certs"
export ROBOport_OIDC_CLIENT=roboport-dev
export ROBOport_OIDC_REDIRECT=https://127.0.0.1:8444/api/v1/dev/oidc/callback
export ROBOport_OIDC_STATE_KEY="${ROBO_DEV_OIDC_STATE_KEY:?Set unique ROBO_DEV_OIDC_STATE_KEY in .env.local}"
if [[ ${#ROBOport_OIDC_STATE_KEY} -lt 32 ]]; then echo "OIDC state key too short" >&2; exit 1; fi
export SSL_CERT_FILE="$(pwd)/infra/dev/tls/local.crt"
export ROBO_DEV_HTTPS=1
if [[ ! -d apps/web/node_modules ]]; then (cd apps/web && npm ci); fi
PYTHONPATH=apps/api python -m uvicorn roboport_api.main:app --host 127.0.0.1 --port 8445 \
  --ssl-keyfile infra/dev/tls/local.key --ssl-certfile infra/dev/tls/local.crt &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM
(cd apps/web && npm run dev)
