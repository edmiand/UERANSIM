#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/ueransim-web.py"
SERVICE="ueransim-web.service"

usage() {
    echo "Usage: $0 {start|stop|restart|status|run} [-- args to ueransim-web.py]" >&2
    exit 1
}

check_deps() {
    if [[ ! -f "$SCRIPT" ]]; then
        echo "ueransim-web.py not found at $SCRIPT" >&2
        exit 1
    fi
    if ! python3 -c "import aiohttp" 2>/dev/null; then
        echo "aiohttp not installed — run 'pip3 install --user --break-system-packages aiohttp' first" >&2
        exit 1
    fi
}

has_unit() {
    systemctl list-unit-files "$SERVICE" &>/dev/null
}

do_start() {
    if has_unit; then
        echo "Starting $SERVICE via systemd..."
        sudo systemctl start "$SERVICE"
    else
        check_deps
        echo "No systemd unit installed; starting in background..."
        nohup python3 "$SCRIPT" "$@" >/tmp/ueransim-web.log 2>&1 &
        disown
        echo "Started (pid: $!), logging to /tmp/ueransim-web.log"
    fi
}

do_stop() {
    if has_unit && systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
        echo "Stopping $SERVICE via systemd..."
        sudo systemctl stop "$SERVICE"
        return
    fi
    PIDS=$(pgrep -f "python3 .*ueransim-web\.py" || true)
    if [[ -z "$PIDS" ]]; then
        echo "ueransim-web is not running"
    else
        echo "Stopping ueransim-web (pid: $PIDS)..."
        kill $PIDS
    fi
}

do_status() {
    if has_unit; then
        systemctl status "$SERVICE" --no-pager
    else
        PIDS=$(pgrep -f "python3 .*ueransim-web\.py" || true)
        if [[ -z "$PIDS" ]]; then
            echo "ueransim-web is not running"
        else
            echo "ueransim-web is running (pid: $PIDS)"
        fi
    fi
}

cmd="${1:-}"
[[ $# -gt 0 ]] && shift

case "$cmd" in
    run)
        # Foreground exec, no daemonizing — this is what systemd's ExecStart uses.
        check_deps
        exec python3 "$SCRIPT" "$@"
        ;;
    start)
        do_start "$@"
        ;;
    stop)
        do_stop
        ;;
    restart)
        if has_unit; then
            echo "Restarting $SERVICE via systemd..."
            sudo systemctl restart "$SERVICE"
        else
            do_stop
            do_start "$@"
        fi
        ;;
    status)
        do_status
        ;;
    *)
        usage
        ;;
esac
