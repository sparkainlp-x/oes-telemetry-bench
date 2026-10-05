# OES telemetry bench

[![CI](https://github.com/sparkainlp-x/oes-telemetry-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/sparkainlp-x/oes-telemetry-bench/actions/workflows/ci.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg)](LICENSE)
[![Evidence: SYNTHETIC](https://img.shields.io/badge/evidence-SYNTHETIC-blue.svg)](#evidence-boundary)
[![DOI: pending](https://img.shields.io/badge/DOI-pending-lightgrey.svg)](#cite)

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

## Detectors and evaluation

The candidate is the exact OES-Resilience `oes32` detector definition: `oes_resilience.detectors.OES32Detector`, package/release **0.4.0 / v0.4.0**, source commit `15692152f1851bde617efd3a61fc09485dbe8ed7`.

```text
score = 0.45 * max(abs(x))
      + 0.35 * sqrt(mean(x**2))
      + 0.20 * mean(abs(x))
```

Here `x` is the one simultaneous 32-channel frame and an alarm is `score >= threshold`. The benchmark also compares `maxabs`, `ewma`, and `cusum`. EWMA/CUSUM use the OES-Resilience definitions: standardize the 32-channel frame mean from the event-free warm-up; then use two-sided EWMA (`lambda=0.2`) or tabular CUSUM (`k=0.5`). All detector formulas, parameters, IDs, and versions are in the report.

Each threshold is chosen **only from the separate calibration replay** to meet the protocol's target event recall (90% in the example), then frozen before scoring held-out outcomes. Threshold selection uses calibration event-episode maxima; no held-out labels are passed to it. The report includes held-out event recall/misses, frame precision/recall/F1/specificity/accuracy, false-alarm episodes per normal operating hour, median and 90th-percentile delay for detected events, regime slices, CPU time per frame, throughput, and a traced-memory estimate. Timing depends on the computer and test volume.

The protocol's `locked_at` value is self-declared. Its SHA-256 identifies the exact protocol bytes in the report but does not independently prove when the protocol was frozen.

The report identifies `residual_reference.evaluate_residual` as a **different, unused** OES-32 algorithm: it computes `R = max_i(abs(observed_i - reference_i))` on two 32-value vectors and fails iff `R > tolerance`. It is not the OES-Resilience telemetry detector and is never substituted for it.

## Evidence boundary

The example replay is deterministic generated noise, injected events, and synthetic unlabeled transients. Every result from `demo` is labeled **SYNTHETIC**. It demonstrates that the parser, calibration, replay, and metrics run; it does not establish field performance, detector superiority, a sensor mapping, or operational suitability. A user-supplied replay still requires independently verified provenance, synchronized sensor semantics, trusted event labels, engineering review, and a suitable held-out design before drawing real-world conclusions.


## Cite

```bibtex
@software{oes_telemetry_bench_0_1_0,
  author = {Brisson, Jean-François},
  title = {oes-telemetry-bench: offline replay bench for native synchronized OES32 telemetry frames},
  version = {0.1.0},
  year = {2026},
  url = {https://github.com/sparkainlp-x/oes-telemetry-bench},
  license = {AGPL-3.0-only},
  note = {SYNTHETIC demo only; not field evidence}
}
```

See also [CITATION.cff](CITATION.cff). DOI pending (do not mint a GitHub release until the Zenodo webhook is enabled).

