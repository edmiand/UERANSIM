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
