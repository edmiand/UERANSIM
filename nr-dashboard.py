#!/usr/bin/env python3
"""
UERANSIM Dashboard — side-by-side gNB and UE log viewer with sidebar control menu.
Usage: python3 nr-dashboard.py [--gnb-config CONFIG] [--ue-config CONFIG]
"""

import asyncio
import argparse
import os
import signal
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, RichLog, Static

# ── paths ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
BIN = BASE / "build"
CFG = BASE / "config"

GNB_BIN = BIN / "nr-gnb"
UE_BIN = BIN / "nr-ue"
CLI_BIN = BIN / "nr-cli"


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
        self._proc = None

    async def _stream(self, cb):
        try:
            async for line in self._proc.stdout:
                cb(line.decode(errors="replace").rstrip())
        except Exception:
            pass
        returncode = await self._proc.wait()
        cb(f"[bold red]--- process exited (code {returncode}) ---[/bold red]")


# ── menu items ────────────────────────────────────────────────────────────────
MENU_ITEMS = [
    ("start_gnb",   "▶  Start gNB"),
    ("stop_gnb",    "■  Stop gNB"),
    ("restart_gnb", "↺  Restart gNB"),
    ("---",         "─────────────"),
    ("start_ue",    "▶  Start UE"),
    ("stop_ue",     "■  Stop UE"),
    ("restart_ue",  "↺  Restart UE"),
    ("---",         "─────────────"),
    ("ps_list",     "   PDU: List"),
    ("ps_estab",    "   PDU: Establish"),
    ("ps_release",  "   PDU: Release All"),
    ("deregister",  "   Deregister UE"),
    ("---",         "─────────────"),
    ("save_logs",   "   Save Logs"),
    ("quit",        "✕  Quit"),
]


# ── status bar widget ─────────────────────────────────────────────────────────
class NodeStatus(Static):
    status = reactive("○ Stopped")

    def render(self) -> str:
        return self.status

    def set_running(self, node: str):
        self.status = f"[bold green]● {node}: Running[/bold green]"

    def set_stopped(self, node: str):
        self.status = f"[dim]○ {node}: Stopped[/dim]"

    def set_crashed(self, node: str):
        self.status = f"[bold red]✗ {node}: Crashed[/bold red]"


