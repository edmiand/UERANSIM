"""
In-memory process registry for the web dashboard: one gNB controller and a
dict of UE entries. No persistence across backend restarts (matches
ueransim-tool.py's model — see UERANSIM-WEB.md).
"""

import asyncio
import time
from typing import Callable, Optional

import yaml

from ueransim_core import (
    GNB_BIN, UE_BIN,
    GNB_ATTACH_RE, UE_ATTACH_RE, STARTING_TIMEOUT_SECS,
    NodeProcess, classify, cli_exec,
)
from ueransim_web.dataplane import UE_TUN_RE, UDP_PORT_DEFAULT, CommandConsole, UdpInbox

# How often a running UE's NAS state (cm-state/rm-state, via `nr-cli <node> --exec status`)
# is polled to refresh the reg/RRC-ish columns in the UE table between log-driven transitions.
STATUS_POLL_SECS = 2


class GnbController:
    def __init__(self, config_path: str, on_log: Callable[[str, str], None], on_state: Callable[[str], None]):
        self.config_path = config_path
        self.state = "stopped"
        self.fields = self._load_fields()
        self._stopping = False
        self._starting_since: Optional[float] = None
        self._proc = NodeProcess("gNB", [str(GNB_BIN), "-c", config_path])
        self._on_log = on_log
        self._on_state = on_state
        self._timeout_task: Optional[asyncio.Task] = None

    def _load_fields(self) -> dict:
        with open(self.config_path) as f:
            cfg = yaml.safe_load(f)
        amf_configs = cfg.get("amfConfigs") or []
        amf = amf_configs[0] if amf_configs else {}
        amf_address = f"{amf.get('address')}:{amf.get('port')}" if amf else None
        return {
            "mcc": cfg.get("mcc"),
            "mnc": cfg.get("mnc"),
            "nci": cfg.get("nci"),
            "tac": cfg.get("tac"),
            "linkIp": cfg.get("linkIp"),
            "amfAddress": amf_address,
            "gtpIp": cfg.get("gtpIp"),
        }

    @property
    def running(self) -> bool:
        return self._proc.running

    @property
    def stopping(self) -> bool:
        return self._stopping

    def snapshot(self) -> dict:
        return {"state": self.state, "fields": self.fields}

    def _set_state(self, new_state: str):
        if new_state != self.state:
            self.state = new_state
            self._on_state(new_state)

    def _log(self, line: str):
        self._on_log("gnb", line)
        new_state = classify(self.state, line, GNB_ATTACH_RE, self._stopping)
        if new_state is not None:
            self._set_state(new_state)
            if new_state == "failed":
                # An [error]/[critical] log line means nr-gnb hit a problem (e.g. SCTP
                # "Connection refused" because the AMF is down) — but nr-gnb doesn't
                # necessarily exit, it may just keep retrying in the background. Kill it
                # so "failed" is truthful and Start doesn't hit the already-running
                # guard below on the next click.
                asyncio.create_task(self._kill_if_running())

    async def _kill_if_running(self):
        if self._proc.running:
            self._stopping = True
            await self._proc.stop()
            self._stopping = False

    async def start(self):
        if self._proc.running:
            raise RuntimeError("gNB already running")
        self._set_state("starting")
        self._starting_since = time.monotonic()
        await self._proc.start(self._log)
        self._timeout_task = asyncio.create_task(self._starting_timeout_watch())

    async def _starting_timeout_watch(self):
        while self.state == "starting":
            await asyncio.sleep(1)
            if (
                self.state == "starting"
                and self._starting_since is not None
                and time.monotonic() - self._starting_since > STARTING_TIMEOUT_SECS
            ):
                self._log(
                    f"[bold red]gNB did not reach Connected within {STARTING_TIMEOUT_SECS}s "
                    f"(no error logged; treating as failed)[/bold red]"
                )
                self._set_state("failed")
                # The process is still alive at this point (it just hasn't attached) —
                # kill it so the "failed" state actually matches reality. Otherwise the
                # UI shows "Failed"/not-running (no Stop button, see app.js) while the
                # real nr-gnb process lingers, and a subsequent Start hits the
                # already-running guard above with a confusing error.
                await self._kill_if_running()
                return

    async def stop(self):
        if not self._proc.running:
            if self.state == "failed":
                self._set_state("stopped")
            return
        self._stopping = True
        await self._proc.stop()
        self._stopping = False
        self._set_state("stopped")


