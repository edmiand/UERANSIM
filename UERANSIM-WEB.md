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

`web-ctl.sh` is a lifecycle wrapper, same convention as `start-gnb.sh`/`start-ue.sh`, with `start`/`stop`/`restart`/`status`/`run` subcommands:

```bash
sudo ./web-ctl.sh start     # start (via systemctl if the unit below is installed, else backgrounds it directly)
sudo ./web-ctl.sh stop      # stop it
sudo ./web-ctl.sh restart   # restart it
./web-ctl.sh status         # is it running?
./web-ctl.sh run            # foreground, no daemonizing — what systemd's ExecStart uses; extra args pass through to ueransim-web.py
```

`start`/`stop`/`restart` shell out to `systemctl` when the `ueransim-web.service` unit is installed (see below), and fall back to backgrounding/signaling the process directly otherwise.

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
sudo systemctl stop ueransim-web       # stop it (or: ./web-ctl.sh stop)
sudo systemctl disable ueransim-web    # stop autostarting on boot
```

The unit's `User=`/`WorkingDirectory=`/`ExecStart=` paths are specific to the machine it was generated for — edit `systemd/ueransim-web.service` before installing it on a different host or under a different user.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address — all interfaces, reachable from the LAN on whatever IP this host has. Pass `--host 127.0.0.1` to restrict to localhost only. See Behavior Notes. |
| `--port` | `8088` | Bind port. |
| `--gnb-config` | `config/open5gs-gnb.yaml` | Path to gNB YAML config. |
| `--ue-config` | `config/open5gs-ue.yaml` | Base UE YAML config (key, OP, sessions, …); the IMSI is overridden at launch via `nr-ue -i`. |
| `--ue-imsi` | `999700000000001` | IMSI of the dashboard's single fixed UE (`nr-ue-01`). |
| `--udp-port` | `9000` | UDP port the UE data plane inbox listens on, bound to the UE's TUN IP. |

## Dashboard

The page is a single-screen layout (no sidebar): on a typical desktop viewport everything fits without page scrolling, and the console, UDP inbox, and log panes scroll internally. Below ~1100px wide it falls back to a stacked, page-scrolling layout.

- **Top bar** — brand and AMF reachability; a one-line summary (gNB status · UE registration state · PDU session up/down); "Start/Stop gNB" toggles the single gNB process.
- **gNodeB card** — status pill, a Core → gNB → UE link topology (line/node colors reflect live connection state), and live config fields (MCC/MNC, TAC, NCI, Link IP, AMF address, GTP IP) parsed from the gNB YAML.
- **User Equipment table** — a single, permanently configured UE (`nr-ue-01`, `imsi-999700000000001` by default; see `--ue-imsi`): registration state, NAS CM-state, a coarse signal indicator, TUN interface name, and a start/stop (▶/⏸) control. UEs cannot be added or removed from the dashboard.
- **UE data plane** — follows the attached UE (interface and IP are parsed from nr-ue's `TUN interface[uesimtun0, 10.45.0.x] is up` line). Two panes:
  - **Command console** — runs `ping` or `curl` through the UE's PDU session. The interface is injected automatically (`ping -I <iface>`, `curl --interface <iface>`), so type `ping -c 4 google.com` or `curl -sI https://www.google.com` as-is. One command at a time; **Stop** sends SIGINT (so `ping` prints its summary). Commands without a bound (e.g. `ping` with no `-c`) are stopped after 600s.
  - **UDP inbox** — the in-process equivalent of `nc -u -l <UE IP> 9000`: shows every datagram received on the UE's TUN IP with its sender. Unlike OpenBSD `nc -u -l` it accepts any number of senders and stays up between messages. Used as a downlink channel from the core network (Open5GS can't deliver SMS over NAS). Send from the core host with `echo "hello" | nc -u -w1 <UE IP> 9000`.
- **Live log** — shown beside the data plane; merged, color-tagged gNB/UE log stream (cyan `[gnb]`, green UE info, red on failure).

## API Reference

Base path `/api`, WebSocket at `/ws`.