# ── main app ──────────────────────────────────────────────────────────────────
class Dashboard(App):
    CSS = """
    Screen {
        layout: horizontal;
    }

    #sidebar {
        width: 22;
        border: solid $primary;
        padding: 0 1;
    }

    #sidebar-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding: 1 0;
    }

    ListView {
        border: none;
        background: transparent;
    }

    ListItem {
        padding: 0 1;
    }

    ListItem.separator {
        color: $primary-darken-2;
        height: 1;
    }

    ListItem.--highlight {
        background: $accent 30%;
    }

    #log-area {
        layout: horizontal;
    }

    #gnb-panel, #ue-panel {
        width: 1fr;
        border: solid $primary;
    }

    .panel-title {
        text-align: center;
        text-style: bold;
        padding: 0 1;
        height: 1;
    }

    .panel-status {
        height: 1;
        padding: 0 1;
    }

    #gnb-title { color: $success; }
    #ue-title  { color: $warning; }

    RichLog {
        border: none;
        scrollbar-size: 1 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("escape", "blur_menu", "Unfocus menu", show=False),
    ]

    def __init__(self, gnb_config: str, ue_config: str):
        super().__init__()
        self.gnb_config = gnb_config
        self.ue_config = ue_config
        self.gnb = NodeProcess("gNB", [str(GNB_BIN), "-c", gnb_config])
        self.ue = NodeProcess("UE", ["sudo", str(UE_BIN), "-c", ue_config])
        self._gnb_lines: list[str] = []
        self._ue_lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal():
            # ── sidebar ──
            with Vertical(id="sidebar"):
                yield Label("UERANSIM", id="sidebar-title")
                items = []
                for action, label in MENU_ITEMS:
                    item = ListItem(Label(label))
                    item.action = action
                    if action == "---":
                        item.add_class("separator")
                        item.disabled = True
                    items.append(item)
                yield ListView(*items)

            # ── log panels ──
            with Horizontal(id="log-area"):
                with Vertical(id="gnb-panel"):
                    yield Label("gNB", id="gnb-title", classes="panel-title")
                    yield NodeStatus(id="gnb-status", classes="panel-status")
                    yield RichLog(id="gnb-log", markup=True, highlight=False, wrap=True)

                with Vertical(id="ue-panel"):
                    yield Label("UE", id="ue-title", classes="panel-title")
                    yield NodeStatus(id="ue-status", classes="panel-status")
                    yield RichLog(id="ue-log", markup=True, highlight=False, wrap=True)

        yield Footer()

    def on_mount(self):
        self._update_status()
        self.query_one(ListView).focus()

    # ── menu selection ────────────────────────────────────────────────────────
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action = getattr(event.item, "action", None)
        if action:
            asyncio.create_task(self._dispatch(action))

    async def _dispatch(self, action: str):
        if action == "start_gnb":
            await self._start_gnb()
        elif action == "stop_gnb":
            await self._stop_gnb()
        elif action == "restart_gnb":
            await self._stop_gnb()
            await asyncio.sleep(1)
            await self._start_gnb()
        elif action == "start_ue":
            await self._start_ue()
        elif action == "stop_ue":
            await self._stop_ue()
        elif action == "restart_ue":
            await self._stop_ue()
            await asyncio.sleep(1)
            await self._start_ue()
        elif action == "ps_list":
            await self._cli_ue("ps-list")
        elif action == "ps_estab":
            await self._cli_ue("ps-establish IPv4 --sst 1 --sd 1 --dnn internet")
        elif action == "ps_release":
            await self._cli_ue("ps-release-all")
        elif action == "deregister":
            await self._cli_ue("deregister switch-off")
        elif action == "save_logs":
            self._save_logs()
        elif action == "quit":
            await self._stop_gnb()
            await self._stop_ue()
            self.exit()

    # ── gNB control ──────────────────────────────────────────────────────────
    async def _start_gnb(self):
        if self.gnb.running:
            self._gnb_log("[yellow]gNB already running[/yellow]")
            return
        self._gnb_log(f"[green]Starting gNB: {' '.join(self.gnb.cmd)}[/green]")
        await self.gnb.start(self._gnb_log)
        self._update_status()

    async def _stop_gnb(self):
        if not self.gnb.running:
            return
        self._gnb_log("[yellow]Stopping gNB...[/yellow]")
        await self.gnb.stop()
        self._update_status()
        self._gnb_log("[dim]gNB stopped.[/dim]")

    # ── UE control ───────────────────────────────────────────────────────────
    async def _start_ue(self):
        if not self.gnb.running:
            self._ue_log("[red]Start gNB first.[/red]")
            return
        if self.ue.running:
            self._ue_log("[yellow]UE already running[/yellow]")
            return
        self._ue_log(f"[green]Starting UE: {' '.join(self.ue.cmd)}[/green]")
        await self.ue.start(self._ue_log)
        self._update_status()

    async def _stop_ue(self):
        if not self.ue.running:
            return
        self._ue_log("[yellow]Stopping UE...[/yellow]")
        await self.ue.stop()
        self._update_status()
        self._ue_log("[dim]UE stopped.[/dim]")

    # ── nr-cli passthrough ───────────────────────────────────────────────────
    async def _cli_ue(self, cmd: str):
        if not self.ue.running:
            self._ue_log("[red]UE is not running.[/red]")
            return
        # discover UE node name via --dump
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", str(CLI_BIN), "-d",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            lines = [l.strip() for l in out.decode().splitlines() if l.strip()]
            ue_nodes = [l for l in lines if "imsi-" in l or "UERANSIM-UE" in l]
            if not ue_nodes:
                self._ue_log(f"[red]No UE node found via nr-cli -d (saw: {lines})[/red]")
                return
            node = ue_nodes[0]
            proc2 = await asyncio.create_subprocess_exec(
                "sudo", str(CLI_BIN), node, "--exec", cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10)
            for line in out2.decode().splitlines():
                self._ue_log(f"[cyan][cli] {line}[/cyan]")
        except Exception as e:
            self._ue_log(f"[red]CLI error: {e}[/red]")

    # ── log helpers ──────────────────────────────────────────────────────────
    def _gnb_log(self, msg: str):
        self._gnb_lines.append(msg)
        self.query_one("#gnb-log", RichLog).write(msg)

    def _ue_log(self, msg: str):
        self._ue_lines.append(msg)
        self.query_one("#ue-log", RichLog).write(msg)

    # ── status update ─────────────────────────────────────────────────────────
    def _update_status(self):
        gnb_s = self.query_one("#gnb-status", NodeStatus)
        ue_s = self.query_one("#ue-status", NodeStatus)
        if self.gnb.running:
            gnb_s.set_running("gNB")
        else:
            gnb_s.set_stopped("gNB")
        if self.ue.running:
            ue_s.set_running("UE")
        else:
            ue_s.set_stopped("UE")

    def _save_logs(self):
        import re
        ansi_escape = re.compile(r'\[.*?[mGKH]|\[/?[a-z ]*\]')
        log_path = BASE / "nr-dashboard.log"
        with open(log_path, "w") as f:
            f.write("=== gNB LOG ===\n")
            for line in self._gnb_lines:
                f.write(ansi_escape.sub("", line) + "\n")
            f.write("\n=== UE LOG ===\n")
            for line in self._ue_lines:
                f.write(ansi_escape.sub("", line) + "\n")
        self._gnb_log(f"[green]Logs saved to {log_path}[/green]")

    def action_blur_menu(self):
        self.query_one(ListView).blur()

    # ── periodic status refresh ──────────────────────────────────────────────
    def on_ready(self):
        self.set_interval(2, self._update_status)


# ── entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="UERANSIM TUI Dashboard")
    parser.add_argument("--gnb-config", default=str(CFG / "open5gs-gnb.yaml"),
                        help="Path to gNB config YAML")
    parser.add_argument("--ue-config", default=str(CFG / "open5gs-ue.yaml"),
                        help="Path to UE config YAML")
    args = parser.parse_args()

    app = Dashboard(gnb_config=args.gnb_config, ue_config=args.ue_config)
    app.run()


if __name__ == "__main__":
    main()