class UeEntry:
    def __init__(
        self, id: int, name: str, imsi: str, config_path: str,
        on_log: Callable[[str, str], None], on_state: Callable[["UeEntry"], None],
        on_dataplane: Callable[["UeEntry", dict], None], udp_port: int = UDP_PORT_DEFAULT,
    ):
        self.id = id
        self.name = name
        self.imsi = imsi
        self.node_name = f"imsi-{imsi}"
        self.state = "stopped"
        self.reg_state = "deregistered"
        # NAS CM-state (cm-idle / cm-connected), the closest real signal nr-cli's
        # `status` command exposes for a UE — not literally RRC layer state, but the
        # nearest concept UERANSIM surfaces without deeper instrumentation.
        self.cm_state = "CM-IDLE"
        self._stopping = False
        self._starting_since: Optional[float] = None
        self._proc = NodeProcess(f"UE-{id}", ["sudo", str(UE_BIN), "-c", config_path, "-i", imsi])
        self._on_log = on_log
        self._on_state = on_state
        self._timeout_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        # TUN details parsed from nr-ue's attach line: {"iface", "ip", "ns"}; None while
        # no PDU session is up. Drives the data-plane console and the UDP inbox.
        self.tun: Optional[dict] = None
        self._on_dataplane = on_dataplane
        self.console = CommandConsole(
            lambda kind, text: on_dataplane(self, {"type": "ue_console", "kind": kind, "text": text}))
        self.inbox = UdpInbox(
            udp_port, lambda msg: on_dataplane(self, {"type": "ue_udp", **msg}))

    @property
    def running(self) -> bool:
        return self._proc.running

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def connected(self) -> bool:
        return self.state == "attached"

    @property
    def iface(self) -> str:
        if self.connected and self.tun:
            return self.tun["iface"]
        return "uesimtun0" if self.connected else "—"

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "imsi": self.imsi,
            "state": self.state,
            "regState": self.reg_state,
            "rrcState": self.cm_state,
            "connected": self.connected,
            "iface": self.iface,
            "ip": self.tun["ip"] if self.tun else None,
            "udpPort": self.inbox.port,
            "udpListening": self.inbox.listening,
            "cmdBusy": self.console.busy,
        }

    def _set_state(self, new_state: str):
        changed = new_state != self.state
        self.state = new_state
        if new_state == "attached":
            self.reg_state = "registered"
            self.cm_state = "CM-CONNECTED"
        elif new_state in ("stopped", "failed"):
            self.reg_state = "deregistered"
            self.cm_state = "CM-IDLE"
            self._teardown_dataplane()
        if changed:
            self._on_state(self)

    def _log(self, line: str):
        self._on_log(f"ue:{self.id}", line)
        m = UE_TUN_RE.search(line)
        if m:
            self.tun = {"iface": m["iface"], "ip": m["ip"], "ns": m["ns"]}
            asyncio.create_task(self._start_inbox())
        new_state = classify(self.state, line, UE_ATTACH_RE, self._stopping)
        if new_state is not None:
            self._set_state(new_state)
            if new_state == "failed":
                # Same reasoning as GnbController._log: an [error]/[critical] line
                # doesn't guarantee nr-ue actually exited, so kill it explicitly to
                # keep "failed" truthful and avoid an already-running guard on retry.
                asyncio.create_task(self._kill_if_running())

    async def _kill_if_running(self):
        if self._proc.running:
            self._stopping = True
            await self._proc.stop()
            self._stopping = False

    # ── data plane (console + UDP inbox) ─────────────────────────────────────
    async def _start_inbox(self):
        tun = self.tun
        if tun is None:
            return
        if tun["ns"]:
            self._log(f"[yellow]UE TUN is in namespace {tun['ns']} — the UDP inbox and "
                      f"command console only support useNamespace: false[/yellow]")
            return
        try:
            await self.inbox.start(tun["ip"])
            self._log(f"[green]UDP inbox listening on {tun['ip']}:{self.inbox.port}[/green]")
        except OSError as e:
            self._log(f"[yellow]UDP inbox could not bind {tun['ip']}:{self.inbox.port}: {e}[/yellow]")
        self._on_state(self)

    def _teardown_dataplane(self):
        self.tun = None
        self.inbox.close()
        if self.console.busy:
            asyncio.create_task(self.console.stop())

    async def run_command(self, raw: str):
        if not self.connected or self.tun is None:
            raise RuntimeError("UE has no PDU session up")
        if self.tun["ns"]:
            raise RuntimeError("command console does not support useNamespace: true")
        await self.console.run(raw, self.tun["iface"])
        self._on_state(self)

    async def stop_command(self):
        await self.console.stop()
        self._on_state(self)

    async def start(self):
        if self._proc.running:
            raise RuntimeError(f"UE {self.name} already running")
        self._set_state("starting")
        self._starting_since = time.monotonic()
        await self._proc.start(self._log)
        self._timeout_task = asyncio.create_task(self._starting_timeout_watch())
        self._poll_task = asyncio.create_task(self._status_poll_loop())

    async def _starting_timeout_watch(self):
        while self.state == "starting":
            await asyncio.sleep(1)
            if (
                self.state == "starting"
                and self._starting_since is not None
                and time.monotonic() - self._starting_since > STARTING_TIMEOUT_SECS
            ):
                self._log(
                    f"[bold red]UE did not reach Attached within {STARTING_TIMEOUT_SECS}s "
                    f"(no error logged; treating as failed)[/bold red]"
                )
                self._set_state("failed")
                # Same reasoning as GnbController._starting_timeout_watch: the process
                # is still alive here, so kill it to keep "failed" truthful and avoid
                # a subsequent Start hitting the already-running guard.
                await self._kill_if_running()
                return

    async def _status_poll_loop(self):
        while self._proc.running:
            await asyncio.sleep(STATUS_POLL_SECS)
            if not self._proc.running:
                break
            try:
                out = await cli_exec(self.node_name, "status")
                self._apply_status(out)
            except Exception:
                pass

    def _apply_status(self, out: str):
        cm = rm = None
        for line in out.splitlines():
            line = line.strip()
            low = line.lower()
            if low.startswith("cm-state"):
                cm = line.split(":", 1)[1].strip()
            elif low.startswith("rm-state"):
                rm = line.split(":", 1)[1].strip()
        changed = False
        if rm is not None:
            new_reg = "registered" if "REGISTERED" in rm.upper() and "DE-REGISTERED" not in rm.upper() else "deregistered"
            if new_reg != self.reg_state:
                self.reg_state = new_reg
                changed = True
        if cm is not None and cm != self.cm_state:
            self.cm_state = cm
            changed = True
        if changed:
            self._on_state(self)

    async def stop(self):
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        await self.console.stop()
        if not self._proc.running:
            if self.state == "failed":
                self._set_state("stopped")
            return
        self._stopping = True
        await self._proc.stop()
        self._stopping = False
        self._set_state("stopped")


