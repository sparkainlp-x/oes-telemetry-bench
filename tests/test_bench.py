from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oes_telemetry_bench.bench import (  # noqa: E402
    ValidationError,
    calibrate_threshold,
    detection_metrics,
    detector_scores,
    load_replay,
    run_benchmark,
    validate_split,
)


class FrameContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "frames.jsonl"

    @staticmethod
    def row(index: int, *, n: int = 32, drift: int = 0) -> dict[str, object]:
        stamp = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
        channels = [f"ch_{i:02d}" for i in range(n)]
        return {
            "timestamp": stamp,
            "channel_ids": channels,
            "channel_timestamps": [stamp] * n if not drift else [stamp] * (n - 1) + [
                (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index, milliseconds=1)).isoformat().replace("+00:00", "Z")
            ],
            "channels": [0.0] * n,
            "regime": "steady",
            "event_label": "none",
            "event_id": None,
        }

    def write_rows(self, rows: list[dict[str, object]]) -> None:
        self.path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")

    def test_accepts_exactly_synchronized_32_channel_frames(self) -> None:
        self.write_rows([self.row(0), self.row(1)])
        replay = load_replay(self.path)
        self.assertEqual(len(replay.channel_ids), 32)
        self.assertEqual(replay.cadence_seconds, 1.0)

    def test_rejects_31_and_33_channels(self) -> None:
        for count in (31, 33):
            with self.subTest(count=count):
                self.write_rows([self.row(0, n=count), self.row(1, n=count)])
                with self.assertRaisesRegex(ValidationError, "exactly 32"):
                    load_replay(self.path)

    def test_rejects_skewed_channel_timestamp(self) -> None:
        self.write_rows([self.row(0, drift=1), self.row(1)])
        with self.assertRaisesRegex(ValidationError, "exactly equal"):
            load_replay(self.path)

    def test_rejects_univariate_frames(self) -> None:
        with self.assertRaisesRegex(ValidationError, "native OES32 scoring requires shape"):
            detector_scores("oes32", np.zeros((10, 1)), warmup=2)

    def test_rejects_duplicate_channel_names(self) -> None:
        row = self.row(0)
        row["channel_ids"] = ["duplicate"] * 32
        self.write_rows([row, self.row(1)])
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            load_replay(self.path)

    def test_rejects_boolean_and_nonfinite_values(self) -> None:
        for bad in (True, float("inf")):
            with self.subTest(bad=bad):
                row0 = self.row(0)
                row0["channels"] = [bad] + [0.0] * 31
                self.write_rows([row0, self.row(1)])
                with self.assertRaises(ValidationError):
                    load_replay(self.path)

    def test_rejects_duplicate_json_keys(self) -> None:
        row = json.dumps(self.row(0), separators=(",", ":"))
        self.path.write_text(row[:-1] + ',"timestamp":"2026-01-01T00:00:00Z"}\n' + json.dumps(self.row(1)) + "\n")
        with self.assertRaisesRegex(ValidationError, "duplicate JSON key"):
            load_replay(self.path)

    def test_rejects_irregular_cadence(self) -> None:
        rows = [self.row(0), self.row(1), self.row(3)]
        self.write_rows(rows)
        with self.assertRaisesRegex(ValidationError, "regular sampling interval"):
            load_replay(self.path)

    def test_rejects_reusing_calibration_as_heldout_evaluation(self) -> None:
        self.write_rows([self.row(0), self.row(1), self.row(2)])
        replay = load_replay(self.path)
        with self.assertRaisesRegex(ValidationError, "must start strictly after calibration"):
            validate_split(replay, replay, warmup=1)


