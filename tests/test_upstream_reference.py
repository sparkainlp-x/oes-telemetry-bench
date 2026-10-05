"""External conformance check against the upstream OES-Resilience v0.4.0 OES32Detector.

The expected scores in ``fixtures/oes_resilience_v0_4_0_oes32_reference.json`` were
computed by the upstream ``oes_resilience.detectors.OES32Detector`` at tag v0.4.0
(commit 15692152f1851bde617efd3a61fc09485dbe8ed7) with
``Config(channels=32, block_size=32)``, via ``tools/make_upstream_oes32_reference.py``.
They are not produced by this kit, so this test is independent of the local formula
test. All inputs are SYNTHETIC.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oes_telemetry_bench.bench import OES_VERSION, detector_scores, load_replay  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "oes_resilience_v0_4_0_oes32_reference.json"
RTOL = 1e-12
ATOL = 1e-15


class UpstreamOES32ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_fixture_pins_the_cited_upstream_release(self) -> None:
        upstream = self.fixture["upstream"]
        self.assertEqual(upstream["commit"], OES_VERSION["source_commit"])
        self.assertEqual(upstream["tag"], OES_VERSION["release"])
        self.assertEqual(upstream["package_version"], OES_VERSION["package_version"])
        self.assertEqual(upstream["weights"], {"maximum": 0.45, "rms": 0.35, "mean_absolute": 0.2})

    def test_fixed_vectors_match_upstream_scores(self) -> None:
        vectors = self.fixture["fixed_vectors"]
        self.assertGreaterEqual(len(vectors), 20)
        frames = np.asarray([v["channels"] for v in vectors], dtype=np.float64)
        expected = np.asarray([v["upstream_score"] for v in vectors], dtype=np.float64)
        local = detector_scores("oes32", frames, warmup=1)
        np.testing.assert_allclose(local, expected, rtol=RTOL, atol=ATOL)

    def test_bundled_demo_replays_match_upstream_scores(self) -> None:
        for entry in self.fixture["data_files"]:
            with self.subTest(path=entry["path"]):
                path = ROOT / entry["path"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"],
                                 "demo data changed; regenerate the upstream reference fixture")
                frames = np.asarray([f.channels for f in load_replay(path).frames], dtype=np.float64)
                self.assertEqual(frames.shape[0], entry["frame_count"])
                local = detector_scores("oes32", frames, warmup=1)
                np.testing.assert_allclose(local, np.asarray(entry["upstream_scores"]), rtol=RTOL, atol=ATOL)


if __name__ == "__main__":
    unittest.main()
