#!/usr/bin/env bash
# Arranque en un solo paso (Linux / macOS): entorno, dependencias, modelos, datos y servidor.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  echo "▸ Creando entorno virtual…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

[ -f .env ] || { cp .env.example .env; echo "▸ Creé .env desde .env.example: completá ANTHROPIC_API_KEY y FISH_API_KEY."; }

python scripts/download_models.py
[ -f data/wecare.db ] || python scripts/seed_demo.py

HOST=${HOST:-127.0.0.1}; PORT=${PORT:-8000}
echo "▸ Tótem:  http://$HOST:$PORT/"
echo "▸ Panel:  http://$HOST:$PORT/admin"
echo "▸ Kiosco: chrome --kiosk --use-fake-ui-for-media-stream http://$HOST:$PORT/"
exec uvicorn wecare.main:app --host "$HOST" --port "$PORT"
