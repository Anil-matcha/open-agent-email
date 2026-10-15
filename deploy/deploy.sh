#!/usr/bin/env bash
# Sync the repository to a server and (re)start the production stack.
#   DEPLOY_HOST=ubuntu@<ip> DEPLOY_KEY=~/path/key.pem deploy/deploy.sh
# First run on a new host: create .env.prod on the server (see deploy/README.md), then run this.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${DEPLOY_HOST:?set DEPLOY_HOST, e.g. ubuntu@1.2.3.4}"
KEY="${DEPLOY_KEY:?set DEPLOY_KEY to the ssh private key}"
REMOTE_DIR="${REMOTE_DIR:-open-agent-email}"

rsync -az --delete \
  --exclude .git --exclude node_modules --exclude .next --exclude .venv --exclude '__pycache__' \
  --exclude .env --exclude .env.prod --exclude 'server/*.db' \
  -e "ssh -i $KEY" ./ "$HOST:$REMOTE_DIR/"
ssh -i "$KEY" "$HOST" "cd $REMOTE_DIR/deploy && \
  if sudo docker compose -f docker-compose.prod.yml --env-file ../.env.prod ps -q server 2>/dev/null | grep -q .; then \
    sudo COMPOSE_DIR=\$HOME/$REMOTE_DIR ./backup/oae-backup.sh; fi && \
  sudo docker compose -f docker-compose.prod.yml --env-file ../.env.prod up -d --build"
