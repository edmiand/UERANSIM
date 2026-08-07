#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/ueransim-web.py"

if [[ ! -f "$SCRIPT" ]]; then
    echo "ueransim-web.py not found at $SCRIPT" >&2
    exit 1
fi

if ! python3 -c "import aiohttp" 2>/dev/null; then
    echo "aiohttp not installed — run 'pip3 install --user --break-system-packages aiohttp' first" >&2
    exit 1
fi

exec python3 "$SCRIPT" "$@"
