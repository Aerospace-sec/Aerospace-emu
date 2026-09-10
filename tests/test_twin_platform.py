import json
import threading
import urllib.request

from sim.twin.adapters import populate_virtual_registry
from sim.twin.model import DeviceRegistry, DeviceSpec, InterfaceSpec, ObservedState, TwinValidationError
from sim.twin.registry import load_registry
from sim.twin.store import TwinStore
from web.twin_server import create_server


def test_registry_supports_new_device_and_rejects_unknown_connection():
    registry = DeviceRegistry()
    registry.register_device(DeviceSpec("new", "New", "sensor", "lab", interfaces=(InterfaceSpec("p", "bus"),)))
    assert registry.get_device("new").simulation_only
    try:
        registry.register_connection(__import__("sim.twin.model", fromlist=["ConnectionSpec"]).ConnectionSpec("bad", "new", "p", "missing", "p", "x"))
    except TwinValidationError:
        pass
    else:
        raise AssertionError("unknown endpoint accepted")


def test_observation_expires_and_drift_is_explicit():
    registry = DeviceRegistry(stale_after_ms=10)
    registry.register_device(DeviceSpec("d", "D", "sensor", "lab"))
    registry.upsert_observed_state("d", ObservedState({"link": "up"}, 0, "virtual_model", sequence=1))
    registry.set_desired_state("d", __import__("sim.twin.model", fromlist=["DesiredState"]).DesiredState({"link": "down"}))
    snapshot = registry.snapshot("d", 11)
    assert snapshot.freshness == "stale"
    assert snapshot.reconciliation.status == "drifted"


def test_virtual_registry_and_sqlite_round_trip(tmp_path):
    registry = load_registry("config/twin_devices.json")
    populate_virtual_registry(registry, now_ms=100)
    assert registry.observed["V429-LAB-2T4R"].source == "virtual_model"
    store = TwinStore(tmp_path / "twin.sqlite3")
    store.save_registry(registry, 100)
    assert store.load_latest_snapshot()["devices"]["PX4-FMU-VIRTUAL"]["source"] == "virtual_model"
    assert store.events_since(0)
    store.close()


def test_read_only_api_exposes_topology_and_page(tmp_path):
    registry = load_registry("config/twin_devices.json")
    populate_virtual_registry(registry)
    store = TwinStore(tmp_path / "api.sqlite3"); store.save_registry(registry, 0)
    server = create_server(registry, store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    topology = json.loads(urllib.request.urlopen(base + "/api/topology").read())
    assert any(d["device_id"] == "V429-LAB-2T4R" for d in topology["devices"])
    page = urllib.request.urlopen(base + "/").read().decode()
    assert "SIMULATION" in page
    request = urllib.request.Request(base + "/api/devices", method="POST")
    try:
        urllib.request.urlopen(request)
    except urllib.error.HTTPError as exc:
        assert exc.code == 405
    else:
        raise AssertionError("write endpoint accepted")
    server.shutdown(); server.server_close(); store.close()
