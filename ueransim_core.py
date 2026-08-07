"""
Shared process-management and log-based state-classification core for the
UERANSIM operator tools (ueransim-tool.py TUI and ueransim-web.py web
dashboard). Stdlib-only so neither tool's dependency set leaks into the
other (the TUI doesn't need aiohttp, the web tool doesn't need textual).
"""

import asyncio
import re
import signal
from pathlib import Path
from typing import Optional

from rich.markup import escape as rich_escape

# ── paths ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
BIN = BASE / "build"
CFG = BASE / "config"

GNB_BIN = BIN / "nr-gnb"
UE_BIN = BIN / "nr-ue"
CLI_BIN = BIN / "nr-cli"


# ── log-based state detection ─────────────────────────────────────────────────
# Success is inferred from characteristic log lines emitted by nr-gnb/nr-ue.
# Failure is NOT matched against a curated list of known failure messages: nr-gnb/nr-ue
# can log a failure/rejection in many different ways (auth reject, RRC failure, SM
# reject, timer expiry, config errors, ...) and any allow-list of message text goes
# stale and leaves the UI stuck on "Starting..." whenever a new/unlisted failure log
# line appears. Instead we rely on the log *level*: spdlog's default pattern
# ("[%Y-%m-%d %H:%M:%S.%e] [%n] [%l] %v") tags every line with its level, and
# logger->err()/fatal() in this codebase (src/utils/logger.hpp/.cpp) is only ever used
# to report conditions that are not success. So "not success" == "saw an [error] or
# [critical] level line", per the "everything that isn't success is failure" rule.
GNB_ATTACH_RE = re.compile(r"NG Setup procedure is successful")
UE_ATTACH_RE = re.compile(r"Connection setup for PDU session\[\d+\] is successful")

FAIL_LEVEL_RE = re.compile(r"\[(error|critical)\]")

EXIT_CODE_RE = re.compile(r"process exited \(code (-?\d+)\)")

# Some failure modes (e.g. the network silently dropping a request instead of
# rejecting it) never produce an [error]/[critical] log line at all — only a
# debug-level timer expiry, which isn't surfaced to this tool. So "Starting..."
# also counts as "not success" once it has dragged on too long.
STARTING_TIMEOUT_SECS = 20


def classify(current_state: str, msg: str, attach_re: "re.Pattern[str]", is_stopping: bool) -> Optional[str]:
    """Derive a new state from a single log line, or None if unchanged.

    current_state/return value are one of: "stopped", "starting", "attached", "failed".
    attach_re is GNB_ATTACH_RE or UE_ATTACH_RE depending on the node kind.
    """
    if attach_re.search(msg):
        return "attached"
    if FAIL_LEVEL_RE.search(msg):
        return "failed"
    m = EXIT_CODE_RE.search(msg)
    if m and not is_stopping and int(m.group(1)) != 0:
        return "failed"
    return None


# ── process wrapper ───────────────────────────────────────────────────────────
class NodeProcess:
    def __init__(self, name: str, cmd: list[str]):
        self.name = name
        self.cmd = cmd
        self._proc: Optional[asyncio.subprocess.Process] = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self, log_cb):
        if self.running:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        asyncio.create_task(self._stream(log_cb))

    async def stop(self):
        if self._proc and self._proc.returncode is None:
            self._proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._proc is not None:
            # Close the pipe transport now, while the loop is still running.
            # Otherwise it's closed by __del__ after the loop has already
            # shut down, which logs a harmless but noisy
            # "RuntimeError: Event loop is closed".
            self._proc._transport.close()
        self._proc = None

    async def _stream(self, cb):
        try:
            async for line in self._proc.stdout:
                # nr-gnb/nr-ue output is plain text, not Rich markup: escape it so
                # bracketed content (timestamps, logger name, level tag) is displayed
                # verbatim instead of being parsed/swallowed as bogus markup tags.
                cb(rich_escape(line.decode(errors="replace").rstrip()))
        except Exception:
            pass
        returncode = await self._proc.wait()
        cb(f"[bold red]--- process exited (code {returncode}) ---[/bold red]")


# ── nr-cli discovery & passthrough ─────────────────────────────────────────────
async def discover_nodes() -> list[str]:
    """Return node names (e.g. imsi-999700000000001) currently registered with
    the local nr-cli command server, via `sudo nr-cli -d`."""
    proc = await asyncio.create_subprocess_exec(
        "sudo", str(CLI_BIN), "-d",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    proc._transport.close()
    lines = [l.strip() for l in out.decode().splitlines() if l.strip()]
    return [l for l in lines if "imsi-" in l or "UERANSIM-UE" in l]


async def cli_exec(node: str, cmd: str) -> str:
    """Run `sudo nr-cli <node> --exec "<cmd>"` and return its combined output."""
    proc = await asyncio.create_subprocess_exec(
        "sudo", str(CLI_BIN), node, "--exec", cmd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    proc._transport.close()
    return out.decode()
