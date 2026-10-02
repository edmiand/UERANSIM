"""
aiohttp application: REST API + WebSocket log/state stream + static file
serving for the UERANSIM web dashboard. See UERANSIM-WEB.md for the full
API reference and behavior notes.
"""

import asyncio
import re
import time
from collections import deque
from pathlib import Path

from aiohttp import web, WSMsgType, WSCloseCode

from ueransim_core import cli_exec, classify, GNB_ATTACH_RE, UE_ATTACH_RE
from ueransim_web.dataplane import CommandError, UDP_PORT_DEFAULT
from ueransim_web.registry import GnbController, UeRegistry

STATIC_DIR = Path(__file__).parent / "static"
LOG_BUFFER_MAX = 50

# The single UE the dashboard manages (key/OP come from the UE config YAML).
UE_NAME = "nr-ue-01"
UE_IMSI_DEFAULT = "999700000000001"

COLOR_GNB = "#7dd3fc"
COLOR_SUCCESS = "#86efac"
COLOR_FAIL = "#fca5a5"

# Rich markup tags are only ever unescaped ("[bold red]", never "\[bold red]") in the
# lines we pass through here — real spdlog output was rich_escape()'d upstream in
# NodeProcess, so a literal "[info]"/"[nr-gnb]" always arrives as "\[info]"/"\[nr-gnb]"
# and is protected by the negative lookbehind below. Only genuine (unescaped) markup
# tags get stripped; escaped brackets are then unescaped back to plain "[", "]".
_TAG_RE = re.compile(r"(?<!\\)\[/?[a-zA-Z ]*\]")


def strip_rich(text: str) -> str:
    text = _TAG_RE.sub("", text)
    return text.replace("\\[", "[")


