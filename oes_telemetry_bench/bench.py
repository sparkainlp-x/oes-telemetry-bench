"""Transparent, offline OES native-32 telemetry replay and evaluation."""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import sys
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

PROJECT_VERSION = "0.1.0"
CHANNEL_COUNT = 32
OES_VERSION = {
    "repository": "https://github.com/sparkainlp-x/oes-resilience",
    "release": "v0.4.0",
    "package_version": "0.4.0",
    "source_commit": "15692152f1851bde617efd3a61fc09485dbe8ed7",
}
OES_DETECTOR_ID = "oes_resilience.detectors.OES32Detector (registry: oes32)"
OES_FORMULA = (
    "score = 0.45 * max(abs(x)) + 0.35 * sqrt(mean(x**2)) + "
    "0.20 * mean(abs(x)) over the 32 simultaneous channels; alarm if score >= threshold"
)
RESIDUAL_REFERENCE = {
    "used": False,
    "function_id": "residual_reference.evaluate_residual",
    "repository": "https://github.com/sparkainlp-x/oes32-residual",
    "release": "v0.1.1",
    "contract_commit": "b77b61254f15778c6ae221843dceac7a8571158e",
    "formula": "R = max_i(abs(observed_i - reference_i)) for two 32-value vectors",
    "decision": "fails iff R > tolerance (equality passes); not an anomaly detector and not used here",
}

FRAME_KEYS = frozenset({
    "timestamp", "channel_ids", "channel_timestamps", "channels",
    "regime", "event_label", "event_id",
})
_TIMESTAMP_FRACTION = re.compile(r"[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2}\.(\d+)")


class ValidationError(ValueError):
    """Input or protocol does not satisfy the fail-closed data contract."""


@dataclass(frozen=True)
class Frame:
    timestamp: datetime
    channel_ids: tuple[str, ...]
    channels: tuple[float, ...]
    regime: str
    event_label: str
    event_id: str | None


@dataclass(frozen=True)
class Replay:
    frames: tuple[Frame, ...]
    channel_ids: tuple[str, ...]
    cadence_seconds: float
    sha256: str
    path: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValidationError(f"non-standard JSON constant is not allowed: {value}")


def _parse_json(text: str, where: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except ValidationError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValidationError(f"{where}: invalid JSON: {exc}") from exc


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be an ISO-8601 timestamp with UTC offset")
    fractional = _TIMESTAMP_FRACTION.search(value)
    if fractional and len(fractional.group(1)) > 6:
        raise ValidationError(f"{field} may not exceed microsecond precision")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field} must include an explicit UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValidationError(f"{field} must be a non-empty string without outer whitespace")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a JSON number, not a boolean or null")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValidationError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{field} must be finite")
    return result


