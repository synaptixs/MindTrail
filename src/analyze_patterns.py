#!/usr/bin/env python3
"""Exploratory, reproducible clustering of contiguous AWEAR EEG windows.

This optional offline command needs NumPy and scikit-learn. The live dashboard
continues to use only the Python standard library. It never edits raw readings.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import RobustScaler

import app

SAMPLE_RATE = 256
WINDOW_SECONDS = 4
BANDS = ((1, 4), (4, 8), (8, 13), (13, 30), (30, 40.01))
SEED = 17


def features(samples: np.ndarray) -> tuple[list[float], list[float]]:
    """Return model inputs and descriptive 1–40 Hz band shares."""
    n = samples.size
    x = np.arange(n, dtype=np.float64) - (n - 1) / 2
    centered = samples - samples.mean()
    slope = np.dot(x, centered) / np.dot(x, x)
    detrended = centered - slope * x
    fft = np.fft.rfft(detrended * np.hanning(n))
    power = np.abs(fft) ** 2
    frequencies = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)
    band_power = np.array([power[(frequencies >= low) & (frequencies < high)].sum()
                           for low, high in BANDS])
    total = float(band_power.sum())
    shares = band_power / max(total, 1e-12)
    # Delta is implicit because the five shares sum to one. Log power keeps
    # amplitude information without allowing the largest transients to dominate.
    vector = [math.log10(total + 1e-12), *np.log10(shares[1:] + 1e-6)]
    return [float(value) for value in vector], [float(value) for value in shares]


def extract(connection: sqlite3.Connection, participant: str) -> tuple[list[dict], dict]:
    windows: list[dict] = []
    run: list[np.ndarray] = []
    prior: tuple[int, str | None] | None = None
    stats = {"invalidRecords": 0, "discontinuities": 0, "periodBoundaries": 0,
             "shortRunSeconds": 0}
    cursor = connection.execute("""SELECT timestamp_ms, device_id, waveform_json, sample_count
        FROM readings WHERE participant_id=? ORDER BY timestamp_ms""", (participant,))

    def flush() -> None:
        if run:
            stats["shortRunSeconds"] += len(run)
            run.clear()

    for timestamp, device_id, waveform_json, sample_count in cursor:
        try:
            samples = np.asarray(json.loads(waveform_json), dtype=np.float64)
        except (ValueError, TypeError):
            samples = np.array([])
        if sample_count != SAMPLE_RATE or samples.shape != (SAMPLE_RATE,) or not np.isfinite(samples).all():
            stats["invalidRecords"] += 1
            flush()
            prior = None
            continue
        if prior and (timestamp != prior[0] + 1000 or device_id != prior[1]):
            stats["discontinuities"] += 1
            flush()
        elif prior and timestamp // app.ACTIVITY_INTERVAL_MS != prior[0] // app.ACTIVITY_INTERVAL_MS:
            # A four-second window should belong wholly to one labelable slot.
            stats["periodBoundaries"] += 1
            flush()
        run.append(samples)
        prior = (timestamp, device_id)
        if len(run) != WINDOW_SECONDS:
            continue
        joined = np.concatenate(run)
        centered = joined - joined.mean()
        vector, shares = features(joined)
        windows.append({
            "start": app.iso(timestamp - (WINDOW_SECONDS - 1) * 1000),
            "deviceId": device_id,
            "rms": float(np.sqrt(np.mean(centered ** 2))),
            "peakToPeak": float(np.ptp(joined)),
            "maxStep": float(np.max(np.abs(np.diff(joined)))),
            "bandShares": shares,
            "vector": vector,
        })
        run.clear()
    flush()
    return windows, stats


def ordered_labels(labels: np.ndarray, shares: np.ndarray, count: int) -> np.ndarray:
    """Order pattern numbers by median delta share for stable display colors."""
    order = sorted(range(count), key=lambda label: (float(np.median(shares[labels == label, 0])), label))
    mapping = {label: rank + 1 for rank, label in enumerate(order)}
    return np.asarray([mapping[label] for label in labels], dtype=np.int16)


def fit(windows: list[dict]) -> dict:
    if len(windows) < 30:
        raise ValueError("At least 30 contiguous four-second windows are needed")
    rms = np.asarray([row["rms"] for row in windows])
    p2p = np.asarray([row["peakToPeak"] for row in windows])
    step = np.asarray([row["maxStep"] for row in windows])
    cutoffs = {"rms95": float(np.quantile(rms, .95)),
               "peakToPeak95": float(np.quantile(p2p, .95)),
               "maxStep99": float(np.quantile(step, .99))}
    clean = np.ones(len(windows), dtype=bool)
    reasons = {"highAmplitude": 0, "largeStep": 0, "flatline": 0}
    for index, row in enumerate(windows):
        flags = []
        if row["rms"] > cutoffs["rms95"] or row["peakToPeak"] > cutoffs["peakToPeak95"]:
            flags.append("highAmplitude")
        if row["maxStep"] > cutoffs["maxStep99"]:
            flags.append("largeStep")
        if row["peakToPeak"] < 1e-6:
            flags.append("flatline")
        row["qualityFlags"] = flags
        clean[index] = not flags
        for flag in flags:
            reasons[flag] += 1
    if clean.sum() < 30:
        raise ValueError("At least 30 unflagged windows are needed for clustering")

    input_values = np.asarray([row["vector"] for row in windows])[clean]
    shares = np.asarray([row["bandShares"] for row in windows])[clean]
    scaler = RobustScaler().fit(input_values)
    scaled = np.clip(scaler.transform(input_values), -6, 6)
    sample = np.linspace(0, len(scaled) - 1, min(1500, len(scaled)), dtype=int)
    comparison = []
    fitted = {}
    for count in (2, 3):
        gmm = GaussianMixture(n_components=count, covariance_type="diag", reg_covar=1e-4,
                              n_init=3, random_state=SEED).fit(scaled)
        km = KMeans(n_clusters=count, n_init=10, random_state=SEED).fit(scaled)
        gmm_raw = gmm.predict(scaled)
        km_raw = km.labels_
        gmm_labels = ordered_labels(gmm_raw, shares, count)
        km_labels = ordered_labels(km_raw, shares, count)
        gmm_sample, km_sample = gmm_labels[sample], km_labels[sample]
        comparison.append({
            "patterns": count,
            "gmmBic": round(float(gmm.bic(scaled)), 1),
            "gmmSilhouette": round(float(silhouette_score(scaled[sample], gmm_sample)), 3)
                             if len(set(gmm_sample)) > 1 else None,
            "kmeansSilhouette": round(float(silhouette_score(scaled[sample], km_sample)), 3)
                                if len(set(km_sample)) > 1 else None,
            "agreementAri": round(float(adjusted_rand_score(gmm_labels, km_labels)), 3),
        })
        fitted[count] = (gmm, gmm_labels, km_labels)

    model, gmm_labels, km_labels = fitted[2]
    probability = model.predict_proba(scaled).max(axis=1)
    profiles = []
    for label in (1, 2):
        selected = gmm_labels == label
        profiles.append({"pattern": label, "windows": int(selected.sum()),
                         "medianRms": round(float(np.median(rms[clean][selected])), 2),
                         "medianBandShares": [round(float(value), 4) for value in np.median(shares[selected], axis=0)]})
    good_indices = np.flatnonzero(clean)
    for position, index in enumerate(good_indices):
        windows[index]["gmmPattern"] = int(gmm_labels[position])
        windows[index]["kmeansPattern"] = int(km_labels[position])
        windows[index]["confidence"] = round(float(probability[position]), 3)
    for row in windows:
        row.setdefault("gmmPattern", None)
        row.setdefault("kmeansPattern", None)
        row.setdefault("confidence", None)
        row["rms"] = round(row["rms"], 3)
        row["peakToPeak"] = round(row["peakToPeak"], 3)
        row["maxStep"] = round(row["maxStep"], 3)
        row["bandShares"] = [round(value, 4) for value in row["bandShares"]]
        del row["vector"]
    return {"windows": windows, "quality": {"flaggedWindows": int((~clean).sum()),
                                           "reasons": reasons, "relativeCutoffs": cutoffs},
            "comparison": comparison, "profiles": profiles,
            "unflaggedWindows": int(clean.sum())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cluster local AWEAR EEG as exploratory patterns")
    parser.add_argument("--participant", help="Participant ID; defaults to the dashboard selection")
    parser.add_argument("--demo", action="store_true", help="Analyze the separate demo database")
    args = parser.parse_args()
    database = app.data_root() / ("awear_demo.sqlite3" if args.demo else "awear_data.sqlite3")
    if not database.exists():
        parser.error(f"Database not found: {database}")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.execute("BEGIN")  # Keep source counts and extracted records on one snapshot.
        participant = args.participant or (connection.execute(
            "SELECT value FROM settings WHERE key='participant_id'").fetchone() or [None])[0]
        if not participant:
            parser.error("Choose a participant in the dashboard or pass --participant")
        count, first, last = connection.execute("""SELECT COUNT(*),MIN(timestamp_ms),MAX(timestamp_ms)
            FROM readings WHERE participant_id=?""", (participant,)).fetchone()
        recorded_periods = connection.execute("""SELECT COUNT(DISTINCT timestamp_ms / ?)
            FROM readings WHERE participant_id=?""", (app.ACTIVITY_INTERVAL_MS, participant)).fetchone()[0]
        if not count:
            parser.error("No recordings for this participant")
        print(f"Reading {count:,} records for {participant}...", flush=True)
        windows, extraction = extract(connection, participant)
        extraction["recordedPeriods"] = recorded_periods
    result = fit(windows)
    result.update({"version": 1, "participantId": participant, "sourceDatabase": database.name,
                   "source": {"records": count, "first": app.iso(first), "last": app.iso(last)},
                   "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                   "method": {"channel": "RIGHT_TEMP", "sampleRate": SAMPLE_RATE,
                              "windowSeconds": WINDOW_SECONDS, "overlapSeconds": 0,
                              "model": "diagonal Gaussian mixture, two patterns",
                              "features": ["log total 1–40 Hz power", "log relative theta power",
                                           "log relative alpha power", "log relative beta power",
                                           "log relative 30–40 Hz power"], "seed": SEED},
                   "extraction": extraction})
    app.DB_PATH = database
    path = app.pattern_report_path(participant, for_write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(result, output, separators=(",", ":"), allow_nan=False)
    temporary.replace(path)
    print(f"Saved {len(windows):,} windows ({result['unflaggedWindows']:,} unflagged) to {path}")


if __name__ == "__main__":
    main()