def make_app(gnb_config: str, ue_config: str, ue_imsi: str = UE_IMSI_DEFAULT,
             udp_port: int = UDP_PORT_DEFAULT) -> web.Application:
    app = web.Application()
    ws_clients: set[web.WebSocketResponse] = set()
    log_buffer: deque = deque(maxlen=LOG_BUFFER_MAX)

    def broadcast(payload: dict):
        dead = []
        for ws in ws_clients:
            if ws.closed:
                dead.append(ws)
                continue
            asyncio.create_task(ws.send_json(payload))
        for ws in dead:
            ws_clients.discard(ws)

    def tag_for(source: str) -> str:
        if source == "gnb":
            return "[gnb]"
        # source is "ue:<id>"
        ue_id = int(source.split(":", 1)[1])
        entry = ues.get(ue_id)
        return f"[{entry.name}]" if entry else f"[{source}]"

    def on_log(source: str, text: str, level: str):
        clean = strip_rich(text)
        if level == "fail":
            color = COLOR_FAIL
        elif source == "gnb":
            color = COLOR_GNB
        else:
            color = COLOR_SUCCESS
        entry = {
            "type": "log",
            "source": source,
            "tag": tag_for(source),
            "color": color,
            "level": level,
            "time": time.strftime("%H:%M:%S"),
            "text": clean,
        }
        log_buffer.append(entry)
        broadcast(entry)

    def on_gnb_state(state: str):
        broadcast({"type": "gnb_state", "state": state})

    def on_ue_state(entry):
        broadcast({"type": "ue_state", **entry.snapshot()})

    def on_ue_dataplane(entry, payload: dict):
        broadcast({"id": entry.id, **payload})

    gnb = GnbController(gnb_config, lambda src, text: on_log(src, text, _classify_level_gnb(gnb, text)), on_gnb_state)
    ues = UeRegistry(ue_config, lambda src, text: on_log(src, text, _classify_level_ue(ues, src, text)), on_ue_state,
                     on_ue_dataplane, udp_port)
    # The dashboard manages exactly one, permanently configured UE — no add/remove.
    ues.add(UE_NAME, ue_imsi)

    app["gnb"] = gnb
    app["ues"] = ues

    def fire_cli(entry, cmd: str, label: str):
        async def _run():
            try:
                out = await cli_exec(entry.node_name, cmd)
                for line in out.splitlines():
                    if line.strip():
                        on_log(f"ue:{entry.id}", f"[cli] {line}", "info")
            except Exception as e:
                on_log(f"ue:{entry.id}", f"[cli] {label} error: {e}", "fail")
        asyncio.create_task(_run())

    # ── gNB routes ──────────────────────────────────────────────────────────
    async def get_gnb(request):
        return web.json_response(gnb.snapshot())

    async def start_gnb(request):
        try:
            await gnb.start()
        except RuntimeError as e:
            return web.json_response({"error": str(e)}, status=409)
        return web.json_response({}, status=202)

    async def stop_gnb(request):
        await gnb.stop()
        return web.json_response({}, status=202)

    # ── UE routes ───────────────────────────────────────────────────────────
    async def list_ues(request):
        return web.json_response(ues.list())

    async def start_ue(request):
        ue_id = int(request.match_info["id"])
        if gnb.state != "attached":
            return web.json_response({"error": "Start gNB first (must be Connected)"}, status=409)
        try:
            await ues.start(ue_id)
        except KeyError:
            return web.json_response({"error": "no such UE"}, status=404)
        except (RuntimeError, PermissionError) as e:
            return web.json_response({"error": str(e)}, status=409)
        return web.json_response({}, status=202)

    async def stop_ue(request):
        ue_id = int(request.match_info["id"])
        try:
            await ues.stop(ue_id)
        except KeyError:
            return web.json_response({"error": "no such UE"}, status=404)
        return web.json_response({}, status=202)

    async def _pdu_action(request, cmd: str, label: str):
        ue_id = int(request.match_info["id"])
        entry = ues.get(ue_id)
        if entry is None:
            return web.json_response({"error": "no such UE"}, status=404)
        if not entry.running:
            return web.json_response({"error": "UE is not running"}, status=409)
        fire_cli(entry, cmd, label)
        return web.json_response({}, status=202)

    async def pdu_list(request):
        return await _pdu_action(request, "ps-list", "ps-list")

    async def pdu_establish(request):
        return await _pdu_action(request, "ps-establish IPv4 --sst 1 --sd 1 --dnn internet", "ps-establish")

    async def pdu_release_all(request):
        return await _pdu_action(request, "ps-release-all", "ps-release-all")

    async def deregister(request):
        return await _pdu_action(request, "deregister switch-off", "deregister")

    # ── data plane: command console + message inbox ─────────────────────────────
    async def run_command(request):
        ue_id = int(request.match_info["id"])
        entry = ues.get(ue_id)
        if entry is None:
            return web.json_response({"error": "no such UE"}, status=404)
        body = await request.json()
        try:
            await entry.run_command(body.get("cmd") or "")
        except CommandError as e:
            return web.json_response({"error": str(e)}, status=400)
        except RuntimeError as e:
            return web.json_response({"error": str(e)}, status=409)
        return web.json_response({}, status=202)

    async def stop_command(request):
        ue_id = int(request.match_info["id"])
        entry = ues.get(ue_id)
        if entry is None:
            return web.json_response({"error": "no such UE"}, status=404)
        await entry.stop_command()
        return web.json_response({}, status=202)

    async def get_console(request):
        ue_id = int(request.match_info["id"])
        entry = ues.get(ue_id)
        if entry is None:
            return web.json_response({"error": "no such UE"}, status=404)
        return web.json_response({"busy": entry.console.busy, "lines": list(entry.console.buffer)})

    async def get_udp(request):
        ue_id = int(request.match_info["id"])
        entry = ues.get(ue_id)
        if entry is None:
            return web.json_response({"error": "no such UE"}, status=404)
        return web.json_response({
            "ip": entry.inbox.bound_ip,
            "port": entry.inbox.port,
            "listening": entry.inbox.listening,
            "httpListening": entry.inbox.http_listening,
            "messages": list(entry.inbox.buffer),
        })

    # ── WebSocket ───────────────────────────────────────────────────────────
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        ws_clients.add(ws)
        await ws.send_json({
            "type": "snapshot",
            "gnb": gnb.snapshot(),
            "ues": ues.list(),
            "logs": list(log_buffer),
            "console": {e.id: list(e.console.buffer) for e in ues.entries.values()},
            "udp": {e.id: list(e.inbox.buffer) for e in ues.entries.values()},
        })
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            ws_clients.discard(ws)
        return ws

    async def index(request):
        return web.FileResponse(STATIC_DIR / "index.html")

    async def on_shutdown(app):
        # Close WS connections server-side first — the client-driven `async for
        # msg in ws` loop in ws_handler otherwise blocks aiohttp's shutdown
        # (default 60s timeout) waiting for a close frame that a passive log
        # listener never sends, which can push total shutdown past systemd's
        # TimeoutStopSec and get the process SIGKILLed mid-cleanup.
        await asyncio.gather(
            *(ws.close(code=WSCloseCode.GOING_AWAY, message=b"server shutdown")
              for ws in list(ws_clients)),
            return_exceptions=True,
        )
        await gnb.stop()
        await ues.stop_all()

    app.router.add_get("/", index)
    app.router.add_get("/api/gnb", get_gnb)
    app.router.add_post("/api/gnb/start", start_gnb)
    app.router.add_post("/api/gnb/stop", stop_gnb)
    app.router.add_get("/api/ues", list_ues)
    app.router.add_post("/api/ues/{id}/start", start_ue)
    app.router.add_post("/api/ues/{id}/stop", stop_ue)
    app.router.add_get("/api/ues/{id}/pdu", pdu_list)
    app.router.add_post("/api/ues/{id}/pdu/establish", pdu_establish)
    app.router.add_post("/api/ues/{id}/pdu/release-all", pdu_release_all)
    app.router.add_post("/api/ues/{id}/deregister", deregister)
    app.router.add_get("/api/ues/{id}/exec", get_console)
    app.router.add_post("/api/ues/{id}/exec", run_command)
    app.router.add_post("/api/ues/{id}/exec/stop", stop_command)
    app.router.add_get("/api/ues/{id}/udp", get_udp)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static", STATIC_DIR)
    app.on_shutdown.append(on_shutdown)

    return app


# classify() needs the "is this a fail/attach/info line" verdict per node kind; these
# small wrappers keep GnbController/UeRegistry's own state machine untouched (registry.py
# already calls classify() itself to update .state) while letting the log broadcaster
# derive the same verdict for line coloring, from the *pre-transition* state.
def _classify_level_gnb(gnb, text: str) -> str:
    new_state = classify(gnb.state, text, GNB_ATTACH_RE, gnb.stopping)
    if new_state == "failed":
        return "fail"
    if new_state == "attached":
        return "success"
    return "info"


def _classify_level_ue(ues, source: str, text: str) -> str:
    ue_id = int(source.split(":", 1)[1])
    entry = ues.get(ue_id)
    if entry is None:
        return "info"
    new_state = classify(entry.state, text, UE_ATTACH_RE, entry.stopping)
    if new_state == "failed":
        return "fail"
    if new_state == "attached":
        return "success"
    return "info"
