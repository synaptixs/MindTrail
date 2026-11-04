#!/usr/bin/env python3
"""Local AWEAR EEG collector and activity-label dashboard. No third-party packages."""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import ssl
import sys
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import daily_review
import episode_review
import personal_activities
from analysis_manager import AnalysisManager
from bluetooth_manager import BluetoothManager
from archive_paths import ArchivePaths

SOURCE_ROOT = Path(__file__).resolve().parent
ROOT = SOURCE_ROOT if getattr(sys, "frozen", False) else SOURCE_ROOT.parent
DATA_ROOT = Path(os.environ["AWEAR_DATA_DIR"]).expanduser().resolve() if os.environ.get("AWEAR_DATA_DIR") else None
DB_PATH = (DATA_ROOT or ROOT) / "awear_data.sqlite3"
API_BASE = "https://awearlabs.com/api/v1"
POLL_SECONDS = 30
OVERLAP_MS = 5 * 60 * 1000
RAW_WINDOW_MS = 60 * 60 * 1000
RAW_REQUEST_INTERVAL_SECONDS = 1.05
MAX_BODY = 16_384
ACTIVITY_INTERVAL_MS = 30 * 60 * 1000
ACTIVITY_INTERVAL_PAGE_SIZE = 48
RAW_TABLE_PAGE_SIZE = 20
EEG_WINDOW_SECONDS = (10, 30, 60)
MOBILE_STATES = ("alert", "engaged", "relaxed", "calm")
MOBILE_SCORE_PAGE_SIZE = 50


def data_root() -> Path:
    """Keep writable archives separate from bundled, read-only application assets."""
    return DATA_ROOT or ROOT


def configure_storage(directory: Path) -> None:
    global DATA_ROOT, DB_PATH
    DATA_ROOT = directory.expanduser().resolve()
    DATA_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    DB_PATH = DATA_ROOT / "awear_data.sqlite3"


def utc_ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return daily_review.epoch(value)


def iso(ms: int) -> str:
    return (datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_csv(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def read_api_key(env_path: Path | None = None) -> str:
    """Use the process environment first, then the local .env key if present."""
    value = os.getenv("AWEAR_API_KEY", "").strip()
    if value:
        return value
    path = env_path or data_root() / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, raw = line.partition("=")
        if not separator or name.strip() != "AWEAR_API_KEY":
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        return value.strip()
    return ""


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=15)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        with connection:
            yield connection
    finally:
        connection.close()


def setup_db() -> None:
    with db() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS readings (
                participant_id TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                device_id TEXT,
                waveform_json TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                mean REAL NOT NULL,
                rms REAL NOT NULL,
                peak_to_peak REAL NOT NULL,
                PRIMARY KEY (participant_id, timestamp_ms)
            );
            CREATE INDEX IF NOT EXISTS readings_time ON readings(timestamp_ms);
            CREATE TABLE IF NOT EXISTS labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id TEXT NOT NULL,
                label TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                start_ms INTEGER NOT NULL,
                end_ms INTEGER,
                CHECK (end_ms IS NULL OR end_ms > start_ms)
            );
            CREATE INDEX IF NOT EXISTS labels_time ON labels(participant_id, start_ms, end_ms);
            CREATE TABLE IF NOT EXISTS mobile_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id TEXT NOT NULL,
                observed_ms INTEGER NOT NULL,
                score REAL NOT NULL CHECK(score >= 0 AND score <= 100),
                state TEXT NOT NULL CHECK(state IN ('alert','engaged','relaxed','calm')),
                uncertainty_seconds INTEGER NOT NULL DEFAULT 0,
                app_version TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_ms INTEGER NOT NULL,
                updated_ms INTEGER NOT NULL,
                UNIQUE(participant_id, observed_ms)
            );
        """)
        if "device_id" not in {row[1] for row in connection.execute("PRAGMA table_info(readings)")}:
            connection.execute("ALTER TABLE readings ADD COLUMN device_id TEXT")
        label_columns = {row[1] for row in connection.execute("PRAGMA table_info(labels)")}
        for name, definition in (("source", "TEXT NOT NULL DEFAULT 'manual'"),
                                 ("reviewed", "INTEGER NOT NULL DEFAULT 1"),
                                 ("batch_id", "TEXT")):
            if name not in label_columns:
                connection.execute(f"ALTER TABLE labels ADD COLUMN {name} {definition}")

        personal_activities.migrate(connection, DB_PATH, data_root())
        episode_review.migrate(connection, DB_PATH, data_root())


def setting(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def put_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


class Collector:
    def __init__(self, key: str, demo: bool = False):
        self.key = key
        self.demo = demo
        self.lock = threading.Lock()
        self.running = False
        self.last_error = ""
        self.last_sync_ms = 0
        self.inserted_last = 0
        self.stop_event = threading.Event()
        self.last_raw_request_at = 0.0
        self.enabled = True

    def request(self, path: str, params: dict | None = None) -> dict:
        if not self.key:
            raise RuntimeError("API key is not configured. Add AWEAR_API_KEY to .env or restart with --key-prompt.")
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={"Authorization": "Bearer " + self.key, "Accept": "application/json"},
        )
        try:
            # Frozen Python must not depend on the build machine's CA directory.
            context = None
            if getattr(sys, "frozen", False) or os.environ.get("AWEAR_BUNDLED_RUNTIME") == "1":
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(request, timeout=35, context=context) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(1024).decode("utf-8", "replace")
            try:
                body = json.loads(detail)
                message = body.get("error", {}).get("message") or body.get("error", {}).get("code")
            except (ValueError, AttributeError):
                message = None
            if error.code == 429:
                retry = error.headers.get("Retry-After", "60")
                raise RuntimeError(f"AWEAR rate or quota limit (429). Retry-After: {retry}s") from error
            raise RuntimeError(f"AWEAR HTTP {error.code}: {message or 'request failed'}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"AWEAR connection failed: {error.reason}") from error

    def participants(self) -> list[dict]:
        if self.demo:
            return [{"participantId": "P-DEMO01", "status": "sample", "accessState": "sample"}]
        return self.request("/members").get("members", [])

    def start_sync(self) -> bool:
        with self.lock:
            if self.running or not self.enabled or self.stop_event.is_set():
                return False
            self.running = True
        threading.Thread(target=self._sync, daemon=True).start()
        return True

    def _sync(self) -> None:
        try:
            self.inserted_last = self.sync()
            self.last_error = ""
            self.last_sync_ms = int(time.time() * 1000)
        except Exception as error:
            self.last_error = str(error)
        finally:
            with self.lock:
                self.running = False

    def sync(self) -> int:
        if self.demo:
            return 0
        with db() as connection:
            participant = setting(connection, "participant_id")
            start_ms = int(setting(connection, "sync_start_ms", "0"))
            if not participant or not start_ms:
                return 0
            # Old databases have no checkpoint. Rescan them from the selected start so
            # a previous cursor failure cannot leave an unnoticed hole.
            checkpoint_ms = int(setting(connection, "sync_checkpoint_ms", str(start_ms)))
        start_ms = max(start_ms, checkpoint_ms - OVERLAP_MS)
        end_ms = int(time.time() * 1000)
        if start_ms >= end_ms:
            return 0
        inserted = 0
        window_start = start_ms
        while window_start < end_ms and not self.stop_event.is_set():
            window_end = min(window_start + RAW_WINDOW_MS, end_ms)
            fetch_start = max(start_ms, window_start - 1000)
            rows = self._fetch_complete_window(participant, fetch_start, window_end)
            with db() as connection:
                before = connection.total_changes
                connection.executemany("""
                    INSERT OR IGNORE INTO readings
                    (participant_id, timestamp_ms, device_id, waveform_json, sample_count, mean, rms, peak_to_peak)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, rows)
                inserted += connection.total_changes - before
                # Advance only after every subwindow is complete and committed.
                put_setting(connection, "sync_checkpoint_ms", str(window_end))
            window_start = window_end
        return inserted

    def _pace_raw_request(self) -> None:
        elapsed = time.monotonic() - self.last_raw_request_at
        if self.last_raw_request_at and elapsed < RAW_REQUEST_INTERVAL_SECONDS:
            time.sleep(RAW_REQUEST_INTERVAL_SECONDS - elapsed)
        self.last_raw_request_at = time.monotonic()

    def _fetch_complete_window(self, participant: str, start_ms: int, end_ms: int) -> list[tuple]:
        self._pace_raw_request()
        params = {"start": iso(start_ms), "end": iso(end_ms), "limit": 5000, "sort": "asc"}
        result = self.request("/members/" + urllib.parse.quote(participant, safe="") + "/data", params)
        items = result.get("data", [])
        pagination = result.get("pagination") or {}
        if pagination.get("hasMore") or len(items) >= 5000:
            if end_ms - start_ms <= 1000:
                raise RuntimeError("AWEAR reports additional raw records within a one-second window; "
                                   "this window cannot be downloaded completely")
            midpoint = start_ms + (end_ms - start_ms) // 2
            # Overlap at the boundary; the SQLite primary key deduplicates it.
            return (self._fetch_complete_window(participant, start_ms, midpoint)
                    + self._fetch_complete_window(participant, max(start_ms, midpoint - 1000), end_ms))
        rows = []
        for item in items:
            waveform = item.get("waveform")
            if not isinstance(waveform, list) or not waveform or not all(
                isinstance(x, (int, float)) and math.isfinite(x) for x in waveform
            ):
                continue
            timestamp_ms = utc_ms(item["timestamp"])
            if not start_ms - 1000 <= timestamp_ms <= end_ms + 1000:
                raise RuntimeError("AWEAR returned a raw record outside the requested time window")
            mean = statistics.fmean(waveform)
            rms = math.sqrt(statistics.fmean(x * x for x in waveform))
            rows.append((participant, timestamp_ms, item.get("device_id"), json.dumps(waveform, separators=(",", ":")),
                         len(waveform), mean, rms, max(waveform) - min(waveform)))
        return rows

    def loop(self) -> None:
        while not self.stop_event.wait(POLL_SECONDS):
            self.start_sync()


