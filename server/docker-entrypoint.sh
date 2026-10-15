#!/bin/sh
# Generates ADMIN_API_KEY and SMTP_CONFIG_ENCRYPTION_KEY once and keeps them in the
# data volume. Values supplied in the environment always win and are never written.
# Existing values in the volume are never regenerated or overwritten.
# An optional legacy SQLite file (from the old bind-mounted layout) is copied into the
# volume once, and only when the volume has no database yet.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
SECRETS_FILE="$DATA_DIR/secrets.env"
mkdir -p "$DATA_DIR"
touch "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"

DB_FILE="$DATA_DIR/open_agent_email.db"
LEGACY_DB="${OAE_LEGACY_DB_PATH:-/legacy/open_agent_email.db}"
if [ -f "$LEGACY_DB" ]; then
  if [ -e "$DB_FILE" ]; then
    echo "Legacy database found at $LEGACY_DB but $DB_FILE already exists; leaving the volume untouched."
  else
    cp "$LEGACY_DB" "$DB_FILE.importing"
    for suffix in -wal -shm; do
      if [ -f "$LEGACY_DB$suffix" ]; then cp "$LEGACY_DB$suffix" "$DB_FILE.importing$suffix"; fi
    done
    for suffix in "" -wal -shm; do
      if [ -f "$DB_FILE.importing$suffix" ]; then mv "$DB_FILE.importing$suffix" "$DB_FILE$suffix"; fi
    done
    echo "Imported the legacy database from $LEGACY_DB into $DB_FILE (the original file was not modified)."
  fi
fi

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
