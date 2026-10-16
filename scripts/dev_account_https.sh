#!/usr/bin/env bash
# Synthetic-only ROBOport account HTTPS smoke stand.
# Usage: ROBOport_DATABASE_URL='postgresql+psycopg://...@127.0.0.1:5432/...' \
#        ROBOport_DISPOSABLE_DB_ACK=1 bash scripts/dev_account_https.sh
set -euo pipefail
if [[ "${ROBOport_DISPOSABLE_DB_ACK:-}" != "1" ]]; then
  echo "Refusing: explicit disposable database acknowledgement required" >&2
  exit 2
fi
if [[ ! "${ROBOport_DATABASE_URL:-}" =~ ^postgresql(\+psycopg)?://[^/]*@127\.0\.0\.1:[0-9]+/ ]]; then
  echo "Refusing: only explicit loopback PostgreSQL DSN is permitted" >&2
  exit 2
fi
command -v openssl >/dev/null
tls_dir="$(mktemp -d)"
chmod 700 "$tls_dir"
api_pid=""
web_pid=""
cleanup() {
  if [[ -n "$web_pid" ]]; then kill "$web_pid" 2>/dev/null || true; fi
  if [[ -n "$api_pid" ]]; then kill "$api_pid" 2>/dev/null || true; fi
  wait 2>/dev/null || true
  rm -rf "$tls_dir"
}
trap cleanup EXIT INT TERM
openssl req -x509 -newkey rsa:3072 -nodes -days 1 \
  -keyout "$tls_dir/key.pem" -out "$tls_dir/cert.pem" \
  -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  >/dev/null 2>&1
chmod 600 "$tls_dir/key.pem"
export ROBOport_SYNTHETIC_AUTH_API=1
export ROBOport_SYNTHETIC_ACCESS_API=1
export ROBOport_DEV_TLS_CERT="$tls_dir/cert.pem"
export ROBOport_DEV_TLS_KEY="$tls_dir/key.pem"
PYTHONPATH=apps/api python -m uvicorn roboport_api.main:app \
  --host 127.0.0.1 --port 8000 \
  --ssl-keyfile "$ROBOport_DEV_TLS_KEY" --ssl-certfile "$ROBOport_DEV_TLS_CERT" &
api_pid=$!
(cd apps/web && npm run dev -- --host 127.0.0.1 --port 5173) &
web_pid=$!
echo 'Local TLS stand: https://127.0.0.1:5173/account/login'
echo 'Self-signed certificate: trust manually for this disposable session only.'
wait -n "$api_pid" "$web_pid"