class DetectorAndMetricTests(unittest.TestCase):
    def test_oes32_formula_matches_frozen_weighted_definition(self) -> None:
        x = np.arange(1, 33, dtype=np.float64).reshape(1, 32) / 10
        expected = 0.45 * np.max(np.abs(x)) + 0.35 * np.sqrt(np.mean(x**2)) + 0.20 * np.mean(np.abs(x))
        self.assertAlmostEqual(detector_scores("oes32", x, 2)[0], expected, places=14)

    def test_maxabs_formula(self) -> None:
        x = np.array([[1.0, -3.0] + [0.0] * 30])
        self.assertEqual(detector_scores("maxabs", x, 2)[0], 3.0)

    def test_temporal_baselines_zero_during_warmup(self) -> None:
        x = np.zeros((8, 32))
        x[4:] = 0.2
        for detector in ("ewma", "cusum"):
            with self.subTest(detector=detector):
                scores = detector_scores(detector, x, warmup=3)
                np.testing.assert_array_equal(scores[:3], 0.0)
                self.assertTrue(np.all(scores[3:] >= 0.0))
                self.assertGreater(scores[4], 0.0)

    def test_threshold_uses_calibration_episode_maxima_and_meets_target(self) -> None:
        class F:
            def __init__(self, event_id: str | None):
                self.event_id = event_id
        frames = tuple(F(event_id) for event_id in [None, "a", "a", None, "b", "b", None, "c", None, "d"])
        scores = np.array([0, 1, 5, 0, 2, 3, 0, 4, 0, 6], dtype=float)
        result = calibrate_threshold(frames, scores, warmup=1, target_recall=0.75)
        self.assertEqual(result["threshold"], 4.0)
        self.assertEqual(result["calibration_achieved_event_recall"], 0.75)

    def test_metrics_include_false_alarm_episodes_and_delay(self) -> None:
        class F:
            def __init__(self, i: int, event: str | None):
                self.timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i)
                self.event_id = event
                self.regime = "steady"
        frames = tuple(F(i, None if i < 2 or i > 4 else "e1") for i in range(7))
        flags = np.array([False, False, True, True, True, True, False])
        metrics = detection_metrics(frames, flags, cadence_seconds=1.0, warmup=0)
        self.assertEqual(metrics["event_episode_count"], 1)
        self.assertEqual(metrics["detected_event_count"], 1)
        self.assertEqual(metrics["detection_delay_ms"]["median"], 0.0)
        self.assertEqual(metrics["false_alarm_episodes"], 1)
        self.assertEqual(metrics["frame_confusion"]["fp"], 1)

    def test_eval_labels_do_not_change_calibration_thresholds(self) -> None:
        # End-to-end: same calibration/protocol with two held-out files, threshold results stay fixed.
        demo_cal = load_replay(ROOT / "data" / "demo_calibration.jsonl")
        demo_eval = load_replay(ROOT / "data" / "demo_holdout.jsonl")
        protocol_bytes = (ROOT / "protocols" / "demo_v1.json").read_bytes()
        protocol = json.loads(protocol_bytes)
        first = run_benchmark(demo_cal, demo_eval, protocol, protocol_bytes, "synthetic")
        with tempfile.TemporaryDirectory() as temp:
            changed_path = Path(temp) / "relabeled_holdout.jsonl"
            rows = [json.loads(line) for line in (ROOT / "data" / "demo_holdout.jsonl").read_text().splitlines()]
            for row in rows:
                if row["event_id"] is not None:
                    row["event_label"] = "none"
                    row["event_id"] = None
            changed_path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))
            changed_eval = load_replay(changed_path)
            changed = run_benchmark(demo_cal, changed_eval, protocol, protocol_bytes, "synthetic")
        self.assertEqual(
            {k: v["threshold"] for k, v in first["calibration_thresholds"].items()},
            {k: v["threshold"] for k, v in changed["calibration_thresholds"].items()},
        )
        self.assertEqual(first["held_out_results"]["oes32"]["held_out"]["event_episode_count"], 7)
        self.assertEqual(changed["held_out_results"]["oes32"]["held_out"]["event_episode_count"], 0)
        self.assertEqual(first["detectors"]["oes32"]["version"]["source_commit"], "15692152f1851bde617efd3a61fc09485dbe8ed7")
        self.assertFalse(first["related_algorithm_not_used"]["used"])


class CliTests(unittest.TestCase):
    def test_demo_cli_writes_synthetic_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            completed = subprocess.run(
                [sys.executable, "-m", "oes_telemetry_bench", "demo", "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["evidence_class"], "synthetic")
            self.assertEqual(set(report["held_out_results"]), {"oes32", "maxabs", "ewma", "cusum"})
            self.assertIn("SYNTHETIC", report["interpretation"])


if __name__ == "__main__":
    unittest.main()
