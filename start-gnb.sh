#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/config/open5gs-gnb.yaml"
BINARY="${SCRIPT_DIR}/build/nr-gnb"

if [[ ! -f "$BINARY" ]]; then
    echo "nr-gnb not found at $BINARY — run 'make' first" >&2
    exit 1
fi

exec "$BINARY" -c "$CONFIG"
