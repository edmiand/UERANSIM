#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/config/open5gs-ue.yaml"
BINARY="${SCRIPT_DIR}/build/nr-ue"

if [[ ! -f "$BINARY" ]]; then
    echo "nr-ue not found at $BINARY — run 'make' first" >&2
    exit 1
fi

exec sudo "$BINARY" -c "$CONFIG"
