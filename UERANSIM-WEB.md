# UERANSIM Web

A browser dashboard for controlling and monitoring [UERANSIM](https://github.com/aligungr/UERANSIM) gNB and UE instances, alongside the terminal-based [`ueransim-tool.py`](UERANSIM-TOOL.md). Both tools share the same process-management and log-classification core (`ueransim_core.py`), so state derivation and behavior are identical between the TUI and the web UI.

## Requirements

- Python 3.10+
- [aiohttp](https://docs.aiohttp.org/) library
- UERANSIM built binaries in `build/` (`nr-gnb`, `nr-ue`, `nr-cli`)
- A modern browser

## Installation

```bash
pip3 install aiohttp
```

## Usage

```bash
# Run with default Open5GS configs, bound to all interfaces on port 8088
./ueransim-web.py

# Restrict to localhost only
./ueransim-web.py --host 127.0.0.1

# Run with custom config files / bind address / port
./ueransim-web.py --gnb-config config/open5gs-gnb.yaml \
                   --ue-config  config/open5gs-ue.yaml \
                   --host 0.0.0.0 --port 8088
```

The startup banner prints the actual reachable URL(s) for the host it's running on (e.g. `http://192.168.64.20:8088`) — bind defaults to `0.0.0.0` precisely so this works unchanged on any machine/VM regardless of its IP.

`start-web.sh` / `stop-web.sh` are thin wrappers, same convention as `start-gnb.sh`/`start-ue.sh`: `start-web.sh` resolves paths relative to the repo root, checks `aiohttp` is installed, and execs `ueransim-web.py` (any extra args are passed through). `stop-web.sh` stops it via `systemctl` if running as the systemd service below, otherwise falls back to finding and signaling the process directly.

## Autostart on boot (systemd)

A unit file is provided at `systemd/ueransim-web.service` (`Type=simple`, runs as the owning user — not root — since only the `sudo nr-ue`/`nr-cli` child processes it spawns need elevation, via the passwordless-sudoers entry from `UERANSIM-TOOL.md`).

```bash
sudo cp systemd/ueransim-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ueransim-web   # starts now and on every future boot
```

Useful commands:

```bash
sudo systemctl status ueransim-web     # is it running?
sudo journalctl -u ueransim-web -f     # follow its logs (startup banner, gNB/UE process output)
sudo systemctl stop ueransim-web       # stop it (or: ./stop-web.sh)
sudo systemctl disable ueransim-web    # stop autostarting on boot
```

The unit's `User=`/`WorkingDirectory=`/`ExecStart=` paths are specific to the machine it was generated for — edit `systemd/ueransim-web.service` before installing it on a different host or under a different user.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address — all interfaces, reachable from the LAN on whatever IP this host has. Pass `--host 127.0.0.1` to restrict to localhost only. See Behavior Notes. |
| `--port` | `8088` | Bind port. |
| `--gnb-config` | `config/open5gs-gnb.yaml` | Path to gNB YAML config. |
| `--ue-config` | `config/open5gs-ue.yaml` | Base UE YAML config; each added UE overrides its IMSI at launch via `nr-ue -i`. |

## Dashboard

- **Top bar** — "+ Add UE" opens a modal to register a new simulated UE (name + IMSI); "Start/Stop gNB" toggles the single gNB process.
- **Stat cards** — gNB status, registered UE count, active PDU sessions, and total UEs added.
- **gNodeB card** — live config fields (MCC/MNC, TAC, NCI, Link IP, AMF address, GTP IP) parsed from the gNB YAML, plus a status pill.
- **Link topology** — Core → gNB → UE(s) diagram; line/node colors reflect live connection state.
- **User Equipment table** — one row per added UE: registration state, NAS CM-state, a coarse signal indicator, TUN interface name, and per-row start/stop (⏸/▶) and remove (✕) controls.
- **Live log** — merged, color-tagged gNB/UE log stream (cyan `[gnb]`, green UE info, red on failure).

## API Reference

Base path `/api`, WebSocket at `/ws`.

| Method & Path | Description |
|---|---|
| `GET /api/gnb` | Current gNB state + parsed config fields |
| `POST /api/gnb/start` / `/stop` | Start/stop the gNB process |
| `GET /api/ues` | List all added UEs |
| `POST /api/ues` `{name, imsi}` | Add a UE; auto-starts it if the gNB is attached |
| `POST /api/ues/{id}/start` / `/stop` | Start/stop a specific UE |
| `DELETE /api/ues/{id}` | Stop (if running) and remove a UE |
| `GET /api/ues/{id}/pdu` | Run `ps-list` (async — result arrives over `/ws`) |
| `POST /api/ues/{id}/pdu/establish` | Run `ps-establish IPv4 --sst 1 --sd 1 --dnn internet` |
| `POST /api/ues/{id}/pdu/release-all` | Run `ps-release-all` |
| `POST /api/ues/{id}/deregister` | Run `deregister switch-off` |

`/ws` is server-push only: on connect it replays a full snapshot (gNB state, UE list, last ~50 log lines), then streams `log`, `gnb_state`, and `ue_state` events as they happen.

## Behavior Notes

- **State derivation is identical to the TUI.** Both tools call the same `classify()` function in `ueransim_core.py`, driven by the same log-level/regex rules documented in [UERANSIM-TOOL.md's Behavior Notes](UERANSIM-TOOL.md#behavior-notes) (attach regexes, `[error]`/`[critical]` = failed, 20s starting-timeout fallback). No separate state logic exists for the web tool.
- **Only one UE may run at a time.** Each `nr-ue` process numbers its TUN interfaces starting at `uesimtun0` independently — running two at once collides without network-namespace isolation, which this tool doesn't set up (`useNamespace` stays at its config default). The UE table supports adding multiple UE entries, but starting a second one while another is running/starting is rejected with `409` and a log line telling you to stop the first. Stop the running UE before starting another.
- **Arbitrary IMSIs will genuinely fail authentication.** Only `imsi-999700000000001` is provisioned in the demo Open5GS core by default. Adding a UE with any other IMSI starts a real `nr-ue` process that will fail registration against the real core — this surfaces as a red "Failed" state via the same log classification as any other failure, it is not special-cased or faked.
- **No fabricated telemetry.** The UE table's "Signal" column is a coarse indicator derived from connection state (stopped/starting/attached/failed), not a real RSRP measurement — UERANSIM's `nr-cli status` command doesn't expose one. The "RRC state" column shows the UE's real NAS CM-state (`CM-IDLE`/`CM-CONNECTED`, from periodic `nr-cli <node> --exec status` polling), the closest real signal available.
- **In-memory state only, no persistence.** Restarting `ueransim-web.py` forgets all added UEs and their state. Any `nr-gnb`/`nr-ue` process still running at that point becomes orphaned (it keeps running under its original PID, no longer tracked by the new backend instance) — stop everything from the dashboard before restarting the web tool.
- **Reachable on all interfaces by default, no authentication.** This is local operator tooling that spawns `sudo`-invoked processes; the `--host` default (`0.0.0.0`) makes it reachable from any network this host is on, with no login and no access control. Anything that can reach the host's IP can start/stop the gNB, add UEs, and run arbitrary PDU/deregister commands. Pass `--host 127.0.0.1` to restrict it to the local machine only.
- **sudo required for UE.** Same requirement and setup as the TUI — see [UERANSIM-TOOL.md's sudoers instructions](UERANSIM-TOOL.md#behavior-notes); this tool does not re-derive them.
- **Graceful shutdown.** `Ctrl-C` on the web server stops the gNB and all UE processes before exiting (aiohttp `on_shutdown` hook), same spirit as the TUI's Quit action.