class UeRegistry:
    def __init__(
        self, ue_config_path: str,
        on_log: Callable[[str, str], None], on_state: Callable[[UeEntry], None],
        on_dataplane: Callable[[UeEntry, dict], None], udp_port: int = UDP_PORT_DEFAULT,
    ):
        self.ue_config_path = ue_config_path
        self.udp_port = udp_port
        self._on_dataplane = on_dataplane
        self._next_id = 1
        self.entries: dict[int, UeEntry] = {}
        self._on_log = on_log
        self._on_state = on_state

    def list(self) -> list:
        return [e.snapshot() for e in self.entries.values()]

    def get(self, id: int) -> Optional[UeEntry]:
        return self.entries.get(id)

    def add(self, name: str, imsi: str) -> UeEntry:
        id = self._next_id
        self._next_id += 1
        entry = UeEntry(id, name, imsi, self.ue_config_path, self._on_log, self._on_state,
                        self._on_dataplane, self.udp_port)
        self.entries[id] = entry
        return entry

    @property
    def running_entry(self) -> Optional[UeEntry]:
        for e in self.entries.values():
            if e.state in ("starting", "attached"):
                return e
        return None

    async def start(self, id: int):
        entry = self.entries.get(id)
        if entry is None:
            raise KeyError(id)
        other = self.running_entry
        if other is not None and other.id != id:
            raise PermissionError(f'UE "{other.name}" is already running — stop it first')
        await entry.start()

    async def stop(self, id: int):
        entry = self.entries.get(id)
        if entry is None:
            raise KeyError(id)
        await entry.stop()

    async def stop_all(self):
        for entry in list(self.entries.values()):
            await entry.stop()
