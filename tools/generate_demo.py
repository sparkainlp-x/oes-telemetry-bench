#!/usr/bin/env python3
"""Regenerate the bundled, clearly synthetic synchronized-32 demo replays."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CHANNEL_IDS = [f"sensor_{i:02d}" for i in range(32)]


def make_split(
    *, seed: int, count: int, start: datetime, events: list[tuple[int, int, float]],
    nuisance: str,
) -> list[dict[str, object]]:
    rng = np.random.default_rng(seed)
    values = rng.normal(0.0, 0.10, size=(count, 32))
    regimes = ["steady"] * count
    event_by_frame: dict[int, tuple[str, float, int]] = {}

    # Unlabeled operating transients are included to exercise false-alarm accounting.
    if nuisance == "calibration":
        for j, t in enumerate(range(122, 136)):
            values[t] += np.linspace(0.0, 0.20, 14)[j]
            regimes[t] = "load_transition"
    else:
        for j, t in enumerate(range(145, 162)):
            values[t] += np.linspace(0.0, 0.26, 17)[j]
            regimes[t] = "load_transition"
        values[250:259] += rng.normal(0.0, 0.42, size=(9, 32))
        regimes[250:259] = ["noise_transient"] * 9

    for event_number, (onset, duration, amplitude) in enumerate(events, start=1):
        event_id = f"{nuisance}-event-{event_number:02d}"
        for t in range(onset, onset + duration):
            values[t] += amplitude + rng.normal(0.0, 0.045, size=32)
            regimes[t] = "fault_event"
            event_by_frame[t] = (event_id, amplitude, event_number)

    rows: list[dict[str, object]] = []
    for i in range(count):
        stamp = (start + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
        if i in event_by_frame:
            event_id = event_by_frame[i][0]
            event_label = "synthetic_fault"
        else:
            event_id = None
            event_label = "none"
        rows.append({
            "timestamp": stamp,
            "channel_ids": CHANNEL_IDS,
            "channel_timestamps": [stamp] * 32,
            "channels": [round(float(v), 8) for v in values[i]],
            "regime": regimes[i],
            "event_label": event_label,
            "event_id": event_id,
        })
    return rows


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")


def main() -> None:
    base = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    calibration_events = [
        (28, 4, 0.40), (53, 5, 0.46), (78, 4, 0.52), (103, 4, 0.58),
        (148, 5, 0.62), (173, 4, 0.68), (198, 5, 0.74), (223, 4, 0.82),
        (248, 5, 0.91), (278, 5, 1.02),
    ]
    held_out_events = [
        (36, 4, 0.38), (75, 5, 0.45), (115, 4, 0.54), (180, 5, 0.63),
        (215, 4, 0.80), (272, 5, 0.48), (318, 5, 0.91),
    ]
    calibration = make_split(
        seed=20261005, count=320, start=base, events=calibration_events, nuisance="calibration"
    )
    holdout_start = base + timedelta(seconds=320 + 60)
    holdout = make_split(
        seed=20261006, count=360, start=holdout_start, events=held_out_events, nuisance="heldout"
    )
    write_jsonl(ROOT / "data" / "demo_calibration.jsonl", calibration)
    write_jsonl(ROOT / "data" / "demo_holdout.jsonl", holdout)
    print(f"Wrote {len(calibration)} calibration and {len(holdout)} held-out SYNTHETIC native-32 frames.")


if __name__ == "__main__":
    main()