def demo_seed() -> None:
    now = int(time.time() * 1000) // 1000 * 1000
    start = now - 4 * 60 * 60 * 1000
    with db() as connection:
        put_setting(connection, "participant_id", "P-DEMO01")
        put_setting(connection, "sync_start_ms", str(start))
        if connection.execute("SELECT COUNT(*) FROM readings WHERE participant_id='P-DEMO01'").fetchone()[0]:
            return
        rows = []
        for second in range(4 * 60 * 60):
            timestamp_ms = start + second * 1000
            if second % 143 < 9:  # visible gaps in sample coverage
                continue
            level = 9 + 4 * (second >= 3600) + 3 * (second >= 9000)
            waveform = [round(level * math.sin(2 * math.pi * 10 * i / 256 + second * .1)
                              + 3 * math.sin(2 * math.pi * 3 * i / 256), 3) for i in range(256)]
            rows.append(("P-DEMO01", timestamp_ms, json.dumps(waveform, separators=(",", ":")), 256,
                         statistics.fmean(waveform), math.sqrt(statistics.fmean(x*x for x in waveform)),
                         max(waveform)-min(waveform)))
        connection.executemany("""INSERT OR IGNORE INTO readings
            (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
            VALUES (?,?,?,?,?,?,?)""", rows)
        connection.executemany("INSERT INTO labels(participant_id,label,note,start_ms,end_ms) VALUES (?,?,?,?,?)", [
            ("P-DEMO01", "Reading", "Sample activity", start + 20*60_000, start + 75*60_000),
            ("P-DEMO01", "Walk", "Sample activity", start + 90*60_000, start + 140*60_000),
            ("P-DEMO01", "Focused work", "Sample activity", start + 160*60_000, start + 220*60_000),
        ])


def summary(participant: str, start_ms: int, end_ms: int) -> dict:
    bucket_ms = max(1000, math.ceil((end_ms - start_ms) / 240 / 1000) * 1000)
    with db() as connection:
        total = connection.execute("""SELECT COUNT(*), MIN(timestamp_ms), MAX(timestamp_ms), AVG(rms),
            AVG(rms*rms-mean*mean)
            FROM readings WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<?""",
            (participant, start_ms, end_ms)).fetchone()
        series = connection.execute("""SELECT (timestamp_ms / ?) * ? AS bucket, COUNT(*) AS records,
            AVG(rms) AS rms, AVG(rms*rms-mean*mean) AS ac_power
            FROM readings WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<?
            GROUP BY bucket ORDER BY bucket""",
            (bucket_ms, bucket_ms, participant, start_ms, end_ms)).fetchall()
        labels = connection.execute("""SELECT * FROM labels
            WHERE participant_id=? AND start_ms<? AND (end_ms IS NULL OR end_ms>?) ORDER BY start_ms DESC""",
            (participant, end_ms, start_ms)).fetchall()
        activities = connection.execute("""SELECT CASE WHEN u.id IS NOT NULL AND l.id IS NULL THEN 'Activity unknown (confirmed)' ELSE l.label END AS label,
            CASE WHEN u.id IS NOT NULL AND l.id IS NULL THEN 'manual' ELSE l.source END AS source,
            CASE WHEN u.id IS NOT NULL AND l.id IS NULL THEN 'confirmed' ELSE l.review_state END AS reviewState, (u.id IS NOT NULL AND l.id IS NULL) AS unknown, COUNT(*) AS records, AVG(r.rms) AS rms,
            AVG(r.rms*r.rms-r.mean*r.mean) AS ac_power,
            AVG(r.peak_to_peak) AS peak_to_peak FROM readings r LEFT JOIN labels l
            ON r.participant_id=l.participant_id AND r.timestamp_ms>=l.start_ms
            AND (l.end_ms IS NULL OR r.timestamp_ms<l.end_ms)
            LEFT JOIN episode_decisions u ON r.participant_id=u.participant_id AND u.kind='unknown'
            AND r.timestamp_ms>=u.start_ms AND r.timestamp_ms<u.end_ms
            WHERE r.participant_id=? AND r.timestamp_ms>=? AND r.timestamp_ms<?
            GROUP BY 1,2,3,4 ORDER BY records DESC,label IS NULL,label,source,reviewState""", (participant, start_ms, end_ms)).fetchall()
        last = connection.execute("""SELECT timestamp_ms,waveform_json FROM readings
            WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<? ORDER BY timestamp_ms DESC LIMIT 1""",
            (participant, start_ms, end_ms)).fetchone()
        all_time = connection.execute("SELECT COUNT(*), MAX(timestamp_ms) FROM readings WHERE participant_id=?",
                                      (participant,)).fetchone()
    return {
        "participantId": participant,
        "window": {"start": iso(start_ms), "end": iso(end_ms), "bucketMs": bucket_ms},
        "stats": {"records": total[0], "first": iso(total[1]) if total[1] else None,
                  "last": iso(total[2]) if total[2] else None, "meanRms": total[3],
                  "acRms": math.sqrt(max(0, total[4])) if total[4] is not None else None,
                  "coveragePct": round(100 * total[0] / max(1, (end_ms-start_ms)/1000), 1),
                  "allTimeRecords": all_time[0], "allTimeLast": iso(all_time[1]) if all_time[1] else None},
        "series": [{"time": iso(row[0]), "records": row[1], "rms": row[2],
                    "acRms": math.sqrt(max(0, row[3])) if row[3] is not None else None}
                   for row in series],
        "labels": [public_label(row) for row in labels],
        "activities": [{**dict(row), "label": row["label"] if row["label"] is not None else "No activity assigned",
                        "unassigned": row["label"] is None, "acRms": math.sqrt(max(0, row["ac_power"]))}
                       for row in activities],
        "waveform": {"time": iso(last[0]), "samples": json.loads(last[1])} if last else None,
    }


def eeg_window(participant: str, start_ms: int | None, seconds: int) -> dict:
    """Return a bounded raw signal window with activity intervals and preserved gaps."""
    if seconds not in EEG_WINDOW_SECONDS:
        raise ValueError("EEG window must be 10, 30, or 60 seconds")
    with db() as connection:
        if start_ms is None:
            latest = connection.execute("SELECT MAX(timestamp_ms) FROM readings WHERE participant_id=?",
                                        (participant,)).fetchone()[0]
            start_ms = latest - (seconds - 1) * 1000 if latest is not None else int(time.time() * 1000) - seconds * 1000
        end_ms = start_ms + seconds * 1000
        rows = connection.execute("""SELECT timestamp_ms,device_id,waveform_json
            FROM readings WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<?
            ORDER BY timestamp_ms""", (participant, start_ms, end_ms)).fetchall()
        labels = connection.execute("""SELECT label,start_ms,end_ms FROM labels
            WHERE participant_id=? AND start_ms<? AND (end_ms IS NULL OR end_ms>?)
            ORDER BY start_ms""", (participant, end_ms, start_ms)).fetchall()
    return {"participantId": participant, "channel": "RIGHT_TEMP", "samplingRate": 256,
            "units": "device units", "start": iso(start_ms), "end": iso(end_ms),
            "records": [{"timestamp": iso(row[0]), "deviceId": row[1],
                         "samples": json.loads(row[2])} for row in rows],
            "labels": [{"label": row[0], "start": iso(row[1]),
                        "end": iso(row[2]) if row[2] is not None else None} for row in labels],
            "mobileScores": mobile_score_rows(participant, start_ms, end_ms)}


