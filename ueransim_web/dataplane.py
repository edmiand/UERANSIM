"""
Per-UE data-plane tools for the web dashboard: a restricted command console
(ping / curl, auto-bound to the UE's TUN interface) and a message inbox listening
on the UE's TUN IP for messages sent from the core network side — raw UDP
datagrams and HTTP `POST /notify` over TCP (NetAgent's send_ue_notification
MCP tool), both on the same port.

Both only make sense while the UE has a PDU session up, so UeEntry starts
and stops them from its attach / stop transitions (see registry.py).
"""

import asyncio
import json
import re
import shlex
import signal
import time
from collections import deque
from datetime import datetime
from typing import Callable, Optional

from aiohttp import web

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
# send_ue_notification caps the message at 500 chars; leave headroom for JSON + incident_id.
NOTIFY_BODY_MAX = 4096
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


class MessageInbox:
    """Listens on <UE TUN IP>:<port> for downlink messages from the core side:

    - UDP: the in-process equivalent of `nc -u -l <ip> <port>`, but accepting
      datagrams from any sender (OpenBSD nc locks onto the first peer) and
      staying up across messages.
    - TCP: HTTP `POST /notify` with JSON {"message", "incident_id"}, the contract
      of NetAgent's send_ue_notification MCP tool (same request validation and
      {"ok", "received_at", "incident_id"} reply as its ue_notify_listener.py).

    Either socket may fail to bind independently (e.g. the standalone
    ue_notify_listener.py already holds the TCP port); the other keeps working.
    """

    def __init__(self, port: int, emit: Callable[[dict], None]):
        self.port = port
        self._emit = emit
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._http: Optional[web.AppRunner] = None
        self.bound_ip: Optional[str] = None
        self.buffer: deque = deque(maxlen=UDP_BUFFER_MAX)

    @property
    def listening(self) -> bool:
        return self._transport is not None

    @property
    def http_listening(self) -> bool:
        return self._http is not None

    async def start(self, ip: str) -> list[str]:
        """Bind both sockets to ip; returns one error string per socket that failed."""
        await self.close()
        self.bound_ip = ip
        errors = []
        loop = asyncio.get_running_loop()
        try:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self._udp_received), local_addr=(ip, self.port),
            )
        except OSError as e:
            errors.append(f"UDP: {e}")
        app = web.Application(client_max_size=NOTIFY_BODY_MAX)
        app.router.add_post("/notify", self._notify)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, ip, self.port).start()
            self._http = runner
        except OSError as e:
            await runner.cleanup()
            errors.append(f"TCP: {e}")
        return errors

    def _add(self, proto: str, addr, text: str, incident: Optional[str] = None) -> dict:
        msg = {
            "time": time.strftime("%H:%M:%S"),
            "proto": proto,
            "src": f"{addr[0]}:{addr[1]}" if addr else "?",
            "text": text,
            "incident": incident,
        }
        self.buffer.append(msg)
        self._emit(msg)
        return msg

    def _udp_received(self, data: bytes, addr):
        self._add("udp", addr, data.decode(errors="replace").rstrip("\r\n"))

    async def _notify(self, request: web.Request) -> web.Response:
        def reject(status: int, error: str):
            return web.json_response({"ok": False, "error": error}, status=status)

        if request.content_length is None:
            return reject(411, "Content-Length required")
        if request.content_length > NOTIFY_BODY_MAX:
            return reject(413, f"body exceeds {NOTIFY_BODY_MAX} bytes")
        try:
            data = json.loads(await request.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return reject(400, "body is not valid JSON")
        if not isinstance(data, dict):
            return reject(400, "body must be a JSON object")
        message = data.get("message")
        incident_id = data.get("incident_id")
        if not isinstance(message, str) or not message.strip():
            return reject(400, "'message' must be a non-empty string")
        if incident_id is not None and not isinstance(incident_id, str):
            return reject(400, "'incident_id' must be a string or null")

        self._add("http", request.transport.get_extra_info("peername"), message, incident_id)
        received_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return web.json_response({"ok": True, "received_at": received_at, "incident_id": incident_id})

    async def close(self):
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        if self._http is not None:
            runner, self._http = self._http, None
            await runner.cleanup()
        self.bound_ip = None
