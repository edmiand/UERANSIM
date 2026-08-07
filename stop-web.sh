#!/bin/bash
set -e

SERVICE="ueransim-web.service"

if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    echo "Stopping $SERVICE via systemd..."
    sudo systemctl stop "$SERVICE"
    exit 0
fi

PIDS=$(pgrep -f "python3 .*ueransim-web\.py" || true)
if [[ -z "$PIDS" ]]; then
    echo "ueransim-web is not running"
    exit 0
fi

echo "Stopping ueransim-web (pid: $PIDS)..."
kill $PIDS