def save_mobile_score(participant: str, observed_ms: int, score: float, state: str,
                      uncertainty_seconds: int = 0, app_version: str = "", note: str = "",
                      score_id: int | None = None) -> int:
    """Save a manually observed mobile value; state is never inferred from score."""
    now_ms = int(time.time() * 1000)
    if not participant:
        raise ValueError("Select a participant first")
    if isinstance(observed_ms, bool) or not isinstance(observed_ms, int) or not 0 <= observed_ms <= now_ms + 60_000:
        raise ValueError("Observation time must be in the past or present")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("Score must be a number from 0 to 100")
    if state not in MOBILE_STATES:
        raise ValueError("Choose Alert, Engaged, Relaxed, or Calm as shown in the mobile app")
    if isinstance(uncertainty_seconds, bool) or not isinstance(uncertainty_seconds, int) or not 0 <= uncertainty_seconds <= 3600:
        raise ValueError("Time uncertainty must be a whole number from 0 to 3600 seconds")
    if not isinstance(note, str) or len(note) > 500 or not isinstance(app_version, str) or len(app_version) > 80:
        raise ValueError("Note must be at most 500 characters; app version at most 80")
    try:
        with db() as connection:
            values = (observed_ms, score, state, uncertainty_seconds, app_version.strip(), note.strip(), now_ms)
            if score_id is None:
                cursor = connection.execute("""INSERT INTO mobile_scores
                    (observed_ms,score,state,uncertainty_seconds,app_version,note,updated_ms,created_ms,participant_id)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (*values, now_ms, participant))
                return cursor.lastrowid
            cursor = connection.execute("""UPDATE mobile_scores SET observed_ms=?,score=?,state=?,
                uncertainty_seconds=?,app_version=?,note=?,updated_ms=? WHERE id=? AND participant_id=?""",
                (*values, score_id, participant))
            if cursor.rowcount != 1:
                raise ValueError("Mobile observation not found")
            return score_id
    except sqlite3.IntegrityError as error:
        raise ValueError("An observation already exists at this timestamp; edit it instead") from error


def delete_mobile_score(participant: str, score_id: int) -> None:
    with db() as connection:
        cursor = connection.execute("DELETE FROM mobile_scores WHERE id=? AND participant_id=?",
                                    (score_id, participant))
        if cursor.rowcount != 1:
            raise ValueError("Mobile observation not found")


def mobile_score_filter(participant: str, start_ms: int | None, end_ms: int | None,
                        before_ms: int | None = None) -> tuple[str, list]:
    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        raise ValueError("Start must be earlier than end")
    terms, values = ["o.participant_id=?"], [participant]
    for column, operator, value in (("observed_ms", ">=", start_ms), ("observed_ms", "<", end_ms),
                                    ("observed_ms", "<", before_ms)):
        if value is not None:
            terms.append(f"o.{column}{operator}?")
            values.append(value)
    return " AND ".join(terms), values


def mobile_score_rows(participant: str, start_ms: int | None = None, end_ms: int | None = None,
                      before_ms: int | None = None, limit: int | None = None) -> list[dict]:
    where, values = mobile_score_filter(participant, start_ms, end_ms, before_ms)
    query = """SELECT o.*,r.timestamp_ms AS eeg_ms,r.device_id AS eeg_device,r.sample_count AS eeg_samples,
        l.label AS activity FROM mobile_scores o
        LEFT JOIN readings r ON r.participant_id=o.participant_id AND r.timestamp_ms=(
            SELECT timestamp_ms FROM readings WHERE participant_id=o.participant_id
            AND timestamp_ms<=o.observed_ms AND timestamp_ms>o.observed_ms-1000
            ORDER BY timestamp_ms DESC LIMIT 1)
        LEFT JOIN labels l ON l.participant_id=o.participant_id AND l.start_ms<=o.observed_ms
            AND (l.end_ms IS NULL OR l.end_ms>o.observed_ms)
        WHERE """ + where + " ORDER BY o.observed_ms DESC"
    if limit is not None:
        query += " LIMIT ?"
        values.append(limit)
    with db() as connection:
        rows = connection.execute(query, values).fetchall()
    return [{"id": row["id"], "participantId": row["participant_id"], "observedAt": iso(row["observed_ms"]),
             "score": row["score"], "state": row["state"], "uncertaintySeconds": row["uncertainty_seconds"],
             "appVersion": row["app_version"], "note": row["note"], "source": "mobile_manual",
             "createdAt": iso(row["created_ms"]), "updatedAt": iso(row["updated_ms"]),
             "activity": row["activity"], "eeg": {"recordStart": iso(row["eeg_ms"]),
                 "offsetMs": row["observed_ms"] - row["eeg_ms"], "deviceId": row["eeg_device"],
                 "sampleCount": row["eeg_samples"]} if row["eeg_ms"] is not None else None} for row in rows]


def mobile_score_context(connection: sqlite3.Connection, participant: str, observed_ms: int) -> dict:
    """Describe nearby archived seconds; this is not the mobile app's averaging window."""
    start_ms, end_ms = observed_ms - 30_000, observed_ms + 30_000
    records = connection.execute("""SELECT timestamp_ms FROM readings WHERE participant_id=?
        AND timestamp_ms>? AND timestamp_ms<? ORDER BY timestamp_ms""",
        (participant, start_ms - 1000, end_ms)).fetchall()
    covered_ms, cursor = 0, start_ms
    for record in records:
        lower, upper = max(start_ms, record["timestamp_ms"]), min(end_ms, record["timestamp_ms"] + 1000)
        covered_ms += max(0, upper - max(cursor, lower))
        cursor = max(cursor, upper)
    return {"start": iso(start_ms), "end": iso(end_ms), "seconds": 60,
            "recordCount": len(records), "coveredSeconds": covered_ms / 1000,
            "coveragePercent": round(covered_ms / 600, 1)}


def mobile_score_page(participant: str, start_ms: int | None = None, end_ms: int | None = None,
                      before_ms: int | None = None) -> dict:
    where, values = mobile_score_filter(participant, start_ms, end_ms)
    with db() as connection:
        total = connection.execute("SELECT COUNT(*) FROM mobile_scores o WHERE " + where, values).fetchone()[0]
    rows = mobile_score_rows(participant, start_ms, end_ms, before_ms, MOBILE_SCORE_PAGE_SIZE + 1)
    page = rows[:MOBILE_SCORE_PAGE_SIZE]
    with db() as connection:
        for row in page:
            row["context"] = mobile_score_context(connection, participant, utc_ms(row["observedAt"]))
    return {"participantId": participant, "total": total, "observations": page,
            "nextBefore": page[-1]["observedAt"] if len(rows) > MOBILE_SCORE_PAGE_SIZE else None}


def mobile_score_export(participant: str, format_name: str, start_ms: int | None = None,
                        end_ms: int | None = None) -> bytes:
    rows = mobile_score_rows(participant, start_ms, end_ms)
    if format_name == "json":
        return json.dumps({"participantId": participant, "total": len(rows), "observations": rows},
                          indent=2, allow_nan=False).encode()
    if format_name != "csv":
        raise ValueError("Choose CSV or JSON")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "participant_id", "observed_at_utc", "score", "state", "uncertainty_seconds",
                     "app_version", "note", "source", "created_at_utc", "updated_at_utc", "activity",
                     "eeg_record_start_utc", "eeg_offset_ms", "eeg_device_id", "eeg_sample_count"])
    for row in rows:
        eeg = row["eeg"] or {}
        writer.writerow([safe_csv(str(value)) if value is not None else "" for value in (
            row["id"], row["participantId"], row["observedAt"], row["score"], row["state"],
            row["uncertaintySeconds"], row["appVersion"], row["note"], row["source"], row["createdAt"],
            row["updatedAt"], row["activity"], eeg.get("recordStart"), eeg.get("offsetMs"),
            eeg.get("deviceId"), eeg.get("sampleCount"))])
    return output.getvalue().encode()


def pattern_report_path(participant: str, *, for_write: bool = False) -> Path:
    digest = hashlib.sha256(f"{DB_PATH.name}:{participant}".encode()).hexdigest()[:16]
    return ArchivePaths(data_root()).pattern_report(f"{digest}.json", for_write=for_write)


def pattern_report(participant: str) -> dict:
    """Serve the optional derived analysis with fresh activity labels and source status."""
    path = pattern_report_path(participant)
    if not path.is_file():
        return {"available": False, "participantId": participant}
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("participantId") != participant or report.get("sourceDatabase") != DB_PATH.name:
        raise ValueError("Saved pattern report does not match the selected archive")
    with db() as connection:
        count, first, last = connection.execute("""SELECT COUNT(*),MIN(timestamp_ms),MAX(timestamp_ms)
            FROM readings WHERE participant_id=?""", (participant,)).fetchone()
        labels = connection.execute("""SELECT label,start_ms,end_ms FROM labels
            WHERE participant_id=? ORDER BY start_ms""", (participant,)).fetchall()
    report["available"] = True
    report["sourceCurrent"] = (report["source"] == {"records": count,
        "first": iso(first) if first is not None else None,
        "last": iso(last) if last is not None else None})
    report["currentRecords"] = count
    report["labels"] = [{"label": row[0], "start": iso(row[1]),
                         "end": iso(row[2]) if row[2] is not None else None} for row in labels]
    return report


def signal_preview(rows: list[sqlite3.Row], bucket_ms: int = 10_000,
                   end_ms: int | None = None, start_ms: int | None = None) -> dict:
    """Summarize stored powers, preserving every gap and device boundary."""
    preview = {"bucketMs": bucket_ms, "segments": [], "coverage": []}
    previous = None
    for row in rows:
        stamp = row["timestamp_ms"]
        end = min(stamp + 1000, end_ms) if end_ms is not None else stamp + 1000
        continuous = (previous is not None and stamp == previous["timestamp_ms"] + 1000
                      and row["device_id"] == previous["device_id"])
        if continuous:
            preview["coverage"][-1]["end"] = iso(end)
        else:
            preview["coverage"].append({"start": iso(max(stamp,start_ms) if start_ms is not None else stamp), "end": iso(end)})
            preview["segments"].append([])
        segment = preview["segments"][-1]
        bucket = stamp // bucket_ms * bucket_ms
        if not segment or segment[-1]["bucket"] != bucket:
            segment.append({"bucket": bucket, "start": iso(max(stamp,start_ms) if start_ms is not None else stamp), "end": iso(end),
                            "records": 0, "power": 0.0})
        point = segment[-1]
        point["end"] = iso(end)
        point["records"] += 1
        point["power"] += max(0, row["rms"] ** 2 - row["mean"] ** 2)
        previous = row
    for segment in preview["segments"]:
        for point in segment:
            point["acRms"] = math.sqrt(point.pop("power") / point["records"])
            del point["bucket"]
    return preview


def interval_previews(rows: list[sqlite3.Row]) -> dict:
    slots = {}
    for row in rows:
        slot = row["timestamp_ms"] // ACTIVITY_INTERVAL_MS * ACTIVITY_INTERVAL_MS
        slots.setdefault(slot, []).append(row)
    return {slot: signal_preview(records, end_ms=slot + ACTIVITY_INTERVAL_MS)
            for slot, records in slots.items()}


