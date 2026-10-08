# OES telemetry bench

[![CI](https://github.com/sparkainlp-x/oes-telemetry-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/sparkainlp-x/oes-telemetry-bench/actions/workflows/ci.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg)](LICENSE)
[![Evidence: SYNTHETIC](https://img.shields.io/badge/evidence-SYNTHETIC-blue.svg)](#evidence-boundary)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23175492.svg)](https://doi.org/10.5281/zenodo.23175492)

A small, offline replay kit for **native OES32 evaluation**: each observation must be one timestamped frame containing exactly 32 named, finite channel values measured at that same timestamp. The bundled demo is explicitly **synthetic**; it is a software exercise, not field evidence. The tool makes no live connection and does not operate alarms or equipment.

Companion to [OES-Resilience](https://github.com/sparkainlp-x/oes-resilience) (concept DOI [10.5281/zenodo.23071166](https://doi.org/10.5281/zenodo.23071166)). Commercial dual-licensing: see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).

## Run

Python 3.11+ and NumPy are required. From this directory:

```bash
python3 -m pip install -e .
python3 -m oes_telemetry_bench demo
python3 -m unittest discover -s tests -v
```

The demo writes `reports/synthetic-demo-report.json`. To regenerate its inputs, run `python3 tools/generate_demo.py`. For your own offline files:

```bash
python3 -m oes_telemetry_bench evaluate \
  --calibration path/to/calibration.jsonl \
  --evaluation path/to/heldout.jsonl \
  --protocol protocols/demo_v1.json \
  --evidence-class user_supplied \
  --output report.json
```

A user-supplied dataset is not automatically verified as real, authorized, representative, or field-labeled. The report records file hashes and the declared evidence class. It never turns replay metrics into a field-performance or superiority claim.

## Native frame contract

Each UTF-8 JSONL record has exactly these fields:

- `timestamp`: ISO-8601 timestamp with explicit UTC offset or `Z`.
- `channel_ids`: exactly 32 unique, non-empty names; order is constant in both splits.
- `channel_timestamps`: exactly 32 timestamps. Every timestamp must equal `timestamp` exactly after UTC normalization.
- `channels`: exactly 32 finite JSON numbers, in `channel_ids` order. Booleans, null, NaN, infinity, and non-numeric values are rejected.
- `regime`: non-empty operating-regime label.
- `event_label`: `none` for normal frames, or a non-empty event label.
- `event_id`: null for `event_label: none`; otherwise a non-empty ID shared only by one contiguous event episode.

Frames must be strictly ordered with one exact regular sampling interval. Calibration and held-out evaluation must use the same cadence and channel order; held-out timestamps must follow calibration. Each split begins with the protocol's event-free temporal warm-up. The checker rejects univariate inputs, 31/33-channel frames, mismatched channel timestamps, duplicate channel IDs/JSON keys, missing values, inconsistent event episodes, and implicit alignment. **No interpolation, resampling, padding, channel duplication, or imputation is performed.** If native simultaneity cannot be supported by per-channel timestamps and meaningful channel definitions, do not call the data native-32 validation.

**Timestamp agreement is a file assertion, not proof of simultaneous acquisition.** The checker confirms only that the 32 per-channel timestamps written in the file agree with the frame timestamp; it cannot confirm that the channels were actually sampled simultaneously (timestamps may have been assigned by a logger, re-stamped, or aligned upstream). Treat simultaneity as **user-declared until checked**. When supplying your own data, record the device-clock / DAQ basis alongside the replay — e.g. acquisition hardware and model, whether channels share one sample clock or are separately clocked and later aligned, clock source and synchronization method (PPS, PTP, NTP, shared trigger), and who stamped the timestamps (device vs. host/logger) — so the simultaneity claim can be independently audited.

## Detectors and evaluation

The candidate is a standalone implementation of the formula of the OES-Resilience `oes32` detector, `oes_resilience.detectors.OES32Detector`, package/release **0.4.0 / v0.4.0**, source commit `15692152f1851bde617efd3a61fc09485dbe8ed7`, applied to one native 32-channel frame (a single 32-channel block).

```text
score = 0.45 * max(abs(x))
      + 0.35 * sqrt(mean(x**2))
      + 0.20 * mean(abs(x))
```

Here `x` is the one simultaneous 32-channel frame and an alarm is `score >= threshold`.

**How conformance with upstream is checked.** The local formula test (`test_oes32_formula_matches_frozen_weighted_definition`) compares the implementation with the same formula re-typed in the test, so it effectively compares the implementation with itself; on its own it does not establish conformance with the cited upstream release. Conformance is therefore checked with **external reference vectors**: `tests/test_upstream_reference.py` compares this kit's OES32 scores against scores computed by the upstream `OES32Detector` at v0.4.0 / `15692152` (run with `Config(channels=32, block_size=32)`, i.e. one block per native frame) on 22 fixed synthetic vectors and on all 680 frames of the bundled synthetic demo replays (`tests/fixtures/oes_resilience_v0_4_0_oes32_reference.json`, regenerated by `tools/make_upstream_oes32_reference.py --upstream <checkout>`). In our run the scores were bit-identical; the test tolerance is `rtol=1e-12`. This covers only the OES32 score for a single 32-channel block — not upstream's multi-block status classification, missing-value path, or other detectors.

The benchmark also compares `maxabs`, `ewma`, and `cusum`. EWMA/CUSUM are reimplementations following the documented OES-Resilience definitions (they are **not** covered by the external reference vectors): standardize the 32-channel frame mean from the event-free warm-up; then use two-sided EWMA (`lambda=0.2`) or tabular CUSUM (`k=0.5`). All detector formulas, parameters, IDs, and versions are in the report.

Each threshold is chosen **only from the separate calibration replay** to meet the protocol's target event recall (90% in the example), then frozen before scoring held-out outcomes. **The 90% figure is the calibration target, not observed held-out recall.** Threshold selection uses calibration event-episode maxima; no held-out labels are passed to it. The report includes held-out event recall/misses, frame precision/recall/F1/specificity/accuracy, false-alarm episodes per normal operating hour, median and 90th-percentile delay for detected events, regime slices, CPU time per frame, throughput, and a traced-memory estimate. Timing depends on the computer and test volume.

### Bundled synthetic demo: held-out outcome

From `reports/synthetic-demo-report.json` (evidence class **SYNTHETIC**; 7 synthetic held-out event episodes; 0.0867 h ≈ 5.2 min of event-free held-out time):

| Detector | Held-out event episodes detected | False-alarm episodes | False-alarm episodes per normal hour |
|---|---:|---:|---:|
| `oes32` | 6/7 (85.7%) | 2 | 23.08 |
| `maxabs` | 6/7 (85.7%) | 1 | 11.54 |
| `ewma` | 3/7 (42.9%) | 1 | 11.54 |
| `cusum` | 5/7 (71.4%) | 6 | 69.23 |

On this synthetic replay OES32 detected 6/7 event episodes with about 23.08 false-alarm episodes per normal hour, and the simple `maxabs` baseline detected the same 6/7 with fewer false alarms. The per-hour false-alarm rates extrapolate from a very short synthetic normal period (a handful of alarm episodes over ~5 minutes), so they are highly uncertain and not an operational rate. The demo's fault generator adds a common offset across all 32 channels (plus small per-channel noise). None of this is evidence of field performance or of any detector's superiority.

The protocol's `locked_at` value is self-declared. Its SHA-256 identifies the exact protocol bytes in the report but does not independently prove when the protocol was frozen.

The report identifies `residual_reference.evaluate_residual` as a **different, unused** OES-32 algorithm: it computes `R = max_i(abs(observed_i - reference_i))` on two 32-value vectors and fails iff `R > tolerance`. It is not the OES-Resilience telemetry detector and is never substituted for it.

### Related but distinct OES algorithms

- **This kit (`oes32` here):** the weighted max/RMS/mean-absolute score applied to **one native 32-channel frame** (a single block) with a frame-level threshold calibrated to a target event recall.
- **[OES-32 residual](https://github.com/sparkainlp-x/oes32-residual) (`residual_reference.evaluate_residual`):** a residual-reference pass/fail function, `R = max_i |observed_i − reference_i|` against a tolerance. Not an anomaly score; not used here.
- **[OES-Resilience](https://github.com/sparkainlp-x/oes-resilience) weighted-block score:** the upstream benchmark applies the weighted score per 32-channel block within its default 512-channel (16-block) configuration and classifies status from detected-block counts (e.g. `GLOBAL_SHOCK`). That multi-block score/status pipeline is a different construction from this kit's single-frame detector; the external reference test above pins equivalence only for the one-block, finite-input case.

## Evidence boundary

The example replay is deterministic generated noise, injected events, and synthetic unlabeled transients. Every result from `demo` is labeled **SYNTHETIC**. It demonstrates that the parser, calibration, replay, and metrics run; it does not establish field performance, detector superiority, a sensor mapping, or operational suitability. A user-supplied replay still requires independently verified provenance, synchronized sensor semantics, trusted event labels, engineering review, and a suitable held-out design before drawing real-world conclusions.


## Cite

```bibtex
@software{oes_telemetry_bench_0_1_0,
  author = {Brisson, Jean-François},
  title = {oes-telemetry-bench: offline replay bench for native synchronized OES32 telemetry frames},
  version = {0.1.0},
  year = {2026},
  doi = {10.5281/zenodo.23175492},
  url = {https://github.com/sparkainlp-x/oes-telemetry-bench},
  license = {AGPL-3.0-only},
  note = {SYNTHETIC demo only; not field evidence}
}
```

See also [CITATION.cff](CITATION.cff). The current version is 0.1.1 (tag [`v0.1.1`](https://github.com/sparkainlp-x/oes-telemetry-bench/releases/tag/v0.1.1), 2026-10-08). Concept DOI (all versions): [10.5281/zenodo.23175492](https://doi.org/10.5281/zenodo.23175492); version DOIs: v0.1.1 [10.5281/zenodo.23241694](https://doi.org/10.5281/zenodo.23241694), v0.1.0 [10.5281/zenodo.23175493](https://doi.org/10.5281/zenodo.23175493).

