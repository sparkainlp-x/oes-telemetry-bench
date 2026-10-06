# Changelog

All notable changes to this project. The bundled demo is SYNTHETIC; nothing here is field evidence.

## Unreleased

- Added the Zenodo concept DOI [10.5281/zenodo.23175492](https://doi.org/10.5281/zenodo.23175492) (v0.1.0 version DOI [10.5281/zenodo.23175493](https://doi.org/10.5281/zenodo.23175493)) to the README and `CITATION.cff`. The `v0.1.0` tag was not moved.

## 0.1.0 (2026-10-05)

First tagged release (`v0.1.0`). Archived on Zenodo as 10.5281/zenodo.23175493; the tag is not moved.

### Added

- Strict native 32-channel JSONL frame contract: exactly 32 named, finite channel values with per-channel timestamps equal to the frame timestamp; no interpolation, resampling, padding, duplication or imputation.
- Offline calibration/held-out replay: thresholds chosen only on a separate calibration replay to a target event recall, then frozen before held-out scoring.
- Detectors: the OES-Resilience `oes32` weighted score on one 32-channel block, plus `maxabs`, `ewma` and `cusum` baselines.
- Report with held-out event recall, frame metrics, false-alarm episodes per normal hour, delays, regime slices and resource estimates; file hashes and the declared evidence class are recorded.
- External reference vectors from upstream OES-Resilience v0.4.0 (`15692152`): bit-identical OES32 scores on 22 fixed vectors and all 680 bundled demo frames.
- SYNTHETIC demo replay and report.

### Known limitations

- On the synthetic held-out replay, `oes32` detected 6/7 event episodes with 2 false-alarm episodes; the simple `maxabs` baseline detected the same 6/7 with 1. No detector superiority is claimed.
- False-alarm rates per hour extrapolate from about 5 minutes of synthetic normal data and are highly uncertain.
- Timestamp agreement in a file is not proof of simultaneous acquisition.
- EWMA/CUSUM are reimplementations not covered by the reference vectors.
