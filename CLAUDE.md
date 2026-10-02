# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

UERANSIM is a 5G-SA UE (User Equipment) and gNB (gNodeB) simulator implementing 3GPP Release 15. It simulates the radio interface over UDP (called RLS — Radio Link Simulation) and connects to a real 5G core network (Open5GS or Free5GC).

Produced binaries:
- `nr-gnb` — gNB (base station) simulator
- `nr-ue` — UE (phone) simulator
- `nr-cli` — runtime command-line control tool
- `nr-binder` — shell wrapper to bind a UE's TUN interface to apps via `LD_PRELOAD`

## Build

Requires CMake 3.17+, a C++17/C11 compiler, and Linux kernel SCTP support.

```bash
make          # release build; outputs binaries to build/
make clean    # remove build artifacts
```

The Makefile runs CMake into `cmake-build-release/` then copies binaries to `build/`.

## Running

```bash
# Start gNB (must run before UEs)
build/nr-gnb -c config/open5gs-gnb.yaml
# or: ./start-gnb.sh

# Start UE(s)
sudo build/nr-ue -c config/open5gs-ue.yaml
# or: ./start-ue.sh

# CLI control of running instances
build/nr-cli --help
build/nr-cli <ue-name> --exec "ps-list"   # list PDU sessions
build/nr-cli <ue-name> --exec "ps-establish IPv4 --sst 1 --sd 1 --dnn internet"

# Bind app traffic to a specific UE interface
sudo build/nr-binder <ue-tun-interface> <command>
```

`start-gnb.sh` / `start-ue.sh` are thin wrappers that resolve paths relative to the repo root and (for the UE) invoke `sudo` for TUN device creation.

### ueransim-tool (operator TUI)

`ueransim-tool.py` is a Python/[textual](https://github.com/Textualize/textual) terminal UI that runs gNB and UE side by side, streams their logs, derives Connected/Failed status from log content (not just process liveness), and exposes PDU session and deregistration commands. See `UERANSIM-TOOL.md` for full usage, the sidebar menu reference, and the passwordless-sudo setup needed for the UE process. This tool is operator tooling for local testing, not part of the simulator itself — it lives outside `src/`.

### ueransim-web (browser operator dashboard)

`ueransim-web.py` is a Python/[aiohttp](https://docs.aiohttp.org/) web server exposing a browser dashboard for the same gNB/UE control surface as `ueransim-tool.py` — start/stop gNB, start/stop a single fixed UE (`--ue-imsi`, default `999700000000001`), PDU session and deregistration commands, and a live merged log stream over a WebSocket. It shares its process-management and log-classification logic with the TUI via `ueransim_core.py`, so state derivation is identical between the two tools. `web-ctl.sh` wraps it with `start`/`stop`/`restart`/`status`/`run` subcommands (same convention as `start-gnb.sh`/`start-ue.sh`), and `systemd/ueransim-web.service` runs it as a boot-time systemd service. See `UERANSIM-WEB.md` for the API reference, systemd install steps, and behavior notes (binds all interfaces by default, single fixed UE, no state persistence across restarts). Also operator tooling outside `src/`, not part of the simulator itself.

## Architecture

### Threading Model: NTS (Nested Task System)

All major subsystems run as independent tasks with message-passing queues. The NTS framework (`src/utils/nts.hpp`) provides the threading backbone. Code is organized so each task owns its state and communicates only via typed messages (`NtsMessage` subclasses defined in `*/nts.hpp` within each subsystem directory).

### gNB Task Hierarchy

```
GnbAppTask       — application lifecycle, CLI server
GnbRrcTask       — RRC layer, UE context management, MIB/SIB broadcast
NgapTask         — NGAP (N2) toward 5G core AMF
SctpTask         — SCTP transport for NGAP
GtpTask          — GTP-U (N3) toward 5G core UPF, TUN packet forwarding
GnbRlsTask       — Radio Link Simulation (UDP toward UEs)
```

### UE Task Hierarchy

```
UeAppTask        — application lifecycle, CLI server
UeRrcTask        — RRC layer, cell selection, system info
NasTask          — NAS (5GMM/5GSM), registration, authentication, PDU sessions
UeRlsTask        — Radio Link Simulation (UDP toward gNB)
TunTask          — TUN device read/write, IP packet routing per PDU session
```

### Key Protocol Boundaries

| Interface | Protocol | Transport | Files |
|-----------|----------|-----------|-------|
| UE ↔ gNB (simulated radio) | RLS | UDP | `src/lib/rls/`, `src/gnb/rls/`, `src/ue/rls/` |
| gNB ↔ AMF (N2) | NGAP | SCTP | `src/gnb/ngap/`, `src/gnb/sctp/`, `src/lib/sctp/` |
| gNB ↔ UPF (N3) | GTP-U | UDP | `src/gnb/gtp/` |
| UE applications | TUN device | IP | `src/ue/tun/` |

### Protocol Library Layers (`src/lib/`)

- `nas/` — 5G NAS message encoding/decoding (5GMM + 5GSM IEs)
- `rrc/` — RRC message encoding/decoding (uses ASN.1)
- `rlc/` — RLC entity implementations: TM, UM, AM modes
- `rls/` — RLS PDU definitions shared by gNB and UE
- `crypt/` — 5G security algorithms: Milenage, SNOW-3G, ZUC, 128-EEA/EIA
- `asn/` — ASN.1 helper utilities wrapping the generated asn1c code
- `sctp/` — SCTP client/server wrappers
- `app/` — Base application class, CLI server infrastructure

### ASN.1 (`src/asn/`)

Generated C code from ASN.1 schemas. `src/asn/rrc/` covers 5G-NR RRC; `src/asn/ngap/` covers NGAP. The source `.asn1` files are in `tools/`. Do not hand-edit generated files under `src/asn/asn1c/`.

### Configuration (`config/`)

YAML files per core network variant (`open5gs-*`, `free5gc-*`, `custom-*`). Key fields:
- gNB: `mcc`, `mnc`, `nci`, `tac`, `linkIp`, `ngapIp`, `gtpIp`, `amfConfigs`, `slices`
- UE: `supi`, `key`, `op`/`opType`, `sessions` (PDU session list), `gnbSearchList`

### External Libraries (`src/ext/`)

Bundled vendored dependencies — do not upgrade them via package managers:
- `spdlog` — logging
- `yaml-cpp` — config parsing
- `compact25519` — ECC for SUCI protection
- `crypt-ext` — additional crypto primitives

## Code Conventions

- C++17; avoid raw pointers where smart pointers apply
- Each task subclass lives in its own `task.cpp/hpp`; message types are in the sibling `nts.hpp`
- Message handler functions are split into `handler.cpp` files within each subsystem
- Logging uses `logger->*()` (spdlog wrapper); logger instances are per-task
- YAML config structs are defined in `types.hpp` within each subsystem and populated via `yaml_utils`
