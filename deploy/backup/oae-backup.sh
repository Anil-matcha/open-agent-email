#!/usr/bin/env bash
# Consistent backup of the SQLite database and the generated secrets for the production stack.
# Uses SQLite's online backup API inside the running container, verifies the copy, keeps
# KEEP_DAYS of history (never fewer than KEEP_MIN), and saves secrets.env when it changes.
#
# Root cron, daily:
#   20 3 * * * COMPOSE_DIR=/home/ubuntu/open-agent-email /home/ubuntu/open-agent-email/deploy/backup/oae-backup.sh >> /var/log/oae-backup.log 2>&1
#
# On-box snapshots protect against corruption and bad upgrades, not against losing the machine:
# copy BACKUP_DIR off the host as well. Keep secrets.env: without its encryption key, stored
# relay passwords, webhook secrets and domain signing keys cannot be read.
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-$HOME/open-agent-email}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/open-agent-email}"
KEEP_DAYS="${KEEP_DAYS:-14}"
KEEP_MIN="${KEEP_MIN:-3}"
DB_PATH_IN_CONTAINER="${DB_PATH_IN_CONTAINER:-/data/open_agent_email.db}"
SECRETS_IN_CONTAINER="${SECRETS_IN_CONTAINER:-/data/secrets.env}"
SERVICE="${SERVICE:-server}"

umask 077
cd "$COMPOSE_DIR/deploy"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
compose=(docker compose -f docker-compose.prod.yml --env-file ../.env.prod)
tmp_in_container="/tmp/oae-backup-$ts.db"
out="$BACKUP_DIR/oae-$ts.db"

echo "[$ts] snapshot start"
"${compose[@]}" exec -T "$SERVICE" python - "$DB_PATH_IN_CONTAINER" "$tmp_in_container" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
PY
container_id="$("${compose[@]}" ps -q "$SERVICE")"
docker cp "$container_id:$tmp_in_container" "$out"
"${compose[@]}" exec -T "$SERVICE" rm -f "$tmp_in_container"

python3 - "$out" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
result = db.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    raise SystemExit(f"integrity_check failed: {result}")
tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
counts = {t: db.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
print("verified:", ", ".join(f"{t}={n}" for t, n in sorted(counts.items()) if n) or "empty database")
PY
gzip -f "$out"
echo "[$ts] wrote $(basename "$out").gz ($(du -h "$out.gz" | cut -f1))"

# Secrets: copy only when they changed
if "${compose[@]}" exec -T "$SERVICE" test -f "$SECRETS_IN_CONTAINER"; then
  "${compose[@]}" exec -T "$SERVICE" cat "$SECRETS_IN_CONTAINER" > "$BACKUP_DIR/secrets.new"
  latest="$(ls -1t "$BACKUP_DIR"/secrets.env.* 2>/dev/null | head -n1 || true)"
  if [ -z "$latest" ] || ! cmp -s "$BACKUP_DIR/secrets.new" "$latest"; then
    mv "$BACKUP_DIR/secrets.new" "$BACKUP_DIR/secrets.env.$ts"
    echo "[$ts] secrets changed, saved secrets.env.$ts"
  else
    rm -f "$BACKUP_DIR/secrets.new"
  fi
fi

mapfile -t snaps < <(ls -1t "$BACKUP_DIR"/oae-*.db.gz 2>/dev/null)
if [ "${#snaps[@]}" -gt "$KEEP_MIN" ]; then
  for f in "${snaps[@]:$KEEP_MIN}"; do
    if [ -n "$(find "$f" -mtime +"$KEEP_DAYS" -print 2>/dev/null)" ]; then
      rm -f "$f"
      echo "[$ts] pruned $(basename "$f")"
    fi
  done
fi
echo "[$ts] done: ${#snaps[@]} snapshot(s) kept"
