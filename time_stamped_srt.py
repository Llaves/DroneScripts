#!/usr/bin/env python3
"""
time_stamped_srt.py

Generate an SRT subtitle file from a flight-data CSV with one entry per row.
Each subtitle shows the CSV timestamp in MM:SS format and HeightAboveGround
rounded to the nearest 0.5 m.

Usage:
    python time_stamped_srt.py <csv_file> <offset> [output_file]

Arguments:
    csv_file     Path to the input CSV file.
    offset       Seconds to shift timestamps (positive = video starts before
                 CSV time 0; e.g. +14 means CSV second 0 appears at video
                 second 14).
    output_file  Optional output path (default: same name as CSV with .srt).
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


def seconds_to_mm_ss(total_seconds: float) -> str:
    """Convert a float number of seconds to display format MM:SS."""
    total_int = int(total_seconds)
    secs = total_int % 60
    mins = total_int // 60
    return f"{mins:02d}:{secs:02d}"


def round_half(value: float) -> float:
    """Round a value to the nearest 0.5."""
    return round(value * 2) / 2


def load_csv(csv_path: str) -> list[dict]:
    """Load the CSV and return a list of row dicts."""
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def build_subtitle_entries(rows: list[dict], offset: float) -> list[dict]:
    """
    Produce one SRT entry per CSV row.

    Each entry spans from its own video timestamp to the next row's timestamp
    (or +1 second for the final row).  Rows that map to negative video time
    are skipped.

    Each entry has:
        index  - 1-based subtitle number
        start  - video time (seconds) when subtitle appears
        end    - video time (seconds) when subtitle disappears
        text   - display string, e.g. "00:14  2.5 m"
    """
    entries = []
    index = 1

    for i, row in enumerate(rows):
        csv_time = float(row["time_s"])
        video_time = csv_time + offset

        if video_time < 0:
            continue

        # End time: next row's video time, or +1 s for the last row
        if i + 1 < len(rows):
            next_video_time = float(rows[i + 1]["time_s"]) + offset
            end_time = max(next_video_time, video_time + 0.001)
        else:
            end_time = video_time + 1.0

        height = float(row["HeightAboveGround"])
        rounded = round_half(height)
        timestamp_str = seconds_to_mm_ss(csv_time)

        entries.append({
            "index": index,
            "start": video_time,
            "end": end_time,
            "text": f"{timestamp_str}  {rounded:.1f} m",
        })
        index += 1

    return entries


def write_srt(entries: list[dict], output_path: str) -> None:
    """Write an SRT file from the entries list."""
    with open(output_path, "w") as f:
        for entry in entries:
            start_ts = seconds_to_srt_time(entry["start"])
            end_ts = seconds_to_srt_time(entry["end"])
            f.write(f"{entry['index']}\n")
            f.write(f"{start_ts} --> {end_ts}\n")
            f.write(f"{entry['text']}\n")
            f.write("\n")
    print(f"Wrote {len(entries)} subtitle entries to: {output_path}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python time_stamped_srt.py <csv_file> <offset> [output_file]")
        print("  offset: seconds difference between video start and CSV time 0")
        print("          positive  → video starts before CSV data")
        print("          negative  → CSV data starts before video")
        sys.exit(1)

    csv_path = sys.argv[1]
    offset = float(sys.argv[2])
    output_path = sys.argv[3] if len(sys.argv) >= 4 else str(Path(csv_path).with_suffix(".srt"))

    rows = load_csv(csv_path)
    if not rows:
        print("Error: CSV file is empty or has no data rows.")
        sys.exit(1)

    entries = build_subtitle_entries(rows, offset)
    if not entries:
        print("No subtitle entries generated (all rows may fall before video start).")
        sys.exit(1)

    write_srt(entries, output_path)


if __name__ == "__main__":
    main()
