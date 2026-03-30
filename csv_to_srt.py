#!/usr/bin/env python3
"""
csv_to_srt.py

Generate an SRT subtitle file from a flight-data CSV.

Usage:
    python csv_to_srt.py <csv_file> <offset> [output_file]

Arguments:
    csv_file     Path to the input CSV file.
    offset       Seconds to shift timestamps (positive = video starts before
                 CSV time 0; e.g. +14 means CSV second 0 appears at video
                 second 14).
    output_file  Optional output path (default: same name as CSV with .srt).

The subtitle text shows HeightAboveGround rounded to the nearest 0.5 m.
A new SRT entry is created each time the rounded value changes, and each
entry stays on-screen until the next change (or the end of the data).
"""

import csv
import sys
from pathlib import Path


def seconds_to_srt_time(total_seconds: float) -> str:
    """Convert a float number of seconds to SRT timestamp HH:MM:SS,mmm."""
    total_seconds = max(0.0, total_seconds)
    millis = int(round((total_seconds % 1) * 1000))
    total_int = int(total_seconds)
    secs = total_int % 60
    mins = (total_int // 60) % 60
    hours = total_int // 3600
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def round_half(value: float) -> float:
    """Round a value to the nearest 0.5."""
    return round(value * 2) / 2


def load_csv(csv_path: str) -> list[dict]:
    """Load the CSV and return a list of row dicts."""
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def build_subtitle_segments(rows: list[dict], offset: float) -> list[dict]:
    """
    Walk through CSV rows and produce one segment per unique rounded altitude.

    Each segment has:
        start  - video time (seconds) when subtitle appears
        end    - video time (seconds) when subtitle disappears
        label  - display string, e.g. "12.5 m"
    """
    segments = []
    prev_rounded = None
    seg_start = None
    seg_label = None

    for row in rows:
        csv_time = float(row["time_s"])
        height = float(row["HeightAboveGround"])
        rounded = round_half(height)
        video_time = csv_time + offset

        # Skip rows that map to negative video time
        if video_time < 0:
            prev_rounded = rounded  # still track the value
            continue

        if rounded != prev_rounded:
            # Close the previous segment
            if seg_start is not None:
                segments.append({
                    "start": seg_start,
                    "end": video_time,
                    "label": seg_label,
                })
            # Open a new segment
            seg_start = video_time
            seg_label = f"{rounded:.1f} m"
            prev_rounded = rounded

    # Close the final open segment (give it 1-second duration)
    if seg_start is not None:
        last_video_time = float(rows[-1]["time_s"]) + offset
        end_time = max(last_video_time + 1.0, seg_start + 1.0)
        segments.append({
            "start": seg_start,
            "end": end_time,
            "label": seg_label,
        })

    return segments


def write_srt(segments: list[dict], output_path: str) -> None:
    """Write an SRT file from the segment list."""
    with open(output_path, "w") as f:
        for idx, seg in enumerate(segments, start=1):
            start_ts = seconds_to_srt_time(seg["start"])
            end_ts = seconds_to_srt_time(seg["end"])
            f.write(f"{idx}\n")
            f.write(f"{start_ts} --> {end_ts}\n")
            f.write(f"{seg['label']}\n")
            f.write("\n")
    print(f"Wrote {len(segments)} subtitle entries to: {output_path}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python csv_to_srt.py <csv_file> <offset> [output_file]")
        print("  offset: seconds difference between video start and CSV time 0")
        print("          positive  → video starts before CSV data")
        print("          negative  → CSV data starts before video")
        sys.exit(1)

    csv_path = sys.argv[1]
    offset = float(sys.argv[2])

    if len(sys.argv) >= 4:
        output_path = sys.argv[3]
    else:
        output_path = str(Path(csv_path).with_suffix(".srt"))

    rows = load_csv(csv_path)
    if not rows:
        print("Error: CSV file is empty or has no data rows.")
        sys.exit(1)

    segments = build_subtitle_segments(rows, offset)

    if not segments:
        print("No subtitle segments generated (all rows may have been before video start).")
        sys.exit(1)

    write_srt(segments, output_path)


if __name__ == "__main__":
    main()
