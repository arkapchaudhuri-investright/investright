#!/usr/bin/env bash
# InvestRight deploy — pull the latest main and restart the service.
#
# Secrets & data are NOT in git: .env (Gemini key) and data/ (the SQLite DB)
# are .gitignore'd and live only on the VM, so `git pull` never touches them.
# Run this on the VM from /opt/investright, as the `ubuntu` user:
#     cd /opt/investright && ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

echo "==> git pull (ff-only)"
git pull --ff-only origin main

echo "==> pip install -r requirements.txt (no-op unless deps changed)"
.venv/bin/pip install -q -r requirements.txt

echo "==> restart investright"
sudo systemctl restart investright
sleep 3

echo "==> health check"
if systemctl is-active --quiet investright \
   && curl -sf -o /dev/null -w "localhost:8700 -> HTTP %{http_code}\n" http://127.0.0.1:8700/; then
  echo "==> deployed OK @ $(git rev-parse --short HEAD)"
else
  echo "!! HEALTHCHECK FAILED — check: journalctl -u investright -n50"
  exit 1
fi
