"""
Per-UE data-plane tools for the web dashboard: a restricted command console
(ping / curl, auto-bound to the UE's TUN interface) and a UDP inbox listening
on the UE's TUN IP for messages sent from the core network side.

Both only make sense while the UE has a PDU session up, so UeEntry starts
and stops them from its attach / stop transitions (see registry.py).
"""

import asyncio
import re
import shlex
import signal
import time
from collections import deque
from typing import Callable, Optional

# nr-ue's attach line (src/ue/app/task.cpp) carries the TUN interface name, its IP and,
# with useNamespace: true, the namespace name. NodeProcess rich_escape()s every line, so
# "[uesimtun0, ...]" arrives as "\[uesimtun0, ...]" — hence the optional backslashes.
UE_TUN_RE = re.compile(
    r"PDU session\[(?P<psi>\d+)\] is successful, "
    r"TUN interface\\?\[(?P<iface>[^,\]]+), (?P<ip>[^\]]+)\] is up"
    r"(?: in namespace\\?\[(?P<ns>[^\]]*)\])?"
)

UDP_PORT_DEFAULT = 9000
UDP_BUFFER_MAX = 200
CONSOLE_BUFFER_MAX = 300
# Safety net for commands the operator forgot to bound (e.g. `ping` without -c).
CMD_MAX_SECS = 600

ALLOWED_PROGRAMS = ("ping", "curl")

# The dashboard has no authentication (see UERANSIM-WEB.md), so the console must not
# turn into a remote file read/write primitive. curl options that write local files,
# read local files or load a config file are rejected; local-file URL schemes are
# blocked separately via an injected --proto.
_CURL_DENY = {
    "-o", "--output", "-O", "--remote-name", "--remote-name-all", "-J", "--remote-header-name",
    "-K", "--config", "-T", "--upload-file", "-D", "--dump-header", "-c", "--cookie-jar",
    "-b", "--cookie", "--trace", "--trace-ascii", "--stderr", "--libcurl", "--output-dir",
    "-F", "--form", "--form-string",
    "--create-dirs", "--netrc-file", "-E", "--cert", "--key", "--cacert", "--capath",
    "--unix-socket", "--abstract-unix-socket", "--etag-save", "--etag-compare", "--hsts", "--alt-svc",
}
# Request-body options are allowed (handy for POSTing JSON), but not their "@file" form.
_CURL_DATA = {"-d", "--data", "--data-binary", "--data-urlencode", "--data-ascii", "--json"}


class CommandError(ValueError):
    pass


def _check_curl_data(opt: str, value: str):
    # "@file" reads a local file for every data option; --data-urlencode also
    # accepts "name@file".
    if value.startswith("@") or (opt == "--data-urlencode" and "@" in value.split("=", 1)[0]):
        raise CommandError(f"curl {opt} @file is not allowed")


