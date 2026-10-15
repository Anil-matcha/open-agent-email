#!/bin/sh
# Generates ADMIN_API_KEY and SMTP_CONFIG_ENCRYPTION_KEY once and keeps them in the
# data volume. Values supplied in the environment always win and are never written.
# Existing values in the volume are never regenerated or overwritten.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
SECRETS_FILE="$DATA_DIR/secrets.env"
mkdir -p "$DATA_DIR"
touch "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"

stored() { grep "^$1=" "$SECRETS_FILE" | head -n 1 | cut -d= -f2- || true; }

NEW_ADMIN=""
if [ -z "${ADMIN_API_KEY:-}" ]; then
  ADMIN_API_KEY="$(stored ADMIN_API_KEY)"
  if [ -z "$ADMIN_API_KEY" ]; then
    ADMIN_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    echo "ADMIN_API_KEY=$ADMIN_API_KEY" >> "$SECRETS_FILE"
    NEW_ADMIN="$ADMIN_API_KEY"
  fi
fi

if [ -z "${SMTP_CONFIG_ENCRYPTION_KEY:-}" ]; then
  SMTP_CONFIG_ENCRYPTION_KEY="$(stored SMTP_CONFIG_ENCRYPTION_KEY)"
  if [ -z "$SMTP_CONFIG_ENCRYPTION_KEY" ]; then
    SMTP_CONFIG_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    echo "SMTP_CONFIG_ENCRYPTION_KEY=$SMTP_CONFIG_ENCRYPTION_KEY" >> "$SECRETS_FILE"
  fi
fi

export ADMIN_API_KEY SMTP_CONFIG_ENCRYPTION_KEY

if [ -n "$NEW_ADMIN" ]; then
  echo "=================================================================="
  echo "First start: generated an admin key (shown only this once)."
  echo "Admin key: $NEW_ADMIN"
  echo "Sign in to the dashboard at http://localhost:3000 with this key."
  echo "Both secrets are stored in the data volume at $SECRETS_FILE."
  echo "Back up that volume together with the database."
  echo "=================================================================="
fi

exec "$@"
