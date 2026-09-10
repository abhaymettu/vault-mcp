#!/usr/bin/env bash
# One-command setup: venv, deps, tests, usage line. Safe to rerun.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "need Python 3.11+, got $("$PY" --version)"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m pytest -q
echo
echo "usage: $PWD/.venv/bin/python $PWD/server.py /path/to/vault"
