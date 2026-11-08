"""The laptop map: Sandy's home, the neighbors who agreed to help, and the live incident.

    python -m dashboard.server --mqtt carebot.local     # the real setup: subscribe to the robot's broker
    python -m dashboard.server --mqtt localhost         # a broker on this laptop (brew install mosquitto)
    python -m apps.watcher --webcam 1 --map             # or: watcher and map in one process (no broker)

Then open http://localhost:8050. The page shows what the robot sends (JSON events only: never
video or faces), simulates the neighbors who have no real phone, and has Trigger alert / Reset.

FastAPI + WebSocket; the page is Leaflet 1.9.4 on OpenStreetMap / CARTO tiles.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # module level: FastAPI reads these annotations
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from carebot.escalate.receivers import SimReceivers, load_receivers, load_routes, rank, walk_s

STATIC = Path(__file__).parent / "static"
HISTORY = 400          # messages kept for a page opened (or refreshed) mid-incident
COMMANDS = ("trigger", "reset")


class Hub:
    """Fans every robot message out to the open pages (thread-safe: MQTT calls in on its own thread)."""

    def __init__(self) -> None:
        self.history: collections.deque = collections.deque(maxlen=HISTORY)
        self.clients: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def push(self, topic: str, msg: dict) -> None:
        item = {"topic": topic, "msg": msg}
        with self._lock:
            if msg.get("type") != "heartbeat":
                self.history.append(item)
            else:
                self.last_heartbeat = item
            clients = list(self.clients)
        if self.loop is None:
            return
        for q in clients:
            self.loop.call_soon_threadsafe(q.put_nowait, item)

    def backlog(self) -> list[dict]:
        with self._lock:
            out = list(self.history)
            hb = getattr(self, "last_heartbeat", None)
        return out + ([hb] if hb else [])


def map_config(receivers_path: str, routes_path: str, profile: dict, unit: str) -> dict:
    rc = load_receivers(receivers_path)
    routes = load_routes(routes_path)
    home = rc["home"] or {"lat": 40.8077, "lng": -73.9661, "label": "Home"}
    ranked = rank(home, rc["receivers"], routes)
    return {
        "unit": unit,
        "resident": profile.get("resident_name", "the resident"),
        "family": profile.get("family_name", "Family"),
        "home": home,
        "waves": rc["waves"],
        "receivers": [{"id": r.id, "name": r.name, "street": r.street, "lat": r.lat, "lng": r.lng, "note": r.note,
                       "channel": r.channel, "rank": i + 1, "walk_s": round(walk_s(home, r, routes))}
                      for i, r in enumerate(ranked)],
        "routes": {rid: {"geometry": v.get("geometry"), "walk_s": v.get("walk_s"), "distance_m": v.get("distance_m"),
                         "source": v.get("source")} for rid, v in routes.items()},
    }


def create_app(transport, cfg: dict, receivers_path: str = "config/receivers.yaml",
               routes_path: str = "data/routes.json", simulate: bool = True, yes_after_s: float | None = None):
    hub = Hub()
    for name in ("event", "receivers"):
        transport.subscribe(name, lambda m, n=name: hub.push(n, m))
    sim = None
    if simulate:
        sim = SimReceivers(load_receivers(receivers_path)["receivers"],
                           reply=lambda rid, ans: transport.publish("cmd", {"type": "receiver_reply", "id": rid,
                                                                             "reply": ans}),
                           yes_after_s=yes_after_s)
        transport.subscribe("receivers", sim.on_receivers)
    conf = map_config(receivers_path, routes_path, cfg.get("profile", {}), cfg.get("unit_id", "robot-4A"))
    conf["link"] = transport.name
    conf["simulated"] = simulate
    conf["relation"] = cfg.get("profile", {}).get("family_relation", "family")
    conf["texts"] = cfg.get("twilio", {}).get("texts", "sms")

    app = FastAPI(title="CareBot map", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/config")
    async def config():
        return JSONResponse(conf)

    @app.post("/api/cmd/{kind}")
    async def cmd(kind: str):
        if kind not in COMMANDS:
            return JSONResponse({"ok": False, "error": "unknown command"}, status_code=400)
        transport.publish("cmd", {"type": kind})
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        hub.loop = asyncio.get_running_loop()  # the server's loop: MQTT threads hand messages to it
        q: asyncio.Queue = asyncio.Queue()
        with hub._lock:
            hub.clients.add(q)
        try:
            for item in hub.backlog():
                await sock.send_text(json.dumps(item))
            while True:
                item = await q.get()
                await sock.send_text(json.dumps(item))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            with hub._lock:
                hub.clients.discard(q)

    app.state.hub, app.state.sim = hub, sim
    return app


def serve_in_thread(transport, cfg: dict, port: int = 8050, host: str = "127.0.0.1",
                    yes_after_s: float | None = None) -> threading.Thread:
    """Run the map inside another app (e.g. the watcher, on the in-memory transport)."""
    import uvicorn

    app = create_app(transport, cfg, yes_after_s=yes_after_s)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True, name="map")
    t.start()
    return t


def main() -> None:
    import uvicorn

    from carebot.config import load_config
    from carebot.net.transport import MqttTransport

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt", default="carebot.local", help="the robot's MQTT broker (default carebot.local)")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--config", default="config/mock.yaml")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to open the map from another device")
    ap.add_argument("--no-sim", action="store_true", help="don't simulate neighbors (real replies only)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    transport = MqttTransport(args.mqtt, args.mqtt_port, cfg.get("unit_id", "robot-4A"), client_id="carebot-map")
    if transport.wait_connected(5):
        print(f"Map: connected to the robot's broker at {args.mqtt}:{args.mqtt_port}", flush=True)
    else:
        print(f"Map: no broker at {args.mqtt}:{args.mqtt_port} yet (retrying). Is the robot on our router?", flush=True)
    app = create_app(transport, cfg, simulate=not args.no_sim)
    print(f"Map: open http://localhost:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
