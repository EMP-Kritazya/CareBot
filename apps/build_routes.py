"""Precompute every neighbor's walking route to Sandy's home, once, into data/routes.json.

    python -m apps.build_routes             # foot routes from the FOSSGIS OSRM server (OpenStreetMap)
    python -m apps.build_routes --offline   # straight lines only (no network)

Nothing calls a routing service during the demo: the robot ranks neighbors and the map draws
routes from this file. A neighbor with no route falls back to a straight line (and an estimate).
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

from carebot.escalate.receivers import DETOUR, WALK_M_PER_S, load_receivers, straight_m

OSRM = "https://routing.openstreetmap.de/routed-foot/route/v1/driving/{a};{b}?overview=full&geometries=geojson"
UA = "CareBot-hackathon-route-builder/1.0"


def osrm_route(frm: tuple[float, float], to: tuple[float, float]) -> dict | None:
    url = OSRM.format(a=f"{frm[1]},{frm[0]}", b=f"{to[1]},{to[0]}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"    OSRM failed: {e}")
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    route = data["routes"][0]
    snap = [w.get("distance", 0) for w in data.get("waypoints", [])]
    return {"distance_m": round(route["distance"]), "walk_s": round(route["duration"]),
            "geometry": [[round(lat, 6), round(lng, 6)] for lng, lat in route["geometry"]["coordinates"]],
            "snap_m": [round(s) for s in snap], "source": "osrm-foot"}


def straight(frm: tuple[float, float], to: tuple[float, float]) -> dict:
    d = straight_m(frm, to)
    return {"distance_m": round(d * DETOUR), "walk_s": round(d * DETOUR / WALK_M_PER_S),
            "geometry": [list(frm), list(to)], "snap_m": [0, 0], "source": "straight-line"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--receivers", default="config/receivers.yaml")
    ap.add_argument("--out", default="data/routes.json")
    ap.add_argument("--offline", action="store_true", help="straight lines only")
    args = ap.parse_args()

    cfg = load_receivers(args.receivers)
    home = cfg["home"]
    if not home:
        raise SystemExit(f"No home in {args.receivers}")
    to = (home["lat"], home["lng"])
    routes = {}
    for r in cfg["receivers"]:
        frm = (r.lat, r.lng)
        route = None if args.offline else osrm_route(frm, to)
        if route is None:
            route = straight(frm, to)
        else:
            time.sleep(1)  # be polite to the free server
        warn = "  <- check this position, it's far from a street" if max(route["snap_m"]) > 60 else ""
        print(f"  {r.name:14} {route['walk_s'] / 60:4.1f} min  {route['distance_m']:5} m  ({route['source']}){warn}")
        routes[r.id] = route
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"home": home, "built": time.strftime("%Y-%m-%d %H:%M"),
                               "credit": "Routes: OSRM (FOSSGIS) on OpenStreetMap data, (c) OpenStreetMap contributors",
                               "routes": routes}, indent=1))
    print(f"Saved {len(routes)} routes to {out}")


if __name__ == "__main__":
    main()