def recording_day_bounds(date: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("Choose a recording date in YYYY-MM-DD format")
    midnight = datetime.fromisoformat(date).replace(tzinfo=ZoneInfo("America/New_York"))
    return int(midnight.timestamp() * 1000), int((midnight + timedelta(days=1)).timestamp() * 1000)


def daily_signal(participant: str, date: str, start_ms: int, end_ms: int) -> dict:
    """Re-aggregate a selected daily range from stored seconds, including empty gaps."""
    lower, upper = recording_day_bounds(date)
    if not participant or not lower <= start_ms < end_ms <= upper:
        raise ValueError("Select a participant and a time range within this New York recording day")
    with db() as connection:
        rows = connection.execute("""SELECT timestamp_ms,device_id,mean,rms FROM readings
            WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<? ORDER BY timestamp_ms""",
            (participant, start_ms-999, end_ms)).fetchall()
        labels = connection.execute("""SELECT * FROM labels WHERE participant_id=?
            AND start_ms<? AND (end_ms IS NULL OR end_ms>?) ORDER BY start_ms,id""",
            (participant, end_ms, start_ms)).fetchall()
        decisions=episode_review.unknown_spans(connection,participant,start_ms,end_ms)
    bucket_ms = max(1000, math.ceil((end_ms - start_ms) / 600 / 1000) * 1000)
    preview=signal_preview(rows,bucket_ms,end_ms,start_ms)
    public=[public_label(label) for label in labels]
    lane_facts=daily_review.lanes(preview,public,start_ms,end_ms,decisions)
    return {"participantId":participant,"date": date, "timezone": "America/New_York", "start": iso(start_ms), "end": iso(end_ms),
            "records": len(rows), "coveragePct": round(100*lane_facts["recordedMs"]/(end_ms-start_ms),1),
            "labels": public, "lanes":lane_facts,
            "preview": preview}


def recorded_days(connection: sqlite3.Connection, participant: str, dates: set[str]) -> list[dict]:
    """Return complete New York recording days, independent of half-hour pagination."""
    zone = ZoneInfo("America/New_York")
    days = []
    for date in sorted(dates, reverse=True):
        lower, upper = recording_day_bounds(date)
        rows = connection.execute("""SELECT timestamp_ms,device_id,mean,rms FROM readings
            WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<? ORDER BY timestamp_ms""",
            (participant, lower-999, upper)).fetchall()
        if not rows:
            continue
        start, end = max(lower,rows[0]["timestamp_ms"]), min(upper,rows[-1]["timestamp_ms"] + 1000)
        labels = connection.execute("""SELECT * FROM labels WHERE participant_id=?
            AND start_ms<? AND (end_ms IS NULL OR end_ms>?) ORDER BY start_ms,id""",
            (participant, end, start)).fetchall()
        # Approximately 600 time buckets per day, without merging recording gaps.
        bucket_ms = max(10_000, math.ceil((end - start) / 600 / 10_000) * 10_000)
        preview=signal_preview(rows,bucket_ms,end,start)
        public=[public_label(label) for label in labels]
        lane_facts=daily_review.lanes(preview,public,start,end,episode_review.unknown_spans(connection,participant,start,end))
        days.append({"participantId":participant,"date": date, "timezone": zone.key, "start": iso(start), "end": iso(end),
                     "firstRecord": iso(rows[0]["timestamp_ms"]), "lastRecord": iso(rows[-1]["timestamp_ms"]), "records": len(rows),
                     "coveragePct": round(100*lane_facts["recordedMs"]/(end-start),1),
                     "labels": public, "lanes":lane_facts,
                     "preview": preview})
    return days


def recorded_intervals(participant: str, before_ms: int, limit: int = ACTIVITY_INTERVAL_PAGE_SIZE,
                       now_ms: int | None = None) -> dict:
    """List recorded half-hour slots, newest first, with overlapping activity labels."""
    if not 1 <= limit <= 96:
        raise ValueError("Interval page size must be 1–96")
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    with db() as connection:
        rows = connection.execute("""SELECT (timestamp_ms / ?) * ? AS start_ms,
            COUNT(*) AS records, MIN(timestamp_ms) AS first_ms, MAX(timestamp_ms) AS last_ms
            FROM readings WHERE participant_id=? AND timestamp_ms<?
            GROUP BY start_ms ORDER BY start_ms DESC LIMIT ?""",
            (ACTIVITY_INTERVAL_MS, ACTIVITY_INTERVAL_MS, participant, before_ms, limit + 1)).fetchall()
        page = rows[:limit]
        zone = ZoneInfo("America/New_York")
        dates = {datetime.fromtimestamp(row["start_ms"] / 1000, zone).date().isoformat() for row in page}
        days = recorded_days(connection, participant, dates)
        if page:
            earliest = page[-1]["start_ms"]
            latest_end = page[0]["start_ms"] + ACTIVITY_INTERVAL_MS
            labels = connection.execute("""SELECT * FROM labels
                WHERE participant_id=? AND start_ms<? AND (end_ms IS NULL OR end_ms>?)
                ORDER BY start_ms""", (participant, latest_end, earliest)).fetchall()
            signal_rows = connection.execute("""SELECT timestamp_ms,device_id,mean,rms FROM readings
                WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<? ORDER BY timestamp_ms""",
                (participant, earliest, min(latest_end, before_ms))).fetchall()
        else:
            labels = []
            signal_rows = []
    previews = interval_previews(signal_rows)
    intervals = []
    for row in page:
        start_ms = row["start_ms"]
        end_ms = start_ms + ACTIVITY_INTERVAL_MS
        overlapping = [public_label(label) for label in labels
                       if label["start_ms"] < end_ms
                       and (label["end_ms"] is None or label["end_ms"] > start_ms)]
        intervals.append({"start": iso(start_ms), "end": iso(end_ms),
                          "day": datetime.fromtimestamp(start_ms / 1000, zone).date().isoformat(),
                          "records": row["records"],
                          "coveragePct": round(min(100, 100 * row["records"] / 1800), 1),
                          "firstRecord": iso(row["first_ms"]), "lastRecord": iso(row["last_ms"]),
                          "complete": end_ms <= now_ms, "labels": overlapping,
                          "preview": previews[start_ms]})
    return {"intervals": intervals, "days": days,
            "nextBefore": iso(page[-1]["start_ms"]) if len(rows) > limit else None}


def raw_table(participant: str, before_ms: int | None = None,
              start_ms: int | None = None, end_ms: int | None = None,
              limit: int = RAW_TABLE_PAGE_SIZE) -> dict:
    """Page through archived waveform records, preserving every sample value."""
    if not 1 <= limit <= 100:
        raise ValueError("Raw table page size must be 1–100")
    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        raise ValueError("Start must be earlier than end")
    where = ["participant_id=?"]
    values: list[int | str] = [participant]
    if start_ms is not None:
        where.append("timestamp_ms>=?")
        values.append(start_ms)
    if end_ms is not None:
        where.append("timestamp_ms<?")
        values.append(end_ms)
    predicate = " AND ".join(where)
    page_values = list(values)
    if before_ms is not None:
        page_values.append(before_ms)
        page_predicate = predicate + " AND timestamp_ms<?"
    else:
        page_predicate = predicate
    with db() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM readings WHERE {predicate}", values).fetchone()[0]
        rows = connection.execute(f"""SELECT timestamp_ms,device_id,waveform_json FROM readings
            WHERE {page_predicate} ORDER BY timestamp_ms DESC LIMIT ?""",
            (*page_values, limit + 1)).fetchall()
        page = rows[:limit]
        if page:
            labels = connection.execute("""SELECT label,start_ms,end_ms FROM labels
                WHERE participant_id=? AND start_ms<=?
                AND (end_ms IS NULL OR end_ms>?) ORDER BY start_ms""",
                (participant, page[0]["timestamp_ms"], page[-1]["timestamp_ms"])).fetchall()
        else:
            labels = []
    return {"total": total,
            "records": [{"timestamp": iso(row["timestamp_ms"]), "device_id": row["device_id"],
                         "activity": next((label["label"] for label in labels
                                           if label["start_ms"] <= row["timestamp_ms"]
                                           and (label["end_ms"] is None or row["timestamp_ms"] < label["end_ms"])), None),
                         "waveform": json.loads(row["waveform_json"])} for row in page],
            "nextBefore": iso(page[-1]["timestamp_ms"]) if len(rows) > limit else None}


def saved_snapshot_tables(participant: str) -> dict:
    """Read sessions and stats exactly as saved by the most recent complete export."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", participant):
        raise ValueError("Invalid participant ID")
    for snapshot in ArchivePaths(data_root()).snapshots():
        manifest_path = snapshot / "manifest.json"
        sessions_path = snapshot / participant / "sessions.json"
        stats_path = snapshot / participant / "stats.json"
        if not all(path.is_file() for path in (manifest_path, sessions_path, stats_path)):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if participant not in {entry.get("participantId") for entry in manifest.get("participants", [])}:
            continue
        return {"exportedAt": manifest.get("exportedAt"),
                "sessions": json.loads(sessions_path.read_text(encoding="utf-8")),
                "stats": json.loads(stats_path.read_text(encoding="utf-8"))}
    return {"exportedAt": None, "sessions": [], "stats": None}


def public_label(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "label": row["label"], "note": row["note"],
            "start": iso(row["start_ms"]), "end": iso(row["end_ms"]) if row["end_ms"] is not None else None,
            "source": row["source"], "reviewed": bool(row["reviewed"]),
            "activityId": row["activity_id"], "recordedLabel": row["recorded_label"], "reviewState": row["review_state"],
            "timingPrecision": row["timing_precision"], "startUncertaintyMs": row["start_uncertainty_ms"],
            "endUncertaintyMs": row["end_uncertainty_ms"]}


def label_review(participant: str, scope: str, limit: int = 100) -> dict:
    if scope not in ("all", "suggested", "needs_review", "uncertain", "confirmed") or not 1 <= limit <= 1000:
        raise ValueError("Choose a valid activity review scope; page size must be 1–1000")
    where = "participant_id=?"
    if scope == "suggested":where += " AND source IN ('schedule','model') AND review_state='needs_review'"
    elif scope != "all":where += " AND review_state='"+scope+"'"
    with db() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM labels WHERE {where}", (participant,)).fetchone()[0]
        rows = connection.execute(f"SELECT * FROM labels WHERE {where} ORDER BY start_ms DESC,id DESC LIMIT ?",
                                  (participant, limit)).fetchall()
    return {"labels": [public_label(row) for row in rows], "total": total, "hasMore": len(rows) < total}


def add_label(participant: str, label: str, note: str, start_ms: int, end_ms: int | None,
              label_id: int | None = None, activity_id: str | None = None, expected_participant: str | None = None, evidence: dict | None = None, expected_version: int | None = None, review_key: str | None = None, candidate_provider=None, origin="manual", context_provider=None) -> int:
    """Save a human activity, protecting confirmed labels and splitting pending suggestions."""
    label = label.strip()
    if not participant or not label or len(label) > 80 or len(note) > 500:
        raise ValueError("Choose a participant and a label of 1–80 characters (note ≤500 characters)")
    if end_ms is not None and end_ms <= start_ms:
        raise ValueError("End must be later than start")
    if start_ms > int(time.time() * 1000) + 60_000:
        raise ValueError("Start cannot be in the future")
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if expected_participant is not None and (expected_participant != participant or setting(connection,"participant_id") != expected_participant):
            raise ValueError("Participant changed. Refresh before saving this activity.")
        if origin not in ('manual','model'):raise ValueError('Invalid activity origin')
        context=context_provider(connection) if context_provider else {}
        if review_key is not None:
            matches=episode_review.queue(connection,participant,candidates=candidate_provider(participant) if candidate_provider else (),task_key=review_key)['items']
            if not matches:raise ValueError('This review item changed. Refresh the queue before saving.')
            context=matches[0]
        before=episode_review.snapshot(connection,participant)
        episode_review.protect_unknown(connection,participant,start_ms,end_ms)
        if label_id is not None and expected_version is not None:episode_review.require_label(connection,participant,label_id,expected_version)
        current = connection.execute("SELECT * FROM labels WHERE id=? AND participant_id=?",
                                     (label_id, participant)).fetchone() if label_id is not None else None
        if label_id is not None and current is None:
            raise ValueError("Label not found")
        if activity_id is not None and not isinstance(activity_id,str):
            raise ValueError("Invalid activity ID")
        if not activity_id and current is not None and label == current["label"]:
            activity_id = current["activity_id"]
        activity = personal_activities.resolve(connection,participant,label,activity_id,
            allow_archived=current["activity_id"] if current is not None else None)
        label = activity["name"]
        recorded_label = current["recorded_label"] if current is not None and current["activity_id"] == activity["id"] else label
        review,precision,start_uncertainty,end_uncertainty = personal_activities.label_evidence(current,evidence,start_ms,end_ms)
        overlaps = connection.execute("""SELECT * FROM labels WHERE participant_id=?
            AND id != COALESCE(?, -1)
            AND start_ms < COALESCE(?, 9223372036854775807)
            AND COALESCE(end_ms, 9223372036854775807) > ?""",
            (participant, label_id, end_ms, start_ms)).fetchall()
        if any(row["source"] not in ("schedule","model") or row["review_state"] != "needs_review" for row in overlaps):
            raise ValueError("This interval overlaps an existing protected activity. Stop or edit the other interval first.")
        suggestions = list(overlaps)
        if current is not None and current["source"] in ("schedule","model") and current["review_state"] == "needs_review":
            suggestions.append(current)
        for row in suggestions:
            # Preserve schedule suggestions outside the human-entered interval.
            spans = []
            if row["start_ms"] < start_ms:
                left_end = min(start_ms, row["end_ms"]) if row["end_ms"] is not None else start_ms
                if left_end > row["start_ms"]:
                    spans.append((row["start_ms"], left_end))
            if end_ms is not None and (row["end_ms"] is None or end_ms < row["end_ms"]):
                spans.append((max(end_ms, row["start_ms"]), row["end_ms"]))
            if row["id"] != label_id:
                connection.execute("DELETE FROM labels WHERE id=?", (row["id"],))
            for lower, upper in spans:
                connection.execute("""INSERT INTO labels
                    (participant_id,label,note,start_ms,end_ms,source,reviewed,batch_id,activity_id,recorded_label)
                    VALUES (?,?,?,?,?,?,0,?,?,?)""",
                    (participant, row["label"], row["note"], lower, upper, row["source"], row["batch_id"],row["activity_id"],row["recorded_label"]))
        if current is not None:
            connection.execute("UPDATE labels SET label=?,note=?,start_ms=?,end_ms=?,reviewed=?,activity_id=?,recorded_label=?,review_state=?,timing_precision=?,start_uncertainty_ms=?,end_uncertainty_ms=? WHERE id=?",
                               (label, note.strip(), start_ms, end_ms,int(review=="confirmed"),activity["id"],recorded_label,review,precision,start_uncertainty,end_uncertainty,label_id))
            episode_review.record(connection,participant,'edit',before,context=context)
            return label_id
        cursor = connection.execute("INSERT INTO labels(participant_id,label,note,start_ms,end_ms,activity_id,recorded_label,reviewed,review_state,timing_precision,start_uncertainty_ms,end_uncertainty_ms,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (participant, label, note.strip(), start_ms, end_ms,activity["id"],recorded_label,int(review=="confirmed"),review,precision,start_uncertainty,end_uncertainty,origin))
        episode_review.record(connection,participant,'create',before,context=context)
        return cursor.lastrowid


class Handler(BaseHTTPRequestHandler):
    server: "AppServer"

    def log_message(self, format: str, *args: object) -> None:
        # Avoid logging request paths containing participant IDs or annotation text.
        print("HTTP", args[1] if len(args) > 1 else "", flush=True)

    def _json(self, status: int, value: dict | list) -> None:
        body = json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def _check_host(self) -> bool:
        host = self.headers.get("Host", "").split(":")[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def _selected(self) -> str:
        with db() as connection:
            return setting(connection, "participant_id")

    def do_GET(self) -> None:
        self._workspace_request(self._do_GET, post=False)

    def _workspace_request(self, action, *, post):
        # Authenticate before acquiring an exclusive lease. Ordinary GETs remain concurrent.
        if not self._check_host():
            return self._json(403, {"error": "Local host only"})
        route = urllib.parse.urlsplit(self.path).path
        exclusive = route == '/api/desktop/backup' or (post and route in ('/api/desktop/restore','/api/desktop/import'))
        if exclusive:
            desktop = self.server.desktop
            if not desktop or not desktop.authorized(self):
                return self._json(403, {"error": "Open the desktop launcher to access this workspace."})
            if post and (self.headers.get('X-Awear-Local') != '1' or self.headers.get('Origin','') not in ('','http://'+self.headers.get('Host',''))):
                return self._json(403, {"error": "Local request required"})
        try:
            with self.server.workspace_gate.lease(exclusive=exclusive):
                return action()
        except ValueError as error:
            return self._json(409, {"error": str(error)})

    def _do_GET(self) -> None:
        if not self._check_host():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Local host only"})
        route = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(route.query)
        try:
            if self.server.desktop and self.server.desktop.get(self, route):
                return
            if route.path == "/api/activities":
                include = params.get("includeArchived",["1"])[0]
                if include not in ("0","1"):raise ValueError("Invalid archive filter")
                with db() as connection:
                    connection.execute("BEGIN")
                    return self._json(200,personal_activities.page(connection,self._selected(),
                        offset=int(params.get("offset",["0"])[0]),limit=int(params.get("limit",["50"])[0]),include_archived=include=="1"))
            activity_history = re.fullmatch(r"/api/activities/([0-9a-f]{32})/history",route.path)
            if activity_history:
                with db() as connection:
                    connection.execute("BEGIN")
                    return self._json(200,personal_activities.history(connection,self._selected(),activity_history[1],
                        offset=int(params.get("offset",["0"])[0]),limit=int(params.get("limit",["50"])[0])))
            if route.path == "/api/export/activities.json":
                participant = self._selected()
                if not participant:raise ValueError("Select a participant first")
                with db() as connection:
                    connection.execute("BEGIN")
                    self.send_response(200)
                    self.send_header("Content-Type","application/json; charset=utf-8")
                    self.send_header("Content-Disposition",'attachment; filename="mindtrail-activities-history.json"')
                    self.send_header("Cache-Control","no-store")
                    self.send_header("X-Content-Type-Options","nosniff")
                    self.end_headers()
                    for block in personal_activities.export_json(connection,participant):self.wfile.write(block)
                return
            if route.path == "/api/export/activity-assignments.csv":
                return self._export_labels(extended=True)
            if route.path == '/api/monitoring':
                from monitoring_access import page
                return self._json(200,page(self.server.analysis,self._selected(),{k:v[0] for k,v in params.items()}))
            if route.path in ('/api/export/analysis-bundle.zip','/api/export/monitoring-history.zip'):
                from contextlib import ExitStack
                from analysis_exports import analysis_bundle,history_bundle
                historical=route.path.endswith('monitoring-history.zip')
                bundle=history_bundle(self.server.analysis,self._selected()) if historical else analysis_bundle(
                    self.server.analysis,self._selected(),safe_csv,identity=params.get('identity',[None])[0],
                    question=params.get('question',[None])[0],lens=params.get('lens',['recurrence_conditions'])[0])
                with ExitStack() as stack:
                    try:output=stack.enter_context(bundle)
                    except (OSError,sqlite3.Error):raise ValueError('Export could not be prepared. Check local storage and retry; recordings unchanged.') from None
                    size=output.seek(0,2);output.seek(0)
                    self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Disposition','attachment; filename="mindtrail-'+('numerical-history' if historical else 'analysis-audit')+'.zip"');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(size));self.end_headers()
                    while chunk:=output.read(65536):self.wfile.write(chunk)
                return
            if route.path == '/api/export/monitoring-version.json':
                from monitoring_access import payload
                from analysis_provenance import canonical_json
                identifier=params.get('version',[None])[0]
                if not identifier:raise ValueError('Choose a retained numerical version')
                output=canonical_json(payload(self.server.analysis,self._selected(),identifier)).encode('utf-8')
                self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="mindtrail-monitoring-version.json"');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(output)));self.end_headers();self.wfile.write(output);return
            if route.path == "/api/discovery/decision":
                from discovery_decisions import read
                return self._json(200,read(self.server.analysis,self._selected(),int(params.get('offset',['0'])[0])))
            if route.path in ('/api/discovery/report','/api/discovery/report/view','/api/export/discovery-report.html','/api/export/discovery-report.json'):
                from discovery_report_access import page,recheck
                from discovery_report_render import sections,document
                from analysis_provenance import canonical_json
                value=page(self.server.analysis,self._selected(),params.get('identity',[None])[0],internal=True,question=params.get('question',[None])[0],lens=params.get('lens',['recurrence_conditions'])[0])
                if route.path.startswith('/api/export/'):
                    if value['status']!='current':raise ValueError('A current discovery report is required before export')
                    html=route.path.endswith('.html');output=document(value) if html else canonical_json({k:v for k,v in value.items() if k!='_guard'}).encode('utf-8')
                    recheck(self.server.analysis,value)
                    self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="mindtrail-discovery-report.'+('html' if html else 'json')+'"');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(output)));self.end_headers();self.wfile.write(output);return
                if route.path.endswith('/view') and value['status']=='current':
                    markup=sections(value,live=True);recheck(self.server.analysis,value)
                    value={k:value[k] for k in ('status','participantId','evidenceIdentity','snapshotIdentity','decision','decisionRelation','investigation')}|{'html':markup,'questions':value['human']['questions']['questions']}
                elif value['status']=='current':
                    recheck(self.server.analysis,value);value={k:v for k,v in value.items() if k!='_guard'}
                return self._json(200,value)
            if route.path == "/api/discovery/review":
                from discovery_review_access import page
                cues=params.get('cues',['0'])[0]
                if cues not in ('0','1'):raise ValueError('Invalid cues choice')
                return self._json(200,page(self.server.analysis,self._selected(),cues=='1'))
            if route.path == "/api/discovery/review/waveform":
                from discovery_review_access import waveform
                return self._json(200,waveform(self.server.analysis,self._selected(),{k:v[0] for k,v in params.items()}))
            if route.path == "/api/discovery/context":
                import discovery_context
                manager=self.server.analysis;participant=self._selected();key=params.get('windowId',[None])[0]
                if key is None:return self._json(200,discovery_context.listing(manager,participant,int(params.get('offset',['0'])[0])))
                value=discovery_context.entry(manager,participant,key,int(params.get('offset',['0'])[0]),int(params.get('limit',['25'])[0]))
                value['hasContext']=value['record'] is not None
                if params.get('reveal',['0'])[0]!='1':value.update(record=None,history=[])
                return self._json(200,value)
            if route.path in ('/api/export/discovery-context.json','/api/export/discovery-context.csv'):
                from discovery_context import export
                participant=self._selected()
                if not participant:raise ValueError('Select a participant first')
                format='csv' if route.path.endswith('.csv') else 'json'
                chunks=export(self.server.analysis,participant,format,safe_csv);first=next(chunks)
                self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8' if format=='csv' else 'application/json; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="mindtrail-discovery-context.'+format+'"');self.send_header('Cache-Control','no-store');self.end_headers()
                self.wfile.write(first)
                for chunk in chunks:self.wfile.write(chunk)
                return
            if route.path == "/api/discovery/recurrence":
                from recurrence_access import page
                return self._json(200,page(self.server.analysis,self._selected(),{k:v[0] for k,v in params.items()}))
            if route.path == "/api/discovery/recurrence/waveform":
                from recurrence_access import waveform
                return self._json(200,waveform(self.server.analysis,self._selected(),{k:v[0] for k,v in params.items()}))
            if route.path == "/api/discovery/daily":
                from discovery_daily_access import page
                return self._json(200,page(self.server.analysis,self._selected(),{k:v[0] for k,v in params.items()}))
            if route.path == "/api/discovery/daily/waveform":
                from discovery_daily_access import waveform
                return self._json(200,waveform(self.server.analysis,self._selected(),{k:v[0] for k,v in params.items()}))
            if route.path == "/api/discovery":
                from discovery_contracts import read
                return self._json(200,read(self.server.analysis,self._selected()))
            if route.path == "/api/discovery/questions":
                from discovery_questions import page
                return self._json(200,page(self.server.analysis,self._selected()))
            if route.path == "/api/workspace":
                from workspace_journey import snapshot
                from discovery_contracts import read
                participant = self._selected()
                analysis = self.server.analysis.state(participant)
                discovery = read(self.server.analysis, participant)
                with db() as connection:
                    return self._json(200, snapshot(connection, participant, analysis, discovery,
                        mode=self.server.desktop.mode() if self.server.desktop else "api"))
            if route.path == "/api/analysis/state":
                return self._json(200, self.server.analysis.state(self._selected()))
            if route.path == "/api/activity-comparisons":
                from activity_access import page
                return self._json(200,page(self.server.analysis,self._selected(),{k:v[0] for k,v in params.items()}))
            if route.path == "/api/activity-comparisons/waveform":
                from activity_access import waveform
                return self._json(200,waveform(self.server.analysis,self._selected(),params.get('group',[''])[0],params.get('window',[''])[0],params.get('identity',[''])[0]))
            if route.path == "/api/exploration":
                from exploration_access import read_view
                return self._json(200,read_view(self.server.analysis,self._selected(),params.get('group',[None])[0],params.get('view',['with_power'])[0]))
            if route.path == "/api/exploration/waveform":
                from exploration_access import waveform
                return self._json(200,waveform(self.server.analysis,self._selected(),params.get('group',[''])[0],params.get('view',['with_power'])[0],params.get('window',[''])[0],params.get('identity',[''])[0]))
            if route.path == "/api/analysis/report":
                path = self.server.analysis.report_path(self._selected())
                # Open before stat: retention may unlink an old path during a new job.
                with path.open("rb") as source:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="mindtrail-recording-quality.json"')
                    self.send_header("Content-Length", str(os.fstat(source.fileno()).st_size))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    while block := source.read(65536):
                        self.wfile.write(block)
                return
            if route.path == "/api/review/queue":
                participant=self._selected()
                candidates=self._review_candidates(participant) if params.get('cues',['0'])[0]=='1' else []
                with db() as connection:
                    connection.execute('BEGIN')
                    result=episode_review.queue(connection,participant,offset=int(params.get('offset',['0'])[0]),limit=int(params.get('limit',['25'])[0]),candidates=candidates)
                result['cues']=params.get('cues',['0'])[0]=='1'
                analysis=self.server.analysis.state(participant) if result['cues'] else {}
                result['candidateStatus']='hidden' if not result['cues'] else 'unavailable' if not analysis.get('summary') else 'stale' if analysis.get('stale') else 'disabled' if not (analysis.get('settings') or {}).get('candidateBoundaries') else 'current'
                if result['cues'] and analysis.get('stale'):
                    with db() as connection:result.update(episode_review.queue(connection,participant,offset=int(params.get('offset',['0'])[0]),limit=int(params.get('limit',['25'])[0])))
                if result['cues']:
                    with db() as connection:
                        for item in result['items']:
                            if item.get('labelId'):
                                row=connection.execute('SELECT * FROM labels WHERE id=? AND participant_id=?',(item['labelId'],participant)).fetchone()
                                if row is not None:item['annotation']=public_label(row)
                return self._json(200,result)
            if route.path == "/api/review/history":
                with db() as connection:result=episode_review.history(connection,self._selected(),int(params.get('offset',['0'])[0]))
                return self._json(200,result)
            if route.path == "/api/review/label":
                participant=self._selected();label_id=int(params['id'][0])
                with db() as connection:
                    row=connection.execute('SELECT * FROM labels WHERE participant_id=? AND id=?',(participant,label_id)).fetchone()
                    if row is None:raise ValueError('Label not found')
                    result={**public_label(row),'labelVersion':episode_review.label_version(connection,participant,label_id),'participantId':participant}
                return self._json(200,result)
            if route.path == "/api/status":
                with db() as connection:
                    participant = setting(connection, "participant_id")
                    sync_start = setting(connection, "sync_start_ms")
                    desktop_notice = setting(connection, "desktop_notice") if self.server.desktop else ""
                    activities_revision = personal_activities.revision(connection,participant)
                return self._json(200, {"desktop": self.server.desktop is not None,
                                        "connectionMode": self.server.desktop.mode() if self.server.desktop else "api",
                                        "keyConfigured": bool(self.server.collector.key),
                                        "demo": self.server.collector.demo, "participantId": participant,
                                        "activitiesRevision": activities_revision,
                                        "syncStart": iso(int(sync_start)) if sync_start else None,
                                        "desktopNotice": desktop_notice,
                                        "syncing": self.server.collector.running,
                                        "lastError": self.server.collector.last_error,
                                        "lastSync": iso(self.server.collector.last_sync_ms) if self.server.collector.last_sync_ms else None,
                                        "insertedLast": self.server.collector.inserted_last,
                                        "pollSeconds": POLL_SECONDS})
            if route.path == "/api/participants":
                return self._json(200, {"members": self.server.collector.participants()})
            if route.path == "/api/bluetooth/status":
                return self._json(200, self.server.bluetooth.status())
            if route.path == "/api/labels":
                return self._json(200, label_review(self._selected(), params.get("scope", ["all"])[0],
                                                   int(params.get("limit", ["100"])[0])))
            if route.path == "/api/bluetooth/download":
                path = self.server.bluetooth.download_path(params.get("session", [""])[0], params.get("file", [""])[0])
                size = path.stat().st_size
                self.send_response(200)
                mime = {".csv": "text/csv", ".json": "application/json", ".jsonl": "application/x-ndjson"}.get(path.suffix, "text/plain")
                self.send_header("Content-Type", mime + "; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{path.parent.name}-{path.name}"')
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with path.open("rb") as source:
                    remaining = size
                    while remaining:
                        block = source.read(min(65536, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
                return
            if route.path == "/api/summary":
                participant = self._selected()
                if not participant:
                    return self._json(200, {"participantId": "", "stats": None, "series": [], "labels": [], "activities": [], "waveform": None})
                end_ms = utc_ms(params["end"][0]) if "end" in params else int(time.time() * 1000)
                start_ms = utc_ms(params["start"][0]) if "start" in params else end_ms - 24*60*60*1000
                if start_ms >= end_ms or end_ms-start_ms > 90*24*60*60*1000:
                    raise ValueError("Choose a window up to 90 days with start before end")
                return self._json(200, summary(participant, start_ms, end_ms))
            if route.path == "/api/recorded-intervals":
                participant = self._selected()
                if not participant:
                    return self._json(200, {"intervals": [], "days": [], "nextBefore": None})
                before_ms = utc_ms(params["before"][0]) if "before" in params else int(time.time() * 1000)
                if before_ms < 0 or before_ms > int(time.time() * 1000) + ACTIVITY_INTERVAL_MS:
                    raise ValueError("Choose a valid interval cursor")
                return self._json(200, recorded_intervals(participant, before_ms))
            if route.path == "/api/local-time":
                return self._json(200,daily_review.wall_time(params.get("value",[""])[0]))
            if route.path == "/api/daily-boundaries":
                recording_day_bounds(params.get("date",[""])[0])
                return self._json(200,self.server.analysis.daily_boundaries(self._selected(),params['date'][0]))
            if route.path == "/api/daily-quality":
                lower,upper=recording_day_bounds(params.get("date",[""])[0])
                start,end=utc_ms(params["start"][0]),utc_ms(params["end"][0])
                if not lower<=start<end<=upper:raise ValueError("Choose a range within this recording day")
                return self._json(200,self.server.analysis.daily_quality(self._selected(),params['date'][0],start,end))
            if route.path == "/api/daily-signal":
                return self._json(200, daily_signal(self._selected(), params.get("date", [""])[0],
                                                   utc_ms(params["start"][0]), utc_ms(params["end"][0])))
            if route.path == "/api/table/raw":
                participant = self._selected()
                if not participant:
                    return self._json(200, {"total": 0, "records": [], "nextBefore": None})
                before_ms = utc_ms(params["before"][0]) if "before" in params else None
                start_ms = utc_ms(params["start"][0]) if "start" in params else None
                end_ms = utc_ms(params["end"][0]) if "end" in params else None
                return self._json(200, raw_table(participant, before_ms, start_ms, end_ms))
            if route.path == "/api/eeg/window":
                participant = self._selected()
                if not participant:
                    raise ValueError("Select a participant first")
                seconds = int(params.get("seconds", ["30"])[0])
                start_ms = utc_ms(params["start"][0]) if "start" in params else None
                return self._json(200, eeg_window(participant, start_ms, seconds))
            if route.path == "/api/patterns":
                participant = self._selected()
                return self._json(200, pattern_report(participant) if participant else
                                  {"available": False, "participantId": ""})
            if route.path in {"/api/mobile-scores", "/api/export/mobile-scores.json", "/api/export/mobile-scores.csv"}:
                participant = self._selected()
                if not participant:
                    raise ValueError("Select a participant first")
                start_ms = utc_ms(params["start"][0]) if "start" in params else None
                end_ms = utc_ms(params["end"][0]) if "end" in params else None
                if route.path == "/api/mobile-scores":
                    before_ms = utc_ms(params["before"][0]) if "before" in params else None
                    return self._json(200, mobile_score_page(participant, start_ms, end_ms, before_ms))
                format_name = route.path.rsplit(".", 1)[1]
                body = mobile_score_export(participant, format_name, start_ms, end_ms)
                self.send_response(200)
                self.send_header("Content-Type", ("application/json" if format_name == "json" else "text/csv") + "; charset=utf-8")
                self.send_header("Content-Disposition", f"attachment; filename=awear-mobile-scores.{format_name}")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
                return
            if route.path == "/api/table/snapshot":
                participant = self._selected()
                return self._json(200, saved_snapshot_tables(participant) if participant else
                                  {"exportedAt": None, "sessions": [], "stats": None})
            if route.path == "/api/export/raw.jsonl":
                return self._export_raw(params)
            if route.path == "/api/export/labels.csv":
                return self._export_labels()
            name = route.path.lstrip("/") or "index.html"
            if name not in {"journey.html", "journey.js", "navigation.js", "index.html", "app.js", "interval_time.js", "style.css", "data.html", "data.js", "data.css",
                            "eeg.html", "eeg.js", "eeg.css", "patterns.html", "patterns.js", "patterns.css",
                            "scores.html", "bluetooth.html", "bluetooth.js", "bluetooth.css",
                            "welcome.html", "welcome.js", "welcome.css", "quality.html", "quality.js", "quality.css", "review.html", "review.js", "review.css", "explore.html", "explore.js", "explore.css", "similarity.js", "compare.html", "compare.js", "compare.css", "discover.html", "discover.js", "discover.css", "discovery_daily.js", "recurrence.html", "recurrence.js", "recurrence.css", "context.html", "context.js", "context.css", "report.html", "report.js", "report.css", "monitor.html", "monitor.js", "monitor.css"}:
                return self._json(404, {"error": "Not found"})
            path = SOURCE_ROOT / "static" / name
            body = path.read_bytes()
            mime = {"index.html": "text/html", "data.html": "text/html", "eeg.html": "text/html",
                    "patterns.html": "text/html", "app.js": "text/javascript", "interval_time.js": "text/javascript", "data.js": "text/javascript",
                    "eeg.js": "text/javascript", "patterns.js": "text/javascript", "style.css": "text/css",
                    "data.css": "text/css", "eeg.css": "text/css", "patterns.css": "text/css",
                    "scores.html": "text/html",
                    "bluetooth.html": "text/html", "bluetooth.js": "text/javascript", "bluetooth.css": "text/css", "welcome.html": "text/html",
                    "welcome.js": "text/javascript", "welcome.css": "text/css",
                    "quality.html": "text/html", "quality.js": "text/javascript", "quality.css": "text/css",
                    "review.html":"text/html","review.js":"text/javascript","review.css":"text/css",
                    "journey.html":"text/html","journey.js":"text/javascript","navigation.js":"text/javascript", "explore.html":"text/html","explore.js":"text/javascript","explore.css":"text/css","similarity.js":"text/javascript","compare.html":"text/html","compare.js":"text/javascript","compare.css":"text/css","discover.html":"text/html","discover.js":"text/javascript","discover.css":"text/css","discovery_daily.js":"text/javascript","recurrence.html":"text/html","recurrence.js":"text/javascript","recurrence.css":"text/css","context.html":"text/html","context.js":"text/javascript","context.css":"text/css","report.html":"text/html","report.js":"text/javascript","report.css":"text/css","monitor.html":"text/html","monitor.js":"text/javascript","monitor.css":"text/css"}[name]
            self.send_response(200)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, KeyError) as error:
            self._json(400, {"error": str(error)})
        except RuntimeError as error:
            self._json(502, {"error": str(error)})

    def _review_candidates(self,participant):
        with db() as connection:
            stamps=connection.execute("SELECT MIN(timestamp_ms),MAX(timestamp_ms) FROM readings WHERE participant_id=? AND typeof(timestamp_ms)='integer' AND timestamp_ms BETWEEN 0 AND 253402300798000 GROUP BY timestamp_ms/86400000",(participant,)).fetchall()
        zone=ZoneInfo('America/New_York')
        dates=sorted({datetime.fromtimestamp(t/1000,zone).date().isoformat() for pair in stamps for t in pair})
        result=[]
        for date in dates:
            value=self.server.analysis.daily_boundaries(participant,date)
            if value['status']!='current':continue
            for run in value['runs']:
                for span in run['methods']['pelt']['segments']:
                    result.append({**span,'candidateIdentity':value['candidateIdentity'],'method':'pelt','maxResolutionMs':run['maxResolutionMs']})
        return result

    def _export_raw(self, params: dict) -> None:
        participant = self._selected()
        if not participant:
            raise ValueError("Select a participant first")
        end_ms = utc_ms(params["end"][0]) if "end" in params else int(time.time()*1000)
        start_ms = utc_ms(params["start"][0]) if "start" in params else end_ms-24*60*60*1000
        if start_ms >= end_ms or end_ms-start_ms > 90*24*60*60*1000:
            raise ValueError("Choose a window up to 90 days")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Disposition", "attachment; filename=awear-raw.jsonl")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with db() as connection:
            for row in connection.execute("""SELECT timestamp_ms,waveform_json,sample_count FROM readings
                WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<? ORDER BY timestamp_ms""",
                (participant, start_ms, end_ms)):
                line = '{"timestamp":' + json.dumps(iso(row[0])) + ',"samplingRate":256,"waveform":' + row[1] + '}\n'
                self.wfile.write(line.encode())

    def _export_labels(self, extended: bool = False) -> None:
        participant = self._selected()
        if not participant:
            raise ValueError("Select a participant first")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["label", "start_utc", "end_utc", "note"] + (["id","activity_id","recorded_label","source","reviewed","review_state","timing_precision","start_uncertainty_ms","end_uncertainty_ms"] if extended else []))
        with db() as connection:
            for row in connection.execute("""SELECT label,start_ms,end_ms,note,id,activity_id,recorded_label,source,reviewed,review_state,timing_precision,start_uncertainty_ms,end_uncertainty_ms FROM labels
                WHERE participant_id=? ORDER BY start_ms""", (participant,)):
                writer.writerow([safe_csv(row[0]), iso(row[1]), iso(row[2]) if row[2] else "", safe_csv(row[3])]
                    + ([row[4],safe_csv(row[5]),safe_csv(row[6]),safe_csv(row[7]),row[8],row[9],row[10],row[11],row[12]] if extended else []))
        body = output.getvalue().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", "attachment; filename=" + ("mindtrail-activity-assignments.csv" if extended else "awear-labels.csv"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self._workspace_request(self._do_POST, post=True)

    def _do_POST(self) -> None:
        if not self._check_host():
            return self._json(403, {"error": "Local host only"})
        origin = self.headers.get("Origin", "")
        expected = "http://" + self.headers.get("Host", "")
        if origin and origin != expected:
            return self._json(403, {"error": "Cross-origin request blocked"})
        if self.headers.get("X-Awear-Local") != "1":
            return self._json(403, {"error": "Local request header required"})
        try:
            route = urllib.parse.urlsplit(self.path).path
            desktop = self.server.desktop
            if desktop and not desktop.authorized(self):
                return self._json(403, {"error": "Open the desktop launcher to access this workspace."})
            length = int(self.headers.get("Content-Length", "0"))
            if desktop and route == "/api/desktop/restore":
                return desktop.restore_workspace(self, length)
            if desktop and route == "/api/desktop/import":
                return desktop.import_archive(self, length)
            if length < 0 or length > MAX_BODY:
                raise ValueError("Request too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            if desktop and desktop.post(self, route, body):
                return
            if desktop and not desktop.ready():
                raise ValueError("Complete desktop setup first.")
            if route == '/api/discovery/decision':
                from discovery_decisions import save
                return self._json(200,save(self.server.analysis,self._selected(),body))
            if route in ('/api/discovery/context/save','/api/discovery/context/undo'):
                from discovery_context import mutate
                return self._json(200,mutate(self.server.analysis,self._selected(),body,route.endswith('/undo')))
            if route in ('/api/similarity/search','/api/similarity/accept'):
                import similar_periods
                participant=self._selected()
                result=similar_periods.search(self.server.analysis,participant,body)
                if route.endswith('/search'):return self._json(200,result)
                item=similar_periods.proposal(result,body)
                reference=result['reference']['label']
                label=str(body.get('label','')).strip()
                if not label:raise ValueError('Enter the activity you performed')
                adopted=label==reference['label']
                def validate_proposal(connection):
                    current=similar_periods.search(self.server.analysis,participant,body,connection)
                    verified=similar_periods.proposal(current,body)
                    return {'kind':'similar-period-review','retrievalVersion':similar_periods.VERSION,
                            'explorationIdentity':result['explorationIdentity'],'acquisition':result['acquisition'],
                            'view':result['view'],'metric':result['metric'],'reference':result['reference'],
                            'spacingMinutes':result['spacingMinutes'],'guardMs':result['guardMs'],'match':verified}
                saved=add_label(participant,label,'',item['startMs'],item['endMsExclusive'],
                    activity_id=reference['activity_id'] if adopted else None,expected_participant=participant,
                    evidence={'reviewState':'confirmed','timingPrecision':'unknown'},
                    origin='model' if adopted else 'manual',context_provider=validate_proposal)
                return self._json(201,{'id':saved,'message':'Activity saved. Undo is available in Episode review history. Run analysis to refresh the changed archive.'})
            if route in ('/api/review/action','/api/review/split','/api/review/undo'):
                participant=self._selected()
                candidates=self._review_candidates(participant) if body.get('cues') is True else []
                with db() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if not participant or body.get('participantId')!=participant or setting(connection,'participant_id')!=participant:raise ValueError('Participant changed. Refresh before saving.')
                    if route.endswith('/split'):
                        result=episode_review.split(connection,participant,body['labelId'],utc_ms(body['cut']),body['labelVersion'])
                    elif route.endswith('/undo'):
                        result=episode_review.undo(connection,participant,body['operationId'])
                    else:
                        page=episode_review.queue(connection,participant,candidates=candidates,task_key=body['key'])
                        if not page['items']:raise ValueError('This review item changed. Refresh the queue.')
                        task=page['items'][0]
                        if body['action']=='confirm':
                            if not task.get('labelId'):raise ValueError('Enter your own activity before confirming this period')
                            row=episode_review.require_label(connection,participant,task['labelId'],task['labelVersion'])
                            before=episode_review.snapshot(connection,participant)
                            connection.execute("UPDATE labels SET review_state='confirmed',reviewed=1 WHERE id=?",(row['id'],))
                            result={'operationId':episode_review.record(connection,participant,'confirm',before,context=task)}
                        else:result=episode_review.decide(connection,participant,task,body['action'],body.get('note',''))
                return self._json(200,result)
            if route == "/api/activities":
                with db() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    participant = setting(connection,"participant_id")
                    if body.get("participantId",participant) != participant:raise ValueError("Participant changed. Refresh before saving this activity.")
                    result = personal_activities.create(connection,participant,body)
                return self._json(201,result)
            activity_edit = re.fullmatch(r"/api/activities/([0-9a-f]{32})/edit",route)
            if activity_edit:
                with db() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    participant = setting(connection,"participant_id")
                    if body.get("participantId",participant) != participant:raise ValueError("Participant changed. Refresh before saving this activity.")
                    result = personal_activities.update(connection,participant,activity_edit[1],body)
                return self._json(200,result)
            if route == "/api/discovery/questions":
                from discovery_questions import save
                return self._json(200,save(self.server.analysis,self._selected(),body))
            if route == "/api/analysis/start":
                return self._json(202, self.server.analysis.start(self._selected(), body))
            if route == "/api/analysis/cancel":
                return self._json(200, self.server.analysis.cancel())
            if route == "/api/select":
                if self.server.collector.running or self.server.analysis.running():
                    raise ValueError("Wait for the current analysis or sync to finish before changing the source")
                participant = str(body.get("participantId", ""))
                if participant not in {m.get("participantId") for m in self.server.collector.participants()}:
                    raise ValueError("Choose a participant returned by your AWEAR key")
                start_ms = utc_ms(body["syncStart"])
                if start_ms >= int(time.time()*1000) or start_ms < 0:
                    raise ValueError("Sync start must be in the past")
                with db() as connection:
                    put_setting(connection, "participant_id", participant)
                    put_setting(connection, "sync_start_ms", str(start_ms))
                    put_setting(connection, "sync_checkpoint_ms", str(start_ms))
                self.server.collector.start_sync()
                return self._json(200, {"ok": True})
            if route == "/api/sync":
                return self._json(200, {"started": self.server.collector.start_sync()})
            if route == "/api/bluetooth/scan":
                return self._json(200, self.server.bluetooth.scan())
            if route == "/api/bluetooth/start":
                return self._json(201, self.server.bluetooth.start(body.get("deviceId", ""),
                                  body.get("seconds", 60), self._selected(), body.get("placement", "unknown")))
            if route == "/api/bluetooth/stop":
                return self._json(200, self.server.bluetooth.stop())
            if route == "/api/mobile-scores" or re.fullmatch(r"/api/mobile-scores/\d+/edit", route):
                score_id = int(route.split("/")[3]) if route.endswith("/edit") else None
                score_id = save_mobile_score(self._selected(), utc_ms(body["observedAt"]), body.get("score"),
                    body.get("state"), body.get("uncertaintySeconds", 0), body.get("appVersion", ""),
                    body.get("note", ""), score_id)
                return self._json(200 if route.endswith("/edit") else 201, {"id": score_id})
            if re.fullmatch(r"/api/mobile-scores/\d+/delete", route):
                delete_mobile_score(self._selected(), int(route.split("/")[3]))
                return self._json(200, {"ok": True})
            if route == "/api/labels" or re.fullmatch(r"/api/labels/\d+/edit", route):
                participant = self._selected()
                start_ms = utc_ms(body["start"])
                end_ms = utc_ms(body["end"]) if body.get("end") else None
                label_id = int(route.split("/")[3]) if route.endswith("/edit") else None
                note=str(body.get("note", ""))
                if label_id is not None and body.get('preserveNote') is True:
                    with db() as connection:
                        previous=connection.execute('SELECT note FROM labels WHERE id=? AND participant_id=?',(label_id,participant)).fetchone()
                        if previous is None:raise ValueError('Label not found')
                        note=previous[0]
                saved_id = add_label(participant, str(body.get("label", "")), note, start_ms, end_ms, label_id,body.get("activityId"),body.get("participantId"),body.get("evidence"),body.get("labelVersion"),body.get("reviewKey"),self._review_candidates if body.get("cues") is True else None)
                return self._json(200 if label_id is not None else 201, {"id": saved_id})
            if route.startswith("/api/labels/") and route.endswith("/stop"):
                label_id = int(route.split("/")[3])
                end_ms = utc_ms(body["end"])
                with db() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    participant=self._selected()
                    before=episode_review.snapshot(connection,participant)
                    row = connection.execute("SELECT start_ms FROM labels WHERE id=? AND participant_id=? AND end_ms IS NULL",
                                             (label_id, self._selected())).fetchone()
                    if not row:
                        raise ValueError("Active label not found")
                    if end_ms <= row[0]:
                        raise ValueError("End must be later than start")
                    connection.execute("UPDATE labels SET end_ms=?,end_uncertainty_ms=CASE WHEN timing_precision='exact' THEN 0 ELSE NULL END WHERE id=?", (end_ms, label_id))
                    episode_review.record(connection,participant,"stop",before)
                return self._json(200, {"ok": True})
            if route.startswith("/api/labels/") and route.endswith("/delete"):
                label_id = int(route.split("/")[3])
                with db() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    participant=self._selected()
                    before=episode_review.snapshot(connection,participant)
                    result = connection.execute("DELETE FROM labels WHERE id=? AND participant_id=?",
                                                (label_id, self._selected()))
                    if result.rowcount != 1:
                        raise ValueError("Label not found")
                    episode_review.record(connection,participant,"delete",before)
                return self._json(200, {"ok": True})
            self._json(404, {"error": "Not found"})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
        except RuntimeError as error:
            self._json(502, {"error": str(error)})


class AppServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], collector: Collector, desktop=None, worker_command=None):
        from profile_recovery import WorkspaceGate
        self.workspace_gate = WorkspaceGate()
        self.collector = collector
        self.desktop = desktop
        self.analysis = AnalysisManager(data_root(), DB_PATH, demo=collector.demo)
        self.bluetooth = BluetoothManager(ROOT, data_directory=data_root(), worker_command=worker_command)
        super().__init__(address, Handler)

    def server_bind(self) -> None:
        # Local-only service: do not block launch on a reverse DNS lookup.
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]

    def server_close(self) -> None:
        self.analysis.close()
        self.bluetooth.close()
        super().server_close()


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if message.startswith("unrecognized arguments:"):
            self.exit(2, "error: unexpected argument. Run `python3 src/app.py --key-prompt` with nothing after it. "
                         "When `AWEAR API key:` appears, paste your key and press Return.\n")
        super().error(message)


def main() -> None:
    global DB_PATH
    parser = SafeArgumentParser(description="Local AWEAR EEG dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--key-prompt", action="store_true", help="Read API key without echoing or saving it")
    parser.add_argument("--demo", action="store_true", help="Use clearly marked synthetic sample data")
    args = parser.parse_args()
    if args.demo:
        DB_PATH = ROOT / "awear_demo.sqlite3"
    key = "" if args.demo else getpass.getpass("AWEAR API key: ") if args.key_prompt else read_api_key()
    setup_db()
    if args.demo:
        demo_seed()
    collector = Collector(key, args.demo)
    server = AppServer(("127.0.0.1", args.port), collector)
    threading.Thread(target=collector.loop, daemon=True).start()
    print(f"AWEAR dashboard: http://127.0.0.1:{args.port} (local only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