| Method & Path | Description |
|---|---|
| `GET /api/gnb` | Current gNB state + parsed config fields |
| `POST /api/gnb/start` / `/stop` | Start/stop the gNB process |
| `GET /api/ues` | List UEs (always the single fixed UE, `id` 1) |
| `POST /api/ues/{id}/start` / `/stop` | Start/stop the UE (start requires the gNB to be attached) |
| `GET /api/ues/{id}/pdu` | Run `ps-list` (async — result arrives over `/ws`) |
| `POST /api/ues/{id}/pdu/establish` | Run `ps-establish IPv4 --sst 1 --sd 1 --dnn internet` |
| `POST /api/ues/{id}/pdu/release-all` | Run `ps-release-all` |
| `POST /api/ues/{id}/deregister` | Run `deregister switch-off` |
| `POST /api/ues/{id}/exec` `{cmd}` | Run a `ping`/`curl` command through the UE interface (output streams over `/ws`). `400` if the command is rejected, `409` if no PDU session or a command is already running |
| `POST /api/ues/{id}/exec/stop` | Stop the running console command (SIGINT) |
| `GET /api/ues/{id}/exec` | Console buffer: `{busy, lines: [{kind, text}]}` (last 300 lines) |
| `GET /api/ues/{id}/udp` | UDP inbox: `{ip, port, listening, messages: [{time, src, text}]}` (last 200 messages) |

`/ws` is server-push only: on connect it replays a full snapshot (gNB state, UE list, last ~50 log lines, per-UE console and UDP inbox buffers), then streams `log`, `gnb_state`, `ue_state`, `ue_console` (`{id, kind: cmd|out|exit, text}`) and `ue_udp` (`{id, time, src, text}`) events as they happen.

## Behavior Notes

- **State derivation is identical to the TUI.** Both tools call the same `classify()` function in `ueransim_core.py`, driven by the same log-level/regex rules documented in [UERANSIM-TOOL.md's Behavior Notes](UERANSIM-TOOL.md#behavior-notes) (attach regexes, `[error]`/`[critical]` = failed, 20s starting-timeout fallback). No separate state logic exists for the web tool.
- **One fixed UE.** The backend creates exactly one UE at startup from `--ue-imsi` (default `999700000000001`, the IMSI provisioned in the demo Open5GS core) and `--ue-config`; there is no add/remove API. Running a single `nr-ue` also avoids `uesimtun0` collisions, since each `nr-ue` numbers its TUN interfaces independently. Passing an unprovisioned `--ue-imsi` starts a real `nr-ue` that genuinely fails registration — surfaced as a red "Failed" state, not faked.
- **No fabricated telemetry.** The UE table's "Signal" column is a coarse indicator derived from connection state (stopped/starting/attached/failed), not a real RSRP measurement — UERANSIM's `nr-cli status` command doesn't expose one. The "RRC state" column shows the UE's real NAS CM-state (`CM-IDLE`/`CM-CONNECTED`, from periodic `nr-cli <node> --exec status` polling), the closest real signal available.
- **In-memory state only, no persistence.** Restarting `ueransim-web.py` resets the UE and gNB state (the fixed UE is recreated, stopped). Any `nr-gnb`/`nr-ue` process still running at that point becomes orphaned (it keeps running under its original PID, no longer tracked by the new backend instance) — stop everything from the dashboard before restarting the web tool.
- **Reachable on all interfaces by default, no authentication.** This is local operator tooling that spawns `sudo`-invoked processes; the `--host` default (`0.0.0.0`) makes it reachable from any network this host is on, with no login and no access control. Anything that can reach the host's IP can start/stop the gNB and UE, and run arbitrary PDU/deregister commands. Pass `--host 127.0.0.1` to restrict it to the local machine only.
- **Data plane console is restricted.** Since the dashboard is unauthenticated, the console runs only `ping` and `curl`, executed directly (no shell) as the dashboard's user. It rejects options that would escape the UE interface (`ping -I`, `curl --interface`), flood (`ping -f`), or read/write local files (`curl -o/-O/-K/-T/-F/-b/-c/-D/...`, `-d @file`). It also forces `--proto =http,https` (no `file://`) and `-q` (ignores `~/.curlrc`).
- **Data plane requires `useNamespace: false`.** The console and inbox use the UE's TUN interface in the host namespace. With `useNamespace: true` they stay disabled and the UE log says why.
- **Inbox only receives traffic that crosses the 5G user plane when the core runs on another host.** On the same host, `10.45.0.x` is a local address and packets would skip UPF → GTP-U → gNB. With the core on a separate host, a datagram sent from it to the UE IP is routed via the UPF's `ogstun`, as intended. The UE host firewall must allow inbound UDP on the inbox port on the TUN interface.
- **Internet reachability depends on the core host.** If `ping 10.45.0.1` (the UPF) works but `ping google.com` doesn't, the core host is missing IP forwarding / NAT (MASQUERADE) for the UE subnet. That is an Open5GS host setup issue, not a dashboard one.
- **sudo required for UE.** Same requirement and setup as the TUI — see [UERANSIM-TOOL.md's sudoers instructions](UERANSIM-TOOL.md#behavior-notes); this tool does not re-derive them.
- **Graceful shutdown.** `Ctrl-C` on the web server stops the gNB and all UE processes before exiting (aiohttp `on_shutdown` hook), same spirit as the TUI's Quit action.
