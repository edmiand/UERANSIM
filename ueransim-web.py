#!/usr/bin/env python3
"""
UERANSIM Web — browser dashboard for controlling and monitoring UERANSIM
gNB and UE instances.
Usage: python3 ueransim-web.py [--host 0.0.0.0] [--port 8088] [--gnb-config CONFIG] [--ue-config CONFIG] [--udp-port 9000]
"""

import argparse
import socket

from aiohttp import web

from ueransim_core import CFG
from ueransim_web.dataplane import UDP_PORT_DEFAULT
from ueransim_web.server import UE_IMSI_DEFAULT, make_app


def _local_ips() -> list[str]:
    """Best-effort list of this host's non-loopback IPv4 addresses, for the
    startup banner — bind host is 0.0.0.0 so the actual reachable address(es)
    vary per machine and are worth printing explicitly."""
    ips = set()
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except socket.gaierror:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def main():
    parser = argparse.ArgumentParser(description="UERANSIM Web Dashboard")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address (default: 0.0.0.0 — all interfaces, so this works "
                             "unchanged on any host/VM regardless of its IP. This control plane "
                             "spawns sudo processes with no authentication; pass --host 127.0.0.1 "
                             "to restrict it to the local machine only)")
    parser.add_argument("--port", type=int, default=8088, help="Bind port (default: 8088)")
    parser.add_argument("--gnb-config", default=str(CFG / "open5gs-gnb.yaml"),
                        help="Path to gNB config YAML")
    parser.add_argument("--ue-config", default=str(CFG / "open5gs-ue.yaml"),
                        help="Path to UE config YAML (base config; the UE's IMSI is overridden via -i)")
    parser.add_argument("--ue-imsi", default=UE_IMSI_DEFAULT,
                        help=f"IMSI of the dashboard's single fixed UE (default: {UE_IMSI_DEFAULT})")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT_DEFAULT,
                        help=f"UDP port the per-UE inbox listens on, bound to the UE's TUN IP "
                             f"(default: {UDP_PORT_DEFAULT})")
    args = parser.parse_args()

    app = make_app(args.gnb_config, args.ue_config, args.ue_imsi, args.udp_port)
    if args.host in ("0.0.0.0", "::"):
        reachable = _local_ips() or [args.host]
        for ip in reachable:
            print(f"UERANSIM Web listening on http://{ip}:{args.port}")
    else:
        print(f"UERANSIM Web listening on http://{args.host}:{args.port}")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
