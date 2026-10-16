#!/usr/bin/env bash
# Run from repo root: bash infra/dev/bootstrap-local.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
file=infra/dev/.env.local
if [[ ! -f "$file" ]]; then
  echo "Create $file from infra/dev/local.env.example and replace ALL CHANGE_TO placeholders." >&2
  exit 1
fi
if grep -Eq 'CHANGE_TO|^\s*(ROBO_DEV_DB_PASSWORD|MINIO_ROOT_PASSWORD|ROBO_DEV_KC_PASSWORD)=$' "$file"; then
  echo "Refusing placeholder/empty local credentials." >&2; exit 1
fi
set -a
# Only source your own trusted local env; never use user-supplied untrusted env files.
source "$file"
set +a
for key in ROBO_DEV_DB_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD ROBO_DEV_KC_ADMIN ROBO_DEV_KC_PASSWORD ROBOport_SEED_CLIENT_A_PASSWORD ROBOport_SEED_CLIENT_B_PASSWORD ROBOport_SEED_ADMIN_PASSWORD; do
  if [[ -z "${!key:-}" ]]; then echo "Missing $key" >&2; exit 1; fi
done
umask 077
mkdir -p infra/dev/tls
if [[ ! -f infra/dev/tls/local.crt || ! -f infra/dev/tls/local.key ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3 \
    -keyout infra/dev/tls/local.key -out infra/dev/tls/local.crt \
    -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
fi
# Non-root Keycloak needs read permission on disposable dev key only.
chmod 644 infra/dev/tls/local.crt infra/dev/tls/local.key
docker compose --env-file "$file" -f infra/dev/compose.local.yml up -d --wait
export ROBOport_DATABASE_URL="postgresql+psycopg://roboport:${ROBO_DEV_DB_PASSWORD}@127.0.0.1:5432/roboport"
export ROBOport_SYNTHETIC_AUTH_API=1
python -m alembic -c apps/api/alembic.ini upgrade head
PYTHONPATH=apps/api python scripts/seed_synthetic_users.py --i-understand-dev-only
echo "Local infrastructure and synthetic identities ready."
echo "Start API: PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000"
echo "Start web separately: cd apps/web && npm ci && npm run dev"
echo "Real OIDC login: use the HTTPS-only workflow in .github/workflows/oidc-keycloak.yml."
