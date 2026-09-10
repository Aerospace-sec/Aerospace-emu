"""SQLite persistence for twin snapshots and ordered events."""
from __future__ import annotations
import json, sqlite3
from pathlib import Path
from .model import DeviceRegistry, StateEvent, to_json

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_state(device_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS desired_state(device_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state_events(sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL, payload TEXT NOT NULL);
"""

class TwinStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.executescript(_SCHEMA)
            self.db.commit()
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError(f"cannot open twin database {self.path}: {exc}") from exc

    def save_registry(self, registry: DeviceRegistry, now_ms: int) -> None:
        with self.db:
            for device_id, observed in registry.observed.items():
                self.db.execute("INSERT OR REPLACE INTO device_state VALUES (?, ?)", (device_id, json.dumps(to_json(observed), sort_keys=True)))
            for device_id, desired in registry.desired.items():
                self.db.execute("INSERT OR REPLACE INTO desired_state VALUES (?, ?)", (device_id, json.dumps(to_json(desired), sort_keys=True)))
            for event in registry.events:
                self.db.execute("INSERT OR IGNORE INTO state_events VALUES (?, ?, ?)", (event.sequence, event.event_id, json.dumps(to_json(event), sort_keys=True)))
            payload = {"devices": {k: to_json(v) for k, v in registry.observed.items()}, "events": [to_json(e) for e in registry.events]}
            self.db.execute("INSERT INTO snapshots(created_at, payload) VALUES (?, ?)", (now_ms, json.dumps(payload, sort_keys=True)))

    def load_latest_snapshot(self) -> dict:
        row = self.db.execute("SELECT payload FROM snapshots ORDER BY snapshot_id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else {"devices": {}, "events": []}

    def events_since(self, cursor: int = 0) -> list[dict]:
        rows = self.db.execute("SELECT payload FROM state_events WHERE sequence > ? ORDER BY sequence", (cursor,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self) -> None:
        self.db.close()
