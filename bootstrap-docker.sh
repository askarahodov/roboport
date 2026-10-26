#!/usr/bin/env bash
# Run from repo root: bash bootstrap-docker.sh
set -euo pipefail
cd "$(dirname "$0")"

file=.env
if [[ ! -f "$file" ]]; then
  echo "Create $file from .env.example and replace ALL CHANGE_TO placeholders." >&2
  exit 1
fi
if grep -Eq 'CHANGE_TO|^\s*(ROBO_DEV_DB_PASSWORD|MINIO_ROOT_PASSWORD|ROBO_DEV_KC_PASSWORD)=$' "$file"; then
  echo "Refusing placeholder/empty local credentials." >&2; exit 1
fi

set -a
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
chmod 644 infra/dev/tls/local.crt infra/dev/tls/local.key

echo "Starting infrastructure and applications..."
docker compose --env-file "$file" up -d --wait

echo "Running database migrations..."
docker compose exec -T api alembic -c alembic.ini upgrade head

echo "Seeding synthetic users..."
docker compose exec -T api python scripts/seed_synthetic_users.py --i-understand-dev-only

echo ""
echo "✅ ROBOport is running in Docker!"
echo ""
echo "Services:"
echo "  Web UI:      http://localhost:3000"
echo "  API:         http://localhost:8000"
echo "  API Health:  http://localhost:8000/health/live"
echo "  Keycloak:    https://localhost:9443"
echo "  MinIO Console: http://localhost:9001"
echo ""
echo "Test accounts (passwords from .env):"
echo "  Client A:    client-a@example.com"
echo "  Client B:    client-b@example.com"
echo "  Admin:       admin@example.com"