# UERANSIM Tool

A terminal UI for controlling and monitoring [UERANSIM](https://github.com/aligungr/UERANSIM) gNB and UE instances side by side.

## Screenshot

```
┌─ UERANSIM ──────┬─── gNB ─────────────────────────┬─── UE ───────────────────────────┐
│                 │ ● gNB: Attached                  │ ◐ UE: Starting...                │
│ ▶  Start gNB    │                                  │                                  │
│ ■  Stop gNB     │ Starting gNB: build/nr-gnb ...   │                                  │
│ ↺  Restart gNB  │ [info] gNB started               │                                  │
│ ─────────────   │ [info] SCTP connection successful │                                  │
│ ▶  Start UE     │ [info] NGAP connection successful │                                  │
│ ■  Stop UE      │ [info] NG Setup procedure success │                                  │
│ ↺  Restart UE   │                                  │                                  │
│ ─────────────   │                                  │                                  │
│    PDU: List    │                                  │                                  │
│    PDU: Estab   │                                  │                                  │
│    PDU: Release │                                  │                                  │
│    Deregister   │                                  │                                  │
│ ─────────────   │                                  │                                  │
│ ✕  Quit         │                                  │                                  │
└─────────────────┴──────────────────────────────────┴──────────────────────────────────┘
```

## Requirements

- Python 3.10+
- [textual](https://github.com/Textualize/textual) library
- UERANSIM built binaries in `build/` (`nr-gnb`, `nr-ue`, `nr-cli`)

## Installation

```bash
pip3 install textual
```

## Usage

```bash
# Run with default Open5GS configs
./ueransim-tool.py

# Run with custom config files
./ueransim-tool.py --gnb-config config/open5gs-gnb.yaml \
                  --ue-config  config/open5gs-ue.yaml
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--gnb-config` | `config/open5gs-gnb.yaml` | Path to gNB YAML config |
| `--ue-config` | `config/open5gs-ue.yaml` | Path to UE YAML config |

## Navigation

| Key | Action |
|-----|--------|
| `↑` / `↓` | Scroll through the sidebar menu |
| `Enter` | Execute the selected menu item |
| `Q` | Quit (gracefully stops running processes) |
| `Esc` | Unfocus the sidebar |

## Sidebar Menu

| Item | Description |
|------|-------------|
| **Start gNB** | Launch `nr-gnb` with the configured YAML |
| **Stop gNB** | Send SIGTERM to the gNB process |
| **Restart gNB** | Stop then start gNB |
| **Start UE** | Launch `nr-ue` via `sudo` — blocked unless gNB is Attached |
| **Stop UE** | Send SIGTERM to the UE process |
| **Restart UE** | Stop then start UE |
| **PDU: List** | Run `sudo nr-cli <ue> --exec "ps-list"` |
| **PDU: Establish** | Run `sudo nr-cli <ue> --exec "ps-establish IPv4 --sst 1 --sd 1 --dnn internet"` |
| **PDU: Release All** | Run `sudo nr-cli <ue> --exec "ps-release-all"` |
| **Deregister UE** | Run `sudo nr-cli <ue> --exec "deregister switch-off"` |
| **Save Logs** | Write gNB and UE log output to `ueransim-tool.log` |
| **Quit** | Terminate both processes and exit |

## Behavior Notes

- **Status states.** Each panel's status bar shows one of four states, derived from the process's own log output rather than just "is the process alive":
  - `○ Stopped` — not running.
  - `◐ Starting...` — process launched, waiting for a success/failure signal in its log.
  - `● Attached` — gNB: `NG Setup procedure is successful` seen (NGAP registered with the AMF). UE: `Connection setup for PDU session[N] is successful` seen (registered, PDU session up, TUN interface ready).
  - `✗ Failed` — a known failure line was seen (e.g. NG Setup/SCTP failure for gNB; registration reject, RRC establishment failure, radio link failure, PLMN selection failure, or UAC barring for UE), or the process exited with a nonzero code without being told to stop. Selecting Stop while Failed clears it back to Stopped.
- **gNB must be Attached before UE.** Selecting "Start UE" while gNB is not Attached logs an error and does nothing.
- **Live log streaming.** Both gNB and UE stdout/stderr stream into their respective panes in real time.
- **Process crash detection.** If a process exits unexpectedly (nonzero exit code), the log pane shows the exit code and the status flips to Failed.
- **PDU commands** auto-discover the running UE node name via `sudo nr-cli -d` before executing. The UE node is identified by its IMSI (e.g. `imsi-999700000000001`).
- **sudo required for UE.** `nr-ue` needs root to create the TUN interface. Add a passwordless sudoers entry to avoid password prompts:
  ```bash
  echo "$(whoami) ALL=(ALL) NOPASSWD: /home/dmandrey/UERANSIM/build/nr-ue, /home/dmandrey/UERANSIM/build/nr-cli" \
    | sudo tee /etc/sudoers.d/ueransim
  sudo chmod 440 /etc/sudoers.d/ueransim
  ```
- **Save Logs** dumps all buffered gNB and UE output (markup stripped) to `ueransim-tool.log` in the project root.
- **Graceful shutdown.** Quit (via menu or `Q`) sends SIGTERM to both processes and waits up to 5 seconds before force-killing.
- **Status refresh** runs every 2 seconds as a safety-net re-render; state changes themselves are driven immediately by matching log lines as they stream in.
