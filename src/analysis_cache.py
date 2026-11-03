"""Content-addressed feature cache. No labels, quality flags or raw records live here."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3


class FeatureCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS features_v1 (key TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)")
        self.connection.commit()
        self.hits = self.misses = self.pending = 0

    def get(self, key: str) -> dict | None:
        row = self.connection.execute("SELECT payload,checksum FROM features_v1 WHERE key=?",(key,)).fetchone()
        if row:
            try:
                if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:raise ValueError("checksum")
                value = json.loads(row[0])
                json.dumps(value,allow_nan=False)
                if not isinstance(value,dict):raise ValueError("shape")
            except (ValueError,TypeError):
                self.connection.execute("DELETE FROM features_v1 WHERE key=?",(key,))
            else:
                self.hits += 1
                return value
        self.misses += 1
        return None

    def put(self, key: str, features: dict) -> None:
        payload=json.dumps(features,sort_keys=True,separators=(',',':'),allow_nan=False)
        self.connection.execute("INSERT OR REPLACE INTO features_v1 VALUES(?,?,?)",(key,payload,hashlib.sha256(payload.encode()).hexdigest()))
        self.pending += 1
        if self.pending >= 128:self.connection.commit();self.pending=0

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()

    def __enter__(self):return self
    def __exit__(self,*_):self.close()
