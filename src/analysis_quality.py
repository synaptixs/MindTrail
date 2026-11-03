"""Read-only cloud EEG feature extraction and auditable screening (NumPy required).

No clustering or activity inference is performed.
Quality flags are screening cues, not verified artifact diagnoses.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import numpy as np

import analysis_provenance as provenance

BANDS = ((1, 4), (4, 8), (8, 13), (13, 30), (30, 40.01))
BAND_NAMES = ("delta", "theta", "alpha", "beta", "30–40 Hz")
METHOD_VERSION = "quality-v1"


def power_spectrum(samples: np.ndarray, sample_rate: int) -> dict:
    """Linear detrend, symmetric Hann and a one-sided density in source units²/Hz.

    rawBandPower preserves the legacy unnormalized FFT sums. The density follows
    the standard periodogram scaling; DC/Nyquist are not doubled.
    """
    if type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError("Sample rate must be a positive integer")
    if np.iscomplexobj(samples):
        raise ValueError("Spectrum requires real samples")
    samples = np.asarray(samples, dtype=np.float64)
    n = samples.size
    if samples.ndim != 1 or n < 4 or not np.isfinite(samples).all():
        raise ValueError("Spectrum requires a finite one-dimensional signal")
    x = np.arange(n, dtype=np.float64) - (n - 1) / 2
    centered = samples - samples.mean()
    slope = np.dot(x, centered) / np.dot(x, x)
    detrended = centered - slope * x
    taper = np.hanning(n)
    fft = np.fft.rfft(detrended * taper)
    power = np.abs(fft) ** 2
    frequencies = np.fft.rfftfreq(n, 1 / sample_rate)
    raw_bands = np.array([power[(frequencies >= low) & (frequencies < high)].sum()
                          for low, high in BANDS])
    density = power / (sample_rate * np.dot(taper, taper))
    density[1:-1 if n % 2 == 0 else None] *= 2
    return {"frequencies": frequencies, "density": density, "rawBandPower": raw_bands,
            "centered": centered, "trend": slope * x, "slopePerSecond": float(slope * sample_rate)}


def feature_summary(samples: np.ndarray, sample_rate: int) -> dict:
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        spectrum = power_spectrum(samples, sample_rate)
        f, density = spectrum["frequencies"], spectrum["density"]
        df = sample_rate / len(samples)
        bands = np.array([density[(f >= low) & (f < high)].sum() * df for low, high in BANDS])
        total = float(bands.sum())
        shares = bands / total if total > 0 else np.zeros(len(bands))
        variance = float(np.mean(spectrum["centered"] ** 2))
        broad = float(density[(f >= 1) & (f <= min(100, sample_rate / 2))].sum())
        mains = float(density[(np.abs(f - 50) <= 1) | (np.abs(f - 60) <= 1)].sum())
        result = {"acRms": math.sqrt(variance), "peakToPeak": float(np.ptp(samples)),
                  "maxStep": float(np.max(np.abs(np.diff(samples)))),
                  "linearSlopePerSecond": spectrum["slopePerSecond"],
                  "linearTrendFraction": float(np.mean(spectrum["trend"] ** 2)) / variance if variance > 0 else 0.0,
                  "mainsLikePowerFraction": mains / broad if broad > 0 else 0.0,
                  "power1to40Hz": total, "log10Power1to40Hz": math.log10(total + 1e-12),
                  "bandPowers": bands.tolist(), "bandShares": shares.tolist()}
        provenance.canonical_json(result)  # No NaN/Infinity is published.
        return result


def valid_features(value: dict) -> bool:
    scalars = {"acRms", "peakToPeak", "maxStep", "linearSlopePerSecond",
               "linearTrendFraction", "mainsLikePowerFraction", "power1to40Hz", "log10Power1to40Hz"}
    if set(value) != scalars | {"bandPowers", "bandShares"}:return False
    def finite(v):return type(v) in (int,float) and math.isfinite(v)
    return all(finite(value[k]) for k in scalars) and all(
        isinstance(value[k],list) and len(value[k])==5 and all(finite(v) for v in value[k])
        for k in ("bandPowers","bandShares"))


@dataclass(frozen=True)
class QualityPolicy:
    version: str = "relative-screen-v1"
    flatline_peak_to_peak: float = 1e-6
    relative_mad_multiplier: float = 8.0
    minimum_reference_windows: int = 8
    mains_like_fraction: float = .25
    linear_trend_fraction: float = .8

    def __post_init__(self) -> None:
        if self.version != "relative-screen-v1":
            raise ValueError("Unsupported quality policy version")
        for value in (self.flatline_peak_to_peak, self.relative_mad_multiplier):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("Screening thresholds must be finite and positive")
        for value in (self.mains_like_fraction, self.linear_trend_fraction):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError("Fraction thresholds must be in (0, 1]")
        if type(self.minimum_reference_windows) is not int or self.minimum_reference_windows < 3:
            raise ValueError("Relative screening needs at least three reference windows")


def implementation_identity() -> dict:
    return {"qualitySource": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "provenanceSource": provenance.implementation_fingerprint(), "numpy": np.__version__}


class QualityExtractor:
    """Acquisition-aware core; the CLI currently supplies cloud records only."""
    def __init__(self, spec: provenance.AnalysisSpec, policy: QualityPolicy,
                 implementation: str, end_ms: int | None = None, *, feature_cache=None,
                 check_cancel=None, progress=None):
        if spec.quality_policy_version != policy.version:
            raise ValueError("Specification and screening policy versions differ")
        if spec.sample_rate / 2 < 40:
            raise ValueError("Sample rate cannot represent the requested 1–40 Hz features")
        self.spec, self.policy, self.implementation = spec, policy, implementation
        self.end_ms = end_ms
        self.feature_cache = feature_cache
        self.check_cancel = check_cancel or (lambda: None)
        self.progress = progress or (lambda stage,done,total: None)
        self.total_records = 0
        self.source_records = self.eligible_records = self.failure_windows = 0
        self.excluded = Counter()
        self.boundaries = Counter()
        self.run = []
        self.windows = []
        self.failures = []
        self.exclusion_spans = []
        self.remainder_spans = []
        self.pending = []
        self.pending_key = None
        self.last_times = {}
        self.unrecorded_ms = 0

    def feed(self, record: provenance.SourceRecord) -> None:
        self.feed_raw(record.context, (record.timestamp_ms, record.context.device_id,
                                      record.sample_count, record.waveform_json))

    def feed_raw(self, context: provenance.AcquisitionContext, row: tuple) -> None:
        self.check_cancel()
        self.source_records += 1
        if self.source_records % 128 == 0 or self.source_records == self.total_records:
            self.progress("extracting",self.source_records,self.total_records)
        timestamp = row[0]
        if type(timestamp) is not int or timestamp < 0:
            self._flush_pending()
            self._exclude("invalid_timestamp", 1, context, None)
            return
        # The cloud schema promises one record per participant/time. Ambiguous
        # duplicates are excluded together, including those naming different devices.
        stream = replace(context, device_id=None) if context.source == "awear-cloud" else context
        key = (stream.group_id, timestamp)
        if key != self.pending_key:
            self._flush_pending()
            self.pending_key = key
        self.pending.append((context, row))

    def _break(self, reason: str) -> None:
        if self.run:
            self.remainder_spans.append({"reason":reason,"recordCount":len(self.run),
                "startMs":self.run[0].timestamp_ms,"endMsExclusive":self.run[-1].timestamp_ms+1000,
                "acquisition":asdict(self.run[0].context)})
        self.run.clear()

    def _exclude(self, reason: str, count: int, context: provenance.AcquisitionContext,
                 timestamp: int | None) -> None:
        self.excluded[reason] += count
        self._break("excluded_record:"+reason)
        end = timestamp+1000 if timestamp is not None else None
        span = {"reason":reason,"recordCount":count,"startMs":timestamp,"endMsExclusive":end,
                "acquisition":asdict(context)}
        prior = self.exclusion_spans[-1] if self.exclusion_spans else None
        if (prior is not None and timestamp is not None and prior["endMsExclusive"]==timestamp
                and prior["reason"]==reason and prior["acquisition"]==span["acquisition"]):
            prior["recordCount"] += count
            prior["endMsExclusive"] = end
        else:
            self.exclusion_spans.append(span)

    def _flush_pending(self) -> None:
        if not self.pending:
            return
        stream, timestamp = self.pending_key
        prior = self.last_times.get(stream)
        if prior is not None:
            self.unrecorded_ms += max(0, timestamp - prior - 1000)
        self.last_times[stream] = timestamp
        batch, self.pending = self.pending, []
        if len(batch) > 1:
            self._exclude("duplicate_timestamp", len(batch), batch[0][0], timestamp)
            return
        context, row = batch[0]
        try:
            context = replace(context, device_id=row[1])
        except ValueError:
            self._exclude("invalid_acquisition_metadata", 1, context, timestamp)
            return
        record = provenance.SourceRecord(context, row[0], row[3], row[2])
        if type(record.sample_count) is not int or record.sample_count != context.sample_rate:
            self._exclude("invalid_sample_count", 1, context, timestamp)
            return
        try:
            record.validate()
        except ValueError:
            self._exclude("invalid_waveform", 1, context, timestamp)
            return
        if context.device_id is None:
            self._exclude("unknown_device", 1, context, timestamp)
            return
        if context.sample_rate != self.spec.sample_rate:
            self._exclude("sample_rate_mismatch", 1, context, timestamp)
            return
        if self.end_ms is not None and record.timestamp_ms + 1000 > self.end_ms:
            self._exclude("partial_range_record", 1, context, timestamp)
            return
        self.eligible_records += 1
        if self.run:
            if self.run[-1].context != context:
                self.boundaries["acquisition_change"] += 1
                self._break("acquisition_change")
            elif record.timestamp_ms != self.run[-1].timestamp_ms + 1000:
                self.boundaries["cadence_break"] += 1
                self._break("cadence_break")
        self.run.append(record)
        if len(self.run) != self.spec.window_seconds:
            return
        window = provenance.make_window_provenance(self.run, self.spec, self.implementation)
        try:
            self.check_cancel()
            cached = self.feature_cache.get(window["windowId"]) if self.feature_cache else None
            if cached is not None and not valid_features(cached):
                self.feature_cache.hits -= 1
                self.feature_cache.misses += 1
                cached = None
            if cached is None:
                samples = np.concatenate([np.asarray(json.loads(item.waveform_json), dtype=np.float64) for item in self.run])
                cached = feature_summary(samples, self.spec.sample_rate)
                self.check_cancel()
                if self.feature_cache:self.feature_cache.put(window["windowId"],cached)
            window["features"] = cached
        except (FloatingPointError, OverflowError, ValueError):
            self.failure_windows += 1
            self.failures.append({"provenance":window,"reason":"numeric_range"})
        else:
            self.windows.append(window)
        self.run.clear()

    def finish(self) -> dict:
        self.check_cancel()
        self._flush_pending()
        self._break("end_of_selected_input")
        groups = defaultdict(list)
        for window in self.windows:
            groups[window["acquisitionGroupId"]].append(window)
        summaries = []
        occurrences = Counter()
        for group_number,group_id in enumerate(sorted(groups)):
            self.check_cancel()
            self.progress("screening",group_number,len(groups))
            windows = groups[group_id]
            sufficient = len(windows) >= self.policy.minimum_reference_windows
            cutoffs = {}
            if sufficient:
                for name in ("acRms", "maxStep"):
                    values = np.array([window["features"][name] for window in windows])
                    median = float(np.median(values))
                    mad = float(np.median(np.abs(values - median)))
                    scale = max(1.4826 * mad, 1e-9 * max(abs(median), 1))
                    cutoffs[name] = median + self.policy.relative_mad_multiplier * scale
            for index,window in enumerate(windows):
                if index % 128 == 0:self.check_cancel()
                values = window["features"]
                flags = []
                if values["peakToPeak"] <= self.policy.flatline_peak_to_peak: flags.append("flatline")
                if values["mainsLikePowerFraction"] > self.policy.mains_like_fraction: flags.append("mains_like_power")
                if values["linearTrendFraction"] > self.policy.linear_trend_fraction: flags.append("linear_trend_dominant")
                if sufficient and values["acRms"] > cutoffs["acRms"]: flags.append("relative_high_amplitude")
                if sufficient and values["maxStep"] > cutoffs["maxStep"]: flags.append("relative_large_step")
                window["qualityFlags"] = flags
                occurrences.update(flags)
            baseline = [window for window in windows if not window["qualityFlags"]]
            normalization = {}
            if self.spec.normalization == "within-acquisition-group-robust" and baseline:
                for name in ("acRms", "log10Power1to40Hz"):
                    values = np.array([window["features"][name] for window in baseline])
                    median = float(np.median(values))
                    mad = float(np.median(np.abs(values-median)))
                    normalization[name] = {"median": median, "scale": max(1.4826*mad,1e-9*max(abs(median),1))}
            for index,window in enumerate(windows):
                if index % 128 == 0:self.check_cancel()
                window["normalizedFeatures"] = {name:(window["features"][name]-ref["median"])/ref["scale"]
                    for name,ref in normalization.items()} or None
            summaries.append({"acquisitionGroupId":group_id,"acquisition":windows[0]["acquisition"],
                "computedWindows":len(windows),"relativeScreeningAvailable":sufficient,"relativeCutoffs":cutoffs,
                "normalizationReferenceWindows":len(baseline) if normalization else 0,"normalization":normalization,
                "referenceWindowRevision":provenance.fingerprint([window["windowId"] for window in windows])})
        computed = len(self.windows)
        self.progress("screening",len(groups),len(groups))
        self.check_cancel()
        flagged = sum(bool(window["qualityFlags"]) for window in self.windows)
        remainder = self.eligible_records-(computed+self.failure_windows)*self.spec.window_seconds
        coverage = {"sourceRecords":self.source_records,"excludedRecords":sum(self.excluded.values()),
            "excludedByPrimaryReason":dict(sorted(self.excluded.items())),"eligibleRecords":self.eligible_records,
            "computedWindows":computed,"computedRecordSeconds":computed*self.spec.window_seconds,
            "featureFailureWindows":self.failure_windows,"featureFailureRecordSeconds":self.failure_windows*self.spec.window_seconds,
            "boundaryRemainderRecordSeconds":remainder,"flaggedWindows":flagged,"unflaggedWindows":computed-flagged,
            "flagOccurrences":dict(sorted(occurrences.items())),"unrecordedSpanMilliseconds":self.unrecorded_ms,
            "boundaryEvents":dict(sorted(self.boundaries.items()))}
        if self.source_records != coverage["excludedRecords"]+coverage["computedRecordSeconds"]+coverage["featureFailureRecordSeconds"]+remainder:
            raise RuntimeError("Extraction coverage does not reconcile")
        if (sum(span["recordCount"] for span in self.exclusion_spans) != coverage["excludedRecords"]
                or sum(span["recordCount"] for span in self.remainder_spans) != remainder):
            raise RuntimeError("Coverage spans do not reconcile")
        return {"coverage":coverage,"groups":summaries,"windows":self.windows,"failedWindows":self.failures,
                "excludedSpans":self.exclusion_spans,"remainderSpans":self.remainder_spans}


def analyze_archive(database: Path, participant: str | None = None, *,
                    spec: provenance.AnalysisSpec | None = None, policy: QualityPolicy | None = None,
                    start_ms: int | None = None, end_ms: int | None = None,
                    feature_cache=None, check_cancel=None, progress=None) -> dict:
    policy = policy or QualityPolicy()
    spec = spec or provenance.AnalysisSpec(quality_policy_version=policy.version)
    implementation = implementation_identity()
    implementation_key = provenance.fingerprint({"implementation":implementation,"policy":asdict(policy),"method":METHOD_VERSION})
    extractor = QualityExtractor(spec,policy,implementation_key,end_ms,feature_cache=feature_cache,check_cancel=check_cancel,progress=progress)
    def started(total):
        extractor.total_records=total
        extractor.check_cancel()
        extractor.progress("extracting",0,total)
    inventory = provenance.inspect_archive(database,participant,spec,on_record=extractor.feed_raw,start_ms=start_ms,end_ms=end_ms,on_start=started)
    if inventory["recordCount"] != extractor.source_records:
        raise RuntimeError("Source inventory and extraction snapshot differ")
    result = extractor.finish()
    identity = provenance.analysis_identity(inventory["sourceRevision"],spec,implementation_key)
    for window in result["windows"]:window["qualityAnalysisIdentity"] = identity
    result.update({"reportVersion":1,"methodVersion":METHOD_VERSION,"analysisIdentity":identity,
        "sourceInventory":inventory,"spec":asdict(spec),"qualityPolicy":asdict(policy),"implementation":implementation,
        "scope":"cloud EEG only; Bluetooth files, activity inference and clinical interpretation are not included",
        "bandNames":list(BAND_NAMES),"bandEdgesHz":[list(band) for band in BANDS],
        "featureUnits":{"acRms":"source units","peakToPeak":"source units","maxStep":"source units (adjacent-sample difference)",
            "linearSlopePerSecond":"source units/second","power1to40Hz":"source units squared",
            "bandPowers":"source units squared","bandShares":"fraction of 1–40 Hz power",
            "spectralDensity":"source units squared/Hz","fractions":"unitless",
            "normalizedFeatures":"unitless robust deviations",
            "log10Power1to40Hz":"log10 relative to one source unit squared"},
        "limitations":["Screening flags do not establish artifacts; unflagged does not mean artifact-free",
            "Clipping is not screened without known acquisition limits","Relative references use this exploratory snapshot, not training folds",
            "Gap duration is within observed spans, not device-wearing duration"]})
    provenance.canonical_json(result)
    return result


def _epoch(value: str | None) -> int | None:
    if value is None:return None
    dt = datetime.fromisoformat(value.replace("Z","+00:00"))
    if dt.tzinfo is None:raise ValueError("Range times must include a timezone")
    return int(dt.timestamp()*1000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database",type=Path)
    parser.add_argument("--participant")
    parser.add_argument("--start",help="Inclusive ISO 8601 record start, with timezone")
    parser.add_argument("--end",help="Exclusive ISO 8601 boundary, with timezone")
    parser.add_argument("--normalize",action="store_true",help="Add within-acquisition-group robust feature deviations")
    parser.add_argument("--output",type=Path,required=True,help="New JSON report; existing files are never overwritten")
    args = parser.parse_args()
    try:
        spec = provenance.AnalysisSpec(normalization="within-acquisition-group-robust" if args.normalize else "none",quality_policy_version=QualityPolicy().version)
        report = analyze_archive(args.database,args.participant,spec=spec,start_ms=_epoch(args.start),end_ms=_epoch(args.end))
        text = json.dumps(report,ensure_ascii=False,allow_nan=False,separators=(",",":"))+"\n"
        with args.output.open("x",encoding="utf-8") as output:output.write(text)
    except (OSError,ValueError,sqlite3.Error,RuntimeError) as error:
        parser.exit(1,f"Analysis failed: {error}\n")


if __name__ == "__main__":main()
