<p align="center">
  <a href="https://github.com/aligungr/UERANSIM"><img src="/.github/logo.png" width="75" title="UERANSIM"></a>
</p>
<p align="center">
<img src="https://img.shields.io/badge/UERANSIM-v3.3.0-blue" />
<img src="https://img.shields.io/badge/3GPP-R15-orange" />
<img src="https://img.shields.io/badge/License-AGPL--3.0-green"/>
</p>

**UERANSIM** <small>(pronounced "ju-i ræn sɪm")</small>, is the open source state-of-the-art 5G UE and RAN (gNodeB)
simulator. UE and RAN can be considered as a 5G mobile phone and a base station in basic terms. The project can be used for
testing 5G Core Network and studying 5G System.

UERANSIM introduces the world's first open source 5G-SA UE and gNodeB implementation.

## Current Status

Basic functionalities of UE and gNodeB are fully functional and ready to use. However some of the features are not complete.
More details can be found at [Feature Set](https://github.com/aligungr/UERANSIM/wiki/Feature-Set).

On the other hand, UERANSIM does not fully provide physical layer. 5G-NR radio interface is partially implemented, and simply simulated over UDP protocol.

<p align="center">
<img src="https://img.shields.io/badge/Radio%20Interface-simulated-orange" alt="OS Linux"/>
<img src="https://img.shields.io/badge/Control%20Plane-functional-green" alt="OS Linux"/>  
<img src="https://img.shields.io/badge/User%20Plane-functional-green" alt="OS Linux"/>
</p>

## Requirements

- Linux (kernel 4.15+ recommended; SCTP module must be loaded)
- CMake 3.17+
- GCC/G++ with C++17 support (GCC 9+ or Clang 10+)

## Installation

**1. Install dependencies (Ubuntu/Debian):**

```bash
sudo apt update
sudo apt install -y git cmake make gcc g++ libsctp-dev lksctp-tools
```

**2. Clone and build:**

```bash
git clone https://github.com/aligungr/UERANSIM.git
cd UERANSIM
make
```

Binaries are written to `build/`: `nr-gnb`, `nr-ue`, `nr-cli`, `nr-binder`.

## Running

```bash
# Start gNB (must run before UEs)
sudo build/nr-gnb -c config/open5gs-gnb.yaml

# Start UE
sudo build/nr-ue -c config/open5gs-ue.yaml

# CLI control of a running UE
build/nr-cli <ue-name> --exec "ps-list"

# Bind app traffic to a specific UE TUN interface
sudo build/nr-binder <ue-tun-interface> <command>
```

Edit the YAML files under `config/` to match your 5G core network (MCC, MNC, AMF address, SUPI, keys, etc.) before running.

## Operator Tooling

Two optional operator tools sit alongside the simulator (outside `src/`, not part of UERANSIM itself) for running gNB/UE side by side without juggling terminals: [`ueransim-tool.py`](UERANSIM-TOOL.md), a terminal UI, and [`ueransim-web.py`](UERANSIM-WEB.md), a browser dashboard. Both drive the same `nr-gnb`/`nr-ue`/`nr-cli` binaries built above and share their process-management/state logic via `ueransim_core.py`.

**On a fresh VM, install these on top of the build requirements already covered above:**

```bash
# Python 3.10+ and pip (Ubuntu/Debian usually ship python3; pip3 may need installing)
sudo apt install -y python3 python3-pip

# TUI dependency
pip3 install --user --break-system-packages textual

# Web dashboard dependency
pip3 install --user --break-system-packages aiohttp

# PyYAML is used by both tools' config parsing — usually already present as
# python3-yaml on Debian/Ubuntu; if not:
pip3 install --user --break-system-packages pyyaml
```

`--break-system-packages` is only needed on distros that enforce [PEP 668](https://peps.python.org/pep-0668/) (Debian/Ubuntu 23.04+); older systems accept plain `pip3 install --user`. `python3-yaml`/`python3-aiohttp` are also available as reasonably current distro packages (`sudo apt install python3-yaml python3-aiohttp`) if you'd rather avoid pip for those two — but stick to `pip3 install textual`: the `python3-textual` apt package on Debian/Ubuntu is a long-abandoned `0.1.x` release, far too old for this repo's TUI code.

**`nr-ue` needs root** to create its TUN interface — both tools invoke it via `sudo`. Add a passwordless sudoers entry once so neither tool prompts for a password (see [UERANSIM-TOOL.md](UERANSIM-TOOL.md#behavior-notes) for the exact command).

```bash
./ueransim-tool.py     # terminal UI
./ueransim-web.py      # browser dashboard, http://<this-host>:8088 by default
```

`ueransim-web.py` can also run as a systemd service that starts automatically on boot — see [UERANSIM-WEB.md](UERANSIM-WEB.md#autostart-on-boot-systemd) for the one-time `systemctl enable` setup.

## Documentation

You can find the documentation on [UERANSIM Wiki](https://github.com/aligungr/UERANSIM/wiki).

And, please make sure that you have always the [latest](https://github.com/aligungr/UERANSIM/releases) UERANSIM.

## Contributing

Any contributions you make are greatly appreciated via [Pull Request](https://github.com/aligungr/UERANSIM/pulls).

## Supporting

You can support UERANSIM by:

- Starring the GitHub repository,
- Donating on [Open Collective](https://opencollective.com/UERANSIM)
- Creating pull requests, submitting bugs, suggesting new features or documentation updates.

## License

Copyright (c) 2026 ALİ GÜNGÖR.

All source code and related files including documentation and wiki pages are
dual licensed with [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.en.html) and a commercial license.

> [!WARNING]
> Closed-source commercial usage of UERANSIM may **not** be permitted with the AGPL-3.0. If that license is not compatable with your use case, please contact [ueransim@gmail.com](mailto:ueransim@gmail.com) to buy a commercial license.
