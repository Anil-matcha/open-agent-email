#!/usr/bin/env bash
# One-command local start: creates a virtualenv, generates the required secrets
# on first run (kept in server/.env, which is git-ignored), and starts the server.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/server"

if [ ! -d .venv ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv -q --python ">=3.11,<3.14" .venv
  else
    PY=""
    for c in python3.13 python3.12 python3.11 python3; do
      if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    done
    [ -n "$PY" ] || { echo "Python 3.11 or newer is required." >&2; exit 1; }
    "$PY" -m venv .venv
  fi
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install -q -p .venv/bin/python -r requirements.txt
else
  .venv/bin/python -m pip install -q -r requirements.txt
fi

if [ ! -f .env ] || ! grep -q '^ADMIN_API_KEY=' .env; then
  ADMIN_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  ENC_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  {
    echo "ADMIN_API_KEY=$ADMIN_KEY"
    echo "SMTP_CONFIG_ENCRYPTION_KEY=$ENC_KEY"
  } >> .env
  chmod 600 .env
  echo "Generated secrets in server/.env. Back this file up: the encryption key protects stored credentials."
fi

echo
echo "Admin key:  $(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)"
echo "API:        http://127.0.0.1:8000   (docs at /docs)"
echo "Quickstart: docs/quickstart.md"
echo
exec .venv/bin/python run.py
