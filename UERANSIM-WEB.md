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
| `--udp-port` | `9000` | Port the UE message inbox listens on, bound to the UE's TUN IP — both UDP and TCP (HTTP `POST /notify`). |

## Dashboard

The page is aimed at people who don't work with telecom. It uses plain-language labels and keeps 3GPP terms optional. On desktop-sized windows (at least ~1000×640) it fits the window without page scrolling: the UE Console and Activity cards take the remaining height and their console, inbox and log panes scroll internally. Smaller windows fall back to fixed pane heights and page scrolling; below ~1100px wide the network cards wrap to a 2×2 grid (one column below ~600px).

- **Header** — brand and an overall status pill (Offline / Connecting… / Network ready / Phone online / Problem detected).
- **Your network** — four equal-width cards left to right in the order data flows: **Phone → gNB → Core network → Internet**. Each card shows its 3GPP name underneath (UE · IMSI; gNB node name · PLMN · TAC · NCI; 5GC · AMF address; DNN · TUN interface) and a live status pill. Phone has Power on/off (start/stop the single fixed UE, `nr-ue-01`, `imsi-999700000000001` by default; see `--ue-imsi`) plus Connect/Disconnect beside it (`ps-establish` / `ps-release-all`); gNB has Start/Stop. The connectors between cards (Radio, Linked, Data) light up as each link comes up. **Technical details** expands the gNB config fields parsed from the gNB YAML (MCC, MNC, NCI, TAC, AMF address, GTP IP, link IP) and the UE's IMSI, IP, interface, registration state, NAS CM-state and UDP port.
- **UE Console** — follows the attached UE (interface and IP are parsed from nr-ue's `TUN interface[uesimtun0, 10.45.0.x] is up` line). Two tabs:
  - **Send traffic** — quick-test buttons (ping google.com, ping 8.8.8.8, load a web page) plus a command line that runs `ping` or `curl` through the UE's PDU session. The interface is injected automatically (`ping -I <iface>`, `curl --interface <iface>`), so type `ping -c 4 google.com` or `curl -sI https://www.google.com` as-is. One command at a time; **Stop** sends SIGINT (so `ping` prints its summary). Commands without a bound (e.g. `ping` with no `-c`) are stopped after 600s.
  - **Incoming messages** — a downlink message inbox on the UE's TUN IP (Open5GS can't deliver SMS over NAS), showing each message with its sender, protocol and time, newest first. It listens on two sockets on the same port:
    - **UDP** — the in-process equivalent of `nc -u -l <UE IP> 9000`. Unlike OpenBSD `nc -u -l` it accepts any number of senders and stays up between messages. Send from the core host with `echo "hello" | nc -u -w1 <UE IP> 9000`.
    - **TCP, HTTP `POST /notify`** — the contract of NetAgent's `send_ue_notification` MCP tool, a drop-in replacement for its `scripts/ue_notify_listener.py`. Body is JSON `{"message": "<non-empty string>", "incident_id": "<string or null>"}` (max 4096 bytes). Reply is `200 {"ok": true, "received_at": "<ISO time>", "incident_id": ...}`, or `{"ok": false, "error": ...}` with 400/411/413. The incident ID is shown next to the message. Send from the core host with `curl -X POST http://<UE IP>:9000/notify -H 'Content-Type: application/json' -d '{"message":"hello","incident_id":"INC-1"}'`.

    Each socket binds independently: if one port is already taken (for example `ue_notify_listener.py` still running on TCP 9000), the Activity log shows a yellow "could not bind" line and the other socket keeps working. The pill turns amber when only one is listening. Both rebind automatically to the new IP every time the UE gets a PDU session, so the inbox follows UE restarts.
- **Activity** — **Highlights** shows plain-language milestones derived client-side from known log lines (gNB linked to the core, phone registered, phone online with its IP, …), every failure line, and dashboard button presses, newest first. **Full log** is the merged, color-tagged gNB/UE log stream (cyan `[gnb]`, green UE info, red on failure).

The details panel and the selected tabs are remembered per browser (`localStorage`).

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
| `GET /api/ues/{id}/udp` | Message inbox (UDP + HTTP `/notify`): `{ip, port, listening, httpListening, messages: [{time, proto: udp\|http, src, text, incident}]}` (last 200 messages) |

