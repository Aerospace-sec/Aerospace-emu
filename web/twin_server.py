"""Read-only local HTTP surface for the simulation device twin."""
from __future__ import annotations
import argparse, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from sim.twin.adapters import populate_virtual_registry
from sim.twin.model import DeviceRegistry, to_json
from sim.twin.registry import load_registry
from sim.twin.store import TwinStore
from sim.twin.chain import run_cross_model_chain

ROOT = Path(__file__).parent

class TwinHandler(BaseHTTPRequestHandler):
    registry: DeviceRegistry
    store: TwinStore
    page_root: Path = ROOT
    def _send(self, status: int, payload, content_type="application/json"):
        body = payload.encode() if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        parsed = urlparse(self.path)
        now = int(time.time() * 1000)
        if parsed.path == "/api/health":
            return self._send(200, {"status":"ok", "schema_version":1, "simulation_only":True, "clock_ms":now})
        if parsed.path == "/api/devices":
            return self._send(200, {"devices":[to_json(s) for s in self.registry.snapshots(now)]})
        if parsed.path == "/api/topology":
            return self._send(200, {"devices":[to_json(d) for d in self.registry.devices.values()], "connections":[to_json(c) for c in self.registry.connections.values()]})
        if parsed.path == "/api/events":
            try: cursor = int(parse_qs(parsed.query).get("after", ["0"])[0])
            except ValueError: return self._send(400, {"error":{"code":"invalid_cursor","message":"after must be an integer"}})
            return self._send(200, {"events":self.store.events_since(cursor)})
        if parsed.path == "/api/chain":
            scenario = parse_qs(parsed.query).get("scenario", ["normal"])[0]
            try:
                return self._send(200, run_cross_model_chain(scenario=scenario))
            except ValueError as exc:
                return self._send(400, {"error":{"code":"invalid_scenario","message":str(exc)}})
        if parsed.path == "/":
            return self._send(200, (self.page_root / "index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
        if parsed.path in ("/app.js", "/style.css"):
            file = self.page_root / parsed.path.lstrip("/")
            return self._send(200, file.read_text(encoding="utf-8"), "text/javascript" if parsed.path.endswith("js") else "text/css")
        self._send(404, {"error":{"code":"not_found","message":"unknown path"}})
    def do_POST(self): self._send(405, {"error":{"code":"method_not_allowed","message":"read-only API"}})
    def log_message(self, *_): pass

def create_server(registry: DeviceRegistry, store: TwinStore, host="127.0.0.1", port=8765):
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("twin server only permits loopback binding")
    TwinHandler.registry, TwinHandler.store = registry, store
    return ThreadingHTTPServer((host, port), TwinHandler)

def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--registry", default="config/twin_devices.json"); parser.add_argument("--database", default="artifacts/twin-runtime/twin.sqlite3"); parser.add_argument("--port", type=int, default=8765); parser.add_argument("--scenario", default="none", choices=("none","injection","replay","tamper","flood","rate_mismatch")); args = parser.parse_args(argv)
    registry = load_registry(args.registry); populate_virtual_registry(registry, scenario=args.scenario, now_ms=int(time.time()*1000)); store = TwinStore(args.database); store.save_registry(registry, int(time.time()*1000)); server = create_server(registry, store, port=args.port); print(f"Twin server listening on http://127.0.0.1:{args.port}"); server.serve_forever()

if __name__ == "__main__": main()
