#!/usr/bin/env bash
# Start the flashcard app. First run creates a virtualenv and installs deps.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Setting up .venv ..."
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv >/dev/null
    VIRTUAL_ENV=.venv uv pip install -r requirements.txt >/dev/null
  else
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip >/dev/null
    ./.venv/bin/pip install -r requirements.txt >/dev/null
  fi
fi

if [ -f .env ]; then set -a; . ./.env; set +a; fi

exec ./.venv/bin/python -m flashcard serve --open "$@"