def _parse_frame(obj: Any, where: str) -> Frame:
    if not isinstance(obj, dict):
        raise ValidationError(f"{where}: every JSONL line must be an object")
    missing, unknown = FRAME_KEYS - set(obj), set(obj) - FRAME_KEYS
    if missing:
        raise ValidationError(f"{where}: missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValidationError(f"{where}: unknown fields: {', '.join(sorted(unknown))}")

    stamp = _timestamp(obj["timestamp"], f"{where}.timestamp")
    raw_ids = obj["channel_ids"]
    if not isinstance(raw_ids, list) or len(raw_ids) != CHANNEL_COUNT:
        raise ValidationError(f"{where}.channel_ids must contain exactly {CHANNEL_COUNT} channel names")
    channel_ids = tuple(_text(value, f"{where}.channel_ids[{index}]") for index, value in enumerate(raw_ids))
    if len(set(channel_ids)) != CHANNEL_COUNT:
        raise ValidationError(f"{where}.channel_ids must be unique; duplicate channel names are not allowed")

    raw_stamps = obj["channel_timestamps"]
    if not isinstance(raw_stamps, list) or len(raw_stamps) != CHANNEL_COUNT:
        raise ValidationError(f"{where}.channel_timestamps must contain exactly {CHANNEL_COUNT} timestamps")
    channel_stamps = tuple(
        _timestamp(value, f"{where}.channel_timestamps[{index}]")
        for index, value in enumerate(raw_stamps)
    )
    if any(value != stamp for value in channel_stamps):
        raise ValidationError(
            f"{where}: all 32 channel timestamps must exactly equal the frame timestamp; "
            "no implicit alignment, tolerance, interpolation, or resampling is performed"
        )

    raw_values = obj["channels"]
    if not isinstance(raw_values, list) or len(raw_values) != CHANNEL_COUNT:
        raise ValidationError(f"{where}.channels must contain exactly {CHANNEL_COUNT} finite numbers")
    channels = tuple(_number(value, f"{where}.channels[{index}]") for index, value in enumerate(raw_values))
    regime = _text(obj["regime"], f"{where}.regime")
    label = _text(obj["event_label"], f"{where}.event_label")
    raw_event_id = obj["event_id"]
    if label == "none":
        if raw_event_id is not None:
            raise ValidationError(f"{where}.event_id must be null when event_label is 'none'")
        event_id = None
    else:
        event_id = _text(raw_event_id, f"{where}.event_id")
    return Frame(stamp, channel_ids, channels, regime, label, event_id)


def load_replay(path: str | Path) -> Replay:
    """Load a fail-closed native-32 JSONL replay and preserve its exact-byte hash."""
    source = Path(path)
    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(f"cannot read UTF-8 replay {source}: {exc}") from exc
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    frames: list[Frame] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ValidationError(f"{source}:{line_number}: blank lines are not allowed")
        frames.append(_parse_frame(_parse_json(line, f"{source}:{line_number}"), f"{source}:{line_number}"))
    if len(frames) < 2:
        raise ValidationError(f"{source}: at least two frames are required to establish a sampling cadence")

    channel_ids = frames[0].channel_ids
    for index, frame in enumerate(frames):
        if frame.channel_ids != channel_ids:
            raise ValidationError(f"{source}: channel identity/order changes at frame {index + 1}")
        if index and frame.timestamp <= frames[index - 1].timestamp:
            raise ValidationError(f"{source}: frame timestamps must be strictly increasing")
    intervals = [(frames[i].timestamp - frames[i - 1].timestamp).total_seconds() for i in range(1, len(frames))]
    if any(step != intervals[0] for step in intervals[1:]):
        raise ValidationError(f"{source}: timestamps must have one exact, regular sampling interval")
    if intervals[0] <= 0:
        raise ValidationError(f"{source}: sampling interval must be positive")

    active: str | None = None
    closed: set[str] = set()
    identities: dict[str, tuple[str, str]] = {}
    for index, frame in enumerate(frames, start=1):
        if frame.event_id is None:
            if active is not None:
                closed.add(active)
                active = None
            continue
        identity = (frame.regime, frame.event_label)
        if frame.event_id in identities and identities[frame.event_id] != identity:
            raise ValidationError(f"{source}: event_id {frame.event_id!r} changes regime or label at frame {index}")
        identities[frame.event_id] = identity
        if frame.event_id != active:
            if frame.event_id in closed:
                raise ValidationError(f"{source}: event_id {frame.event_id!r} is not one contiguous episode")
            if active is not None:
                closed.add(active)
            active = frame.event_id
    return Replay(tuple(frames), channel_ids, intervals[0], hashlib.sha256(raw).hexdigest(), str(source))


def _load_protocol(path: str | Path) -> tuple[dict[str, Any], bytes]:
    source = Path(path)
    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(f"cannot read UTF-8 protocol {source}: {exc}") from exc
    protocol = _parse_json(text, str(source))
    required = {
        "schema_version", "protocol_id", "locked_at", "target_event_recall", "warmup_frames",
        "ewma_lambda", "cusum_reference_k", "alarm_comparison", "event_hit_rule",
    }
    if not isinstance(protocol, dict) or set(protocol) != required:
        raise ValidationError(f"{source}: protocol keys must be exactly {sorted(required)}")
    if type(protocol["schema_version"]) is not int or protocol["schema_version"] != 1:
        raise ValidationError("protocol.schema_version must be integer 1")
    _text(protocol["protocol_id"], "protocol.protocol_id")
    _timestamp(protocol["locked_at"], "protocol.locked_at")
    target = _number(protocol["target_event_recall"], "protocol.target_event_recall")
    if not 0 < target <= 1:
        raise ValidationError("protocol.target_event_recall must be in (0, 1]")
    warmup = protocol["warmup_frames"]
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 2:
        raise ValidationError("protocol.warmup_frames must be an integer >= 2")
    lam = _number(protocol["ewma_lambda"], "protocol.ewma_lambda")
    if not 0 < lam <= 1:
        raise ValidationError("protocol.ewma_lambda must be in (0, 1]")
    k = _number(protocol["cusum_reference_k"], "protocol.cusum_reference_k")
    if k < 0:
        raise ValidationError("protocol.cusum_reference_k must be non-negative")
    if protocol["alarm_comparison"] != ">=":
        raise ValidationError("protocol.alarm_comparison must be '>='")
    if protocol["event_hit_rule"] != "any_alarm_frame_in_contiguous_event_episode":
        raise ValidationError("unsupported event_hit_rule")
    return protocol, raw


def validate_split(calibration: Replay, evaluation: Replay, warmup: int) -> None:
    if calibration.channel_ids != evaluation.channel_ids:
        raise ValidationError("calibration and evaluation channel IDs/order must be identical")
    if calibration.cadence_seconds != evaluation.cadence_seconds:
        raise ValidationError("calibration and evaluation must use the same exact sampling cadence")
    if calibration.frames[-1].timestamp >= evaluation.frames[0].timestamp:
        raise ValidationError("held-out evaluation timestamps must start strictly after calibration ends")
    cal_ids = {frame.event_id for frame in calibration.frames if frame.event_id is not None}
    eval_ids = {frame.event_id for frame in evaluation.frames if frame.event_id is not None}
    if cal_ids & eval_ids:
        raise ValidationError("event IDs must not be shared across calibration and held-out evaluation")
    for label, replay in (("calibration", calibration), ("evaluation", evaluation)):
        if len(replay.frames) <= warmup:
            raise ValidationError(f"{label} needs more than warmup_frames={warmup} frames")
        if any(frame.event_id is not None for frame in replay.frames[:warmup]):
            raise ValidationError(f"the first {warmup} {label} frames must be event-free for temporal warm-up")


def _event_groups(frames: tuple[Frame, ...] | list[Frame], start: int = 0) -> dict[str, list[int]]:
    events: dict[str, list[int]] = {}
    for i in range(start, len(frames)):
        event_id = frames[i].event_id
        if event_id is not None:
            events.setdefault(event_id, []).append(i)
    return events


def detector_scores(
    detector: str,
    values: np.ndarray,
    warmup: int,
    ewma_lambda: float = 0.2,
    cusum_k: float = 0.5,
) -> np.ndarray:
    """Return one score per frame; formulas/parameters match the documented definitions."""
    x = np.asarray(values, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] != CHANNEL_COUNT:
        raise ValidationError(f"native OES32 scoring requires shape (frames, {CHANNEL_COUNT}); received {x.shape}")
    if not np.all(np.isfinite(x)):
        raise ValidationError("detector input must contain only finite values")
    if detector == "oes32":
        absolute = np.abs(x)
        scores = 0.45 * absolute.max(axis=1) + 0.35 * np.sqrt(np.mean(x * x, axis=1)) + 0.20 * absolute.mean(axis=1)
    elif detector == "maxabs":
        scores = np.abs(x).max(axis=1)
    elif detector in {"ewma", "cusum"}:
        if len(x) <= warmup:
            raise ValidationError(f"{detector} requires more than {warmup} frames")
        means = x.mean(axis=1)
        baseline = means[:warmup]
        mu = float(baseline.mean())
        sigma = max(float(np.sqrt(np.sum((baseline - mu) ** 2) / (warmup - 1))), 1e-9)
        z = (means - mu) / sigma
        scores = np.zeros(len(x), dtype=np.float64)
        if detector == "ewma":
            state = 0.0
            norm = math.sqrt(ewma_lambda / (2.0 - ewma_lambda))
            for i in range(warmup, len(x)):
                state = ewma_lambda * z[i] + (1.0 - ewma_lambda) * state
                scores[i] = abs(state) / norm
        else:
            upper = lower = 0.0
            for i in range(warmup, len(x)):
                upper = max(0.0, upper + z[i] - cusum_k)
                lower = max(0.0, lower - z[i] - cusum_k)
                scores[i] = max(upper, lower)
    else:
        raise ValidationError(f"unknown detector: {detector}")
    scores = np.asarray(scores, dtype=np.float64)
    if not np.all(np.isfinite(scores)) or np.any(scores < 0):
        raise ValidationError(f"{detector} produced a non-finite or negative score; check numeric range")
    return scores


def calibrate_threshold(
    frames: tuple[Frame, ...], scores: np.ndarray, warmup: int, target_recall: float,
) -> dict[str, Any]:
    """Choose the highest event-max score cutoff meeting recall, using calibration labels only."""
    maxima = [float(np.max(scores[indices])) for indices in _event_groups(frames, warmup).values()]
    if not maxima:
        raise ValidationError("calibration data must contain at least one labeled event episode")
    required = max(1, math.ceil(target_recall * len(maxima) - 1e-12))
    threshold = sorted(maxima, reverse=True)[required - 1]
    achieved = sum(value >= threshold for value in maxima) / len(maxima)
    return {
        "threshold": float(threshold),
        "target_event_recall": float(target_recall),
        "calibration_event_count": len(maxima),
        "calibration_detected_event_count": int(round(achieved * len(maxima))),
        "calibration_achieved_event_recall": float(achieved),
        "selection_rule": "highest score cutoff whose calibration event-episode maxima attain the target; alarm if score >= cutoff",
    }


def _ratio(num: int | float, den: int | float) -> float | None:
    return float(num / den) if den else None


def detection_metrics(
    frames: tuple[Frame, ...], flags: np.ndarray, cadence_seconds: float, warmup: int,
    regime: str | None = None,
) -> dict[str, Any]:
    indices = [i for i in range(warmup, len(frames)) if regime is None or frames[i].regime == regime]
    truth = [frames[i].event_id is not None for i in indices]
    pred = [bool(flags[i]) for i in indices]
    tp = sum(p and t for p, t in zip(pred, truth))
    fp = sum(p and not t for p, t in zip(pred, truth))
    fn = sum((not p) and t for p, t in zip(pred, truth))
    tn = sum((not p) and (not t) for p, t in zip(pred, truth))
    frame_precision = _ratio(tp, tp + fp)
    frame_recall = _ratio(tp, tp + fn)
    f1 = _ratio(2 * tp, 2 * tp + fp + fn)
    specificity = _ratio(tn, tn + fp)

    selected = set(indices)
    event_frames = _event_groups(frames, warmup)
    if regime is not None:
        event_frames = {
            event_id: [i for i in members if i in selected]
            for event_id, members in event_frames.items()
        }
        event_frames = {event_id: members for event_id, members in event_frames.items() if members}
    delays: dict[str, float | None] = {}
    detected_events = 0
    for event_id, members in event_frames.items():
        alarms = [i for i in members if flags[i]]
        if alarms:
            detected_events += 1
            delays[event_id] = (frames[alarms[0]].timestamp - frames[members[0]].timestamp).total_seconds() * 1000.0
        else:
            delays[event_id] = None

    false_episodes = 0
    previous_index: int | None = None
    previous_false_alarm = False
    for i in indices:
        is_false_alarm = frames[i].event_id is None and bool(flags[i])
        if is_false_alarm and (not previous_false_alarm or previous_index != i - 1):
            false_episodes += 1
        previous_false_alarm = is_false_alarm
        previous_index = i

    normal_count = sum(not event for event in truth)
    normal_hours = normal_count * cadence_seconds / 3600.0
    valid_delays = [value for value in delays.values() if value is not None]
    return {
        "frame_count_evaluated": len(indices),
        "event_episode_count": len(event_frames),
        "detected_event_count": detected_events,
        "missed_event_count": len(event_frames) - detected_events,
        "missed_event_ids": [event_id for event_id, delay in delays.items() if delay is None],
        "event_recall": _ratio(detected_events, len(event_frames)),
        "event_hit_rule": "at least one alarm-positive frame within the contiguous labeled event episode",
        "detection_delay_ms": {
            "median": float(np.median(valid_delays)) if valid_delays else None,
            "p90": float(np.percentile(valid_delays, 90)) if valid_delays else None,
            "by_event_id": delays,
            "definition": "first alarm-positive frame timestamp minus event onset; warm-up excluded",
        },
        "false_alarm_episodes": false_episodes,
        "normal_operating_hours": normal_hours,
        "false_alarms_per_normal_hour": _ratio(false_episodes, normal_hours),
        "false_alarm_frame_rate": _ratio(fp, normal_count),
        "frame_confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "frame_precision": frame_precision,
        "frame_recall": frame_recall,
        "frame_f1": f1,
        "frame_specificity": specificity,
        "frame_accuracy": _ratio(tp + tn, tp + fp + fn + tn),
        "localization": "not assessed: each timestamp is one native 32-channel block; no per-channel truth labels are supplied",
    }


def _measure_score(
    score_fn: Callable[[], np.ndarray], frame_count: int, repetitions: int = 5,
) -> tuple[np.ndarray, dict[str, Any]]:
    score_fn()  # one untimed warm-up call
    tracemalloc.start()
    scores = score_fn()
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    cpu_start = time.process_time_ns()
    wall_start = time.perf_counter_ns()
    for _ in range(repetitions):
        score_fn()
    cpu_ns = time.process_time_ns() - cpu_start
    wall_ns = time.perf_counter_ns() - wall_start
    denom = max(1, frame_count * repetitions)
    return scores, {
        "timing_repetitions": repetitions,
        "scored_frames_per_repetition": frame_count,
        "mean_cpu_ms_per_frame": cpu_ns / denom / 1e6,
        "mean_wall_ms_per_frame": wall_ns / denom / 1e6,
        "wall_frames_per_second": (denom * 1e9 / wall_ns) if wall_ns else None,
        "tracemalloc_peak_bytes_for_one_score_call": int(peak_bytes),
        "memory_note": "Python tracemalloc peak for one score call; not whole-process RSS or a hardware-independent cost",
    }


def _environment() -> dict[str, Any]:
    try:
        import numpy
        numpy_version = numpy.__version__
    except Exception:  # pragma: no cover - NumPy is required, defensive metadata only
        numpy_version = "unknown"
    return {
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "numpy": numpy_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "not reported by platform",
        "logical_cpu_count": os.cpu_count(),
    }


def run_benchmark(
    calibration: Replay, evaluation: Replay, protocol: dict[str, Any], protocol_bytes: bytes,
    evidence_class: str,
) -> dict[str, Any]:
    if evidence_class not in {"synthetic", "user_supplied"}:
        raise ValidationError("evidence_class must be 'synthetic' or 'user_supplied'")
    warmup = protocol["warmup_frames"]
    validate_split(calibration, evaluation, warmup)
    cal_values = np.asarray([frame.channels for frame in calibration.frames], dtype=np.float64)
    eval_values = np.asarray([frame.channels for frame in evaluation.frames], dtype=np.float64)
    names = ("oes32", "maxabs", "ewma", "cusum")
    calibration_rows: dict[str, Any] = {}
    evaluation_rows: dict[str, Any] = {}
    for name in names:
        cal_scores = detector_scores(
            name, cal_values, warmup, protocol["ewma_lambda"], protocol["cusum_reference_k"]
        )
        threshold = calibrate_threshold(
            calibration.frames, cal_scores, warmup, protocol["target_event_recall"]
        )
        score_fn = lambda n=name: detector_scores(
            n, eval_values, warmup, protocol["ewma_lambda"], protocol["cusum_reference_k"]
        )
        eval_scores, cost = _measure_score(score_fn, len(evaluation.frames))
        flags = eval_scores >= threshold["threshold"]
        flags[:warmup] = False
        evaluation_rows[name] = {
            "threshold": threshold["threshold"],
            "threshold_source": "calibration replay only; frozen before held-out metrics",
            "calibration": threshold,
            "held_out": detection_metrics(evaluation.frames, flags, evaluation.cadence_seconds, warmup),
            "held_out_by_regime": {
                regime: detection_metrics(evaluation.frames, flags, evaluation.cadence_seconds, warmup, regime)
                for regime in dict.fromkeys(frame.regime for frame in evaluation.frames[warmup:])
            },
            "compute_cost": cost,
        }
        calibration_rows[name] = {"threshold": threshold["threshold"], **threshold}

    detector_ids = {
        "oes32": {
            "detector_id": OES_DETECTOR_ID,
            "formula": OES_FORMULA,
            "version": OES_VERSION,
            "implementation_note": "Standalone score implementation in this kit; formula and threshold semantics match the cited OES-Resilience source commit.",
        },
        "maxabs": {
            "detector_id": "oes_telemetry_bench.baselines.maxabs",
            "formula": "score = max(abs(x)) over the 32 simultaneous channels; alarm if score >= threshold",
            "version": PROJECT_VERSION,
        },
        "ewma": {
            "detector_id": "oes_resilience.detectors.EWMADetector (registry: ewma)",
            "formula": "warm-up standardize block mean z; e_t = lambda*z_t + (1-lambda)*e_(t-1); score = abs(e_t)/sqrt(lambda/(2-lambda)); alarm if score >= threshold",
            "version": OES_VERSION,
            "parameters": {"lambda": protocol["ewma_lambda"], "warmup_frames": warmup},
        },
        "cusum": {
            "detector_id": "oes_resilience.detectors.CUSUMDetector (registry: cusum)",
            "formula": "z from warm-up standardized block mean; S+ = max(0,S+z-k), S- = max(0,S-z-k); score = max(S+,S-); alarm if score >= threshold",
            "version": OES_VERSION,
            "parameters": {"k": protocol["cusum_reference_k"], "warmup_frames": warmup},
        },
    }
    return {
        "report_schema_version": 1,
        "project": {"name": "oes-telemetry-bench", "version": PROJECT_VERSION},
        "scope": "offline replay evaluation only; no live telemetry, operational alarm, safety instrument, or process control",
        "evidence_class": evidence_class,
        "interpretation": (
            "SYNTHETIC demonstration outcomes; not field performance and not evidence of detector superiority."
            if evidence_class == "synthetic" else
            "Offline results on user-supplied files; dataset provenance and field representativeness are not independently verified. No field-performance or superiority claim is made."
        ),
        "native_geometry": {
            "mode": "native synchronized 32-channel frames",
            "channel_count": CHANNEL_COUNT,
            "channels_per_timestamp": CHANNEL_COUNT,
            "blocks_per_frame": 1,
            "synchronization_rule": "each of the 32 channel_timestamps must exactly equal the frame timestamp after UTC normalization",
            "sampling_rule": "strictly increasing, exactly regular sample cadence; same cadence in calibration and held-out evaluation",
            "univariate_data_is_native_validation": False,
            "preprocessing": "none; no resampling, interpolation, channel duplication, padding, or imputation",
            "channel_ids_sha256": hashlib.sha256("\n".join(evaluation.channel_ids).encode("utf-8")).hexdigest(),
        },
        "protocol": {
            **protocol,
            "sha256": hashlib.sha256(protocol_bytes).hexdigest(),
            "split_rule": "calibration timestamps precede held-out timestamps; same channel order/cadence; thresholds use calibration labels only",
            "warmup_rule": f"first {warmup} event-free frames of each split initialize temporal baselines and are excluded for every detector",
            "threshold_rule": "calibrate each detector independently to the target event recall on calibration event-episode maxima; freeze thresholds before held-out scoring",
        },
        "inputs": {
            "calibration": {"path": calibration.path, "sha256": calibration.sha256, "frame_count": len(calibration.frames), "cadence_seconds": calibration.cadence_seconds},
            "held_out_evaluation": {"path": evaluation.path, "sha256": evaluation.sha256, "frame_count": len(evaluation.frames), "cadence_seconds": evaluation.cadence_seconds},
        },
        "detectors": detector_ids,
        "calibration_thresholds": calibration_rows,
        "held_out_results": evaluation_rows,
        "related_algorithm_not_used": RESIDUAL_REFERENCE,
        "compute_environment": _environment(),
        "metric_definitions": {
            "event_recall": "detected contiguous event episodes / labeled event episodes; an episode is detected by any alarm-positive frame",
            "false_alarms_per_normal_hour": "contiguous alarm episodes on event_label=none frames divided by event-free evaluated duration",
            "detection_delay": "first alarm-positive frame timestamp minus event onset; misses omitted from delay percentiles",
            "frame_metrics": "TP/FP/FN/TN and precision, recall, F1, specificity, accuracy over evaluated frames after warm-up",
            "thresholds": "chosen only from calibration event-episode maxima; no held-out label participates in fitting or selection",
        },
        "limitations": [
            "Synthetic examples test software behavior only; they do not establish field performance.",
            "The provided native frame contract asserts simultaneity only through exact per-channel timestamps; source clocks and sensor semantics are not independently audited.",
            "protocol.locked_at is self-declared; its SHA-256 identifies protocol bytes but does not independently prove when the protocol was frozen.",
            "EWMA and CUSUM operate per sample and assume the declared regular cadence.",
            "Measured runtime and traced memory are machine- and workload-dependent; no operational compute budget is implied.",
            "No per-channel localization, field labels, asset/regime confidence intervals, or incumbent comparison is included in this starter kit.",
        ],
    }


def _write_json(report: dict[str, Any], destination: str) -> None:
    text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if destination == "-":
        print(text, end="")
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _print_summary(report: dict[str, Any]) -> None:
    print(f"Evidence class: {report['evidence_class']}")
    print("Detector outcomes (held-out evaluation; thresholds calibrated on separate data):")
    for name, row in report["held_out_results"].items():
        metrics = row["held_out"]
        recall = "n/a" if metrics["event_recall"] is None else f"{metrics['event_recall']:.3f}"
        false_rate = metrics["false_alarms_per_normal_hour"]
        false_text = "n/a" if false_rate is None else f"{false_rate:.2f}"
        delay = metrics["detection_delay_ms"]["median"]
        delay_text = "n/a" if delay is None else f"{delay:.1f} ms"
        cost = row["compute_cost"]["mean_cpu_ms_per_frame"]
        print(
            f"  {name:7s} threshold={row['threshold']:.6g}  event_recall={recall}  "
            f"false_alarms/normal_hour={false_text}  median_delay={delay_text}  "
            f"CPU={cost:.6g} ms/frame"
        )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="oes-telemetry-bench", description="Offline native OES32 telemetry replay/evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("evaluate", help="evaluate separate calibration and held-out JSONL datasets")
    evaluate.add_argument("--calibration", required=True)
    evaluate.add_argument("--evaluation", required=True)
    evaluate.add_argument("--protocol", required=True)
    evaluate.add_argument("--evidence-class", required=True, choices=("synthetic", "user_supplied"))
    evaluate.add_argument("--output", default="-", help="JSON report path, or '-' for stdout")
    demo = sub.add_parser("demo", help="run the bundled, explicitly synthetic native-32 example")
    demo.add_argument("--output", default=None, help="report path (default: reports/synthetic-demo-report.json)")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "demo":
            calibration_path = root / "data" / "demo_calibration.jsonl"
            evaluation_path = root / "data" / "demo_holdout.jsonl"
            protocol_path = root / "protocols" / "demo_v1.json"
            output_path = args.output or str(root / "reports" / "synthetic-demo-report.json")
            evidence_class = "synthetic"
        else:
            calibration_path = Path(args.calibration)
            evaluation_path = Path(args.evaluation)
            protocol_path = Path(args.protocol)
            output_path = args.output
            evidence_class = args.evidence_class
        calibration = load_replay(calibration_path)
        evaluation = load_replay(evaluation_path)
        protocol, protocol_bytes = _load_protocol(protocol_path)
        report = run_benchmark(calibration, evaluation, protocol, protocol_bytes, evidence_class)
        _write_json(report, output_path)
        if output_path == "-":
            return 0
        _print_summary(report)
        print(f"Report: {output_path}")
        return 0
    except (ValidationError, OSError, ValueError, OverflowError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


__all__ = [
    "CHANNEL_COUNT", "Frame", "Replay", "ValidationError", "calibrate_threshold",
    "detection_metrics", "detector_scores", "load_replay", "run_benchmark", "validate_split",
]