`/ws` is server-push only: on connect it replays a full snapshot (gNB state, UE list, last ~50 log lines, per-UE console and message inbox buffers), then streams `log` (`{source, tag, color, level: info|success|fail, time, text}`), `gnb_state`, `ue_state`, `ue_console` (`{id, kind: cmd|out|exit, text}`) and `ue_udp` (`{id, time, proto, src, text, incident}`) events as they happen.

## Behavior Notes

- **State derivation is identical to the TUI.** Both tools call the same `classify()` function in `ueransim_core.py`, driven by the same log-level/regex rules documented in [UERANSIM-TOOL.md's Behavior Notes](UERANSIM-TOOL.md#behavior-notes) (attach regexes, `[error]`/`[critical]` = failed, 20s starting-timeout fallback). No separate state logic exists for the web tool.
- **One fixed UE.** The backend creates exactly one UE at startup from `--ue-imsi` (default `999700000000001`, the IMSI provisioned in the demo Open5GS core) and `--ue-config`; there is no add/remove API. Running a single `nr-ue` also avoids `uesimtun0` collisions, since each `nr-ue` numbers its TUN interfaces independently. Passing an unprovisioned `--ue-imsi` starts a real `nr-ue` that genuinely fails registration — surfaced as a red "Failed" state, not faked.
- **No fabricated telemetry.** Every status on the page is derived from real process state and log lines. The "Connection (CM)" detail is the UE's real NAS CM-state (`CM-IDLE`/`CM-CONNECTED`, from periodic `nr-cli <node> --exec status` polling); there is no signal-strength readout because UERANSIM's `nr-cli status` doesn't expose one.
- **"Disconnect" is tracked client-side.** The UE stays in the `attached` state after `ps-release-all`, so the page marks the session closed locally until the next `TUN interface[...] is up` line (or a UE restart). Another browser tab won't see that release.
- **In-memory state only, no persistence.** Restarting `ueransim-web.py` resets the UE and gNB state (the fixed UE is recreated, stopped). Any `nr-gnb`/`nr-ue` process still running at that point becomes orphaned (it keeps running under its original PID, no longer tracked by the new backend instance) — stop everything from the dashboard before restarting the web tool.
- **Reachable on all interfaces by default, no authentication.** This is local operator tooling that spawns `sudo`-invoked processes; the `--host` default (`0.0.0.0`) makes it reachable from any network this host is on, with no login and no access control. Anything that can reach the host's IP can start/stop the gNB and UE, and run arbitrary PDU/deregister commands. Pass `--host 127.0.0.1` to restrict it to the local machine only.
- **Data plane console is restricted.** Since the dashboard is unauthenticated, the console runs only `ping` and `curl`, executed directly (no shell) as the dashboard's user. It rejects options that would escape the UE interface (`ping -I`, `curl --interface`), flood (`ping -f`), or read/write local files (`curl -o/-O/-K/-T/-F/-b/-c/-D/...`, `-d @file`). It also forces `--proto =http,https` (no `file://`) and `-q` (ignores `~/.curlrc`).
- **Data plane requires `useNamespace: false`.** The console and inbox use the UE's TUN interface in the host namespace. With `useNamespace: true` they stay disabled and the UE log says why.
- **Inbox only receives traffic that crosses the 5G user plane when the core runs on another host.** On the same host, `10.45.0.x` is a local address and packets would skip UPF → GTP-U → gNB. With the core on a separate host, a datagram sent from it to the UE IP is routed via the UPF's `ogstun`, as intended. The UE host firewall must allow inbound UDP on the inbox port on the TUN interface.
- **Internet reachability depends on the core host.** If `ping 10.45.0.1` (the UPF) works but `ping google.com` doesn't, the core host is missing IP forwarding / NAT (MASQUERADE) for the UE subnet. That is an Open5GS host setup issue, not a dashboard one.
- **sudo required for UE.** Same requirement and setup as the TUI — see [UERANSIM-TOOL.md's sudoers instructions](UERANSIM-TOOL.md#behavior-notes); this tool does not re-derive them.
- **Graceful shutdown.** `Ctrl-C` on the web server stops the gNB and all UE processes before exiting (aiohttp `on_shutdown` hook), same spirit as the TUI's Quit action.