def build_command(raw: str, iface: str) -> list[str]:
    """Turn the operator's command line into an argv bound to the UE interface.

    Only ping and curl are allowed; no shell is involved (shlex split, exec directly).
    """
    try:
        argv = shlex.split(raw)
    except ValueError as e:
        raise CommandError(f"cannot parse command: {e}")
    if not argv:
        raise CommandError("empty command")
    prog = argv[0]
    if prog not in ALLOWED_PROGRAMS:
        raise CommandError(f"only {', '.join(ALLOWED_PROGRAMS)} are allowed")
    args = argv[1:]

    if prog == "ping":
        for a in args:
            if a == "-f":
                raise CommandError("ping -f (flood) is not allowed")
            if a.startswith("-I"):
                raise CommandError("ping -I is not allowed (interface is set automatically)")
        return ["ping", "-I", iface, *args]

    for i, a in enumerate(args):
        if not a.startswith("-"):
            continue
        # "--output=x" and bundled short forms like "-sSo" both count.
        opt = a.split("=", 1)[0]
        if opt in _CURL_DENY:
            raise CommandError(f"curl option {opt} is not allowed")
        if opt in ("--interface", "--proto", "--proto-redir"):
            raise CommandError(f"curl option {opt} is set automatically")
        if a.startswith("--"):
            if opt in _CURL_DATA:
                value = a.split("=", 1)[1] if "=" in a else (args[i + 1] if i + 1 < len(args) else "")
                _check_curl_data(opt, value)
            continue
        # short options: "-sSd@x" — scan each letter; "d" consumes the rest / next arg
        for j, ch in enumerate(a[1:], start=1):
            if f"-{ch}" in _CURL_DENY:
                raise CommandError(f"curl option -{ch} is not allowed")
            if ch == "d":
                value = a[j + 1:] or (args[i + 1] if i + 1 < len(args) else "")
                _check_curl_data("-d", value)
                break
    return [
        # -q must come first: it stops curl from reading ~/.curlrc.
        "curl", "-q", "--interface", iface,
        "--proto", "=http,https", "--proto-redir", "=http,https",
        "--no-progress-meter",
        *args,
    ]


class CommandConsole:
    """Runs one operator command at a time for a UE and streams its output."""

    def __init__(self, emit: Callable[[str, str], None]):
        # emit(kind, text): kind is "cmd" (echo of the command), "out" or "exit"
        self._emit = emit
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._task: Optional[asyncio.Task] = None
        self.buffer: deque = deque(maxlen=CONSOLE_BUFFER_MAX)

    @property
    def busy(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _out(self, kind: str, text: str):
        self.buffer.append({"kind": kind, "text": text})
        self._emit(kind, text)

    async def run(self, raw: str, iface: str):
        argv = build_command(raw, iface)
        if self.busy:
            raise RuntimeError("a command is already running — stop it first")
        self._out("cmd", " ".join(shlex.quote(a) for a in argv))
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
        self._task = asyncio.create_task(self._stream(self._proc))

    async def _stream(self, proc: asyncio.subprocess.Process):
        try:
            async def pump():
                async for line in proc.stdout:
                    self._out("out", line.decode(errors="replace").rstrip())
            await asyncio.wait_for(pump(), timeout=CMD_MAX_SECS)
        except asyncio.TimeoutError:
            self._out("out", f"[web] command exceeded {CMD_MAX_SECS}s — stopping it")
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        code = await proc.wait()
        proc._transport.close()
        self._out("exit", f"exit code {code}")

    async def stop(self):
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        # SIGINT so ping prints its summary statistics before exiting.
        proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        if self._task is not None:
            await self._task


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_datagram: Callable[[bytes, tuple], None]):
        self._on_datagram = on_datagram

    def datagram_received(self, data: bytes, addr):
        self._on_datagram(data, addr)


class UdpInbox:
    """Listens on <UE TUN IP>:<port>/udp — the in-process equivalent of
    `nc -u -l <ip> <port>`, but accepting datagrams from any sender (OpenBSD
    nc locks onto the first peer) and staying up across messages."""

    def __init__(self, port: int, emit: Callable[[dict], None]):
        self.port = port
        self._emit = emit
        self._transport: Optional[asyncio.DatagramTransport] = None
        self.bound_ip: Optional[str] = None
        self.buffer: deque = deque(maxlen=UDP_BUFFER_MAX)

    @property
    def listening(self) -> bool:
        return self._transport is not None

    async def start(self, ip: str):
        self.close()
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self._received), local_addr=(ip, self.port),
        )
        self.bound_ip = ip

    def _received(self, data: bytes, addr):
        msg = {
            "time": time.strftime("%H:%M:%S"),
            "src": f"{addr[0]}:{addr[1]}",
            "text": data.decode(errors="replace").rstrip("\r\n"),
        }
        self.buffer.append(msg)
        self._emit(msg)

    def close(self):
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self.bound_ip = None
