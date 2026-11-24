#!/usr/bin/env bash
cd "$(dirname "$0")"
VENV="${CAPRI_VENV:-$HOME/.capri/venv}"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo
    echo "[!] Capri isn't set up yet: $VENV is missing."
    echo "    Run: ./setup.sh"
    echo
    exit 1
fi

exec "$VENV/bin/python" -u -m capri "$@"
