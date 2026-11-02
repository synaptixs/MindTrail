import copy
import importlib.util
import math
import sqlite3
import unittest

if importlib.util.find_spec("numpy") and importlib.util.find_spec("sklearn"):
    import numpy as np
    import analyze_patterns as patterns
else:
    np = None
    patterns = None


@unittest.skipUnless(patterns is not None, "Optional NumPy and scikit-learn are not installed")
class PatternAnalysisTests(unittest.TestCase):
    def test_spectral_features_find_known_tone(self):
        time = np.arange(1024) / 256
        _, alpha = patterns.features(np.sin(2 * math.pi * 10 * time))
        _, delta = patterns.features(np.sin(2 * math.pi * 2 * time))
        self.assertGreater(alpha[2], .9)
        self.assertGreater(delta[0], .9)

    def test_extract_never_joins_gap_or_device_change(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("""CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,
            device_id TEXT,waveform_json TEXT,sample_count INTEGER)""")
        waveform = "[" + ",".join(str(math.sin(2*math.pi*10*i/256)) for i in range(256)) + "]"
        rows = [("P", i*1000, "A", waveform, 256) for i in range(4)]
        rows += [("P", (i+5)*1000, "A", waveform, 256) for i in range(4)]
        rows += [("P", (i+9)*1000, "B", waveform, 256) for i in range(4)]
        connection.executemany("INSERT INTO readings VALUES (?,?,?,?,?)", rows)
        windows, stats = patterns.extract(connection, "P")
        self.assertEqual(len(windows), 3)
        self.assertEqual([row["deviceId"] for row in windows], ["A", "A", "B"])
        self.assertEqual(stats["discontinuities"], 2)

    def test_extract_does_not_cross_labelable_half_hour(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("""CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,
            device_id TEXT,waveform_json TEXT,sample_count INTEGER)""")
        waveform = "[" + ",".join(str(i) for i in range(256)) + "]"
        boundary = 30 * 60 * 1000
        connection.executemany("INSERT INTO readings VALUES (?,?,?,?,?)", [
            ("P", boundary + offset*1000, "A", waveform, 256) for offset in range(-2, 6)])
        windows, stats = patterns.extract(connection, "P")
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["start"], patterns.app.iso(boundary))
        self.assertEqual(stats["periodBoundaries"], 1)

    def test_fit_flags_extreme_window_and_is_repeatable(self):
        rng = np.random.default_rng(8)
        windows = []
        for index in range(60):
            first = index < 30
            noise = rng.normal(0, .04, 5)
            shares = np.array([.2,.1,.1,.4,.2] if first else [.65,.12,.06,.12,.05]) + noise
            shares = np.maximum(shares, .01)
            shares /= shares.sum()
            windows.append({"start": f"2026-05-01T00:{index:02d}:00.000Z", "deviceId": "A",
                            "rms": (10 if first else 25) + float(rng.normal(0, 1)),
                            "peakToPeak": (50 if first else 110) + float(rng.normal(0, 3)),
                            "maxStep": 8 + float(rng.random()), "bandShares": shares.tolist(),
                            "vector": [float(math.log10(10 if first else 25)),
                                       *np.log10(shares[1:] + 1e-6).tolist()]})
        windows[-1]["rms"] = 1000
        windows[-1]["peakToPeak"] = 5000
        first = patterns.fit(copy.deepcopy(windows))
        second = patterns.fit(copy.deepcopy(windows))
        self.assertEqual(first["windows"], second["windows"])
        self.assertIn("highAmplitude", first["windows"][-1]["qualityFlags"])
        self.assertIsNone(first["windows"][-1]["gmmPattern"])
        self.assertEqual(len(first["comparison"]), 2)


if __name__ == "__main__":
    unittest.main()
