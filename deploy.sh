#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "[deploy] Starting deployment for web-diagnostics-orchestration"

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    echo "[deploy] uv already installed"
    return 0
  fi

  echo "[deploy] uv not found, installing..."
  # Install uv to ~/.local/bin (no sudo, non-interactive)
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # Ensure current shell can see uv
  export PATH="$HOME/.local/bin:$PATH"

  if ! command -v uv >/dev/null 2>&1; then
    echo "[deploy] uv installation failed or not on PATH" >&2
    exit 1
  fi
}

install_dependencies() {
  echo "[deploy] Installing dependencies with uv sync"
  ensure_uv
  uv sync
}

initialize_app() {
  echo "[deploy] Initialization step (placeholder) - add migrations or other setup here if needed"
}

start_server() {
  echo "[deploy] Starting FastAPI server in background on port 8080"

  # Use uv to run uvicorn, detached in the background
  # Logs are written to server.log in the project root
  nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --log-level info > server.log 2>&1 &

  echo "[deploy] Server started (PID: $!)"
}

install_dependencies
initialize_app
start_server

echo "[deploy] Deployment script completed"


