#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Fehler: Python im venv nicht gefunden unter $PYTHON_BIN"
  echo "Bitte zuerst ein venv anlegen (z. B. mit: python3 -m venv venv)."
  exit 1
fi

echo "Installiere/aktualisiere Test-Abhängigkeiten ..."
"$PYTHON_BIN" -m pip install --quiet --disable-pip-version-check -r "$ROOT_DIR/requirements-dev.txt"

echo "Starte Test-Pipeline ..."
"$PYTHON_BIN" -m pytest -q "$ROOT_DIR/tests"
