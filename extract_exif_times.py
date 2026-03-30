#!/usr/bin/env python3
"""
Extract time-of-day from EXIF DateTimeOriginal for all JPEGs in a folder.
Outputs a CSV with columns: file_name, Timestamp (hh:mm:ss, 24-hour).

Usage:
    python extract_exif_times.py <folder_path> [output.csv]

Requirements:
    pip install Pillow
"""

import sys
import os
import csv
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS

# Disable PIL's decompression bomb warning for large images, since we're only reading metadata.
Image.MAX_IMAGE_PIXELS = None  


DATETIME_ORIGINAL_TAG = 36867  # Numeric tag ID for DateTimeOriginal


def get_datetime_original(image_path: Path) -> str | None:
    """Return the DateTimeOriginal value from a JPEG's EXIF data, or None."""
    try:
        with Image.open(image_path) as img:
            exif_data = img._getexif()
            if exif_data is None:
                return None
            return exif_data.get(DATETIME_ORIGINAL_TAG)
    except Exception as e:
        print(f"  Warning: could not read EXIF from '{image_path.name}': {e}")
        return None


def exif_datetime_to_date_and_time(exif_value: str) -> tuple[str, str] | None:
    """
    Convert an EXIF datetime string ('YYYY:MM:DD HH:MM:SS')
    to a (date, time) tuple: ('YYYY/MM/DD', 'HH:MM:SS').
    Returns None if the value cannot be parsed.
    """
    try:
        date_part, time_part = exif_value.strip().split(" ")
        yyyy, mm, dd = date_part.split(":")
        h, m, s = time_part.split(":")
        if (len(yyyy) == 4 and len(mm) == 2 and len(dd) == 2
                and len(h) == 2 and len(m) == 2 and len(s) == 2):
            return f"{yyyy}/{mm}/{dd}", time_part
    except (ValueError, AttributeError):
        pass
    return None


def process_folder(folder: Path, output_csv: Path) -> None:
    jpeg_extensions = {".jpg", ".jpeg"}
    jpeg_files = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in jpeg_extensions
    )

    if not jpeg_files:
        print(f"No JPEG files found in '{folder}'.")
        return

    rows = []
    skipped = 0

    for image_path in jpeg_files:
        raw = get_datetime_original(image_path)
        if raw is None:
            print(f"  Skipping '{image_path.name}': no DateTimeOriginal tag.")
            skipped += 1
            continue

        parsed = exif_datetime_to_date_and_time(raw)
        if parsed is None:
            print(f"  Skipping '{image_path.name}': unrecognised datetime format '{raw}'.")
            skipped += 1
            continue

        date_str, time_str = parsed
        rows.append({"file_name": image_path.name, "Date": date_str, "Timestamp": time_str})

    rows.sort(key=lambda r: (r["Date"], r["Timestamp"]))

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file_name", "Date", "Timestamp"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. {len(rows)} file(s) written to '{output_csv}'.", end="")
    if skipped:
        print(f" {skipped} file(s) skipped (no valid EXIF timestamp).", end="")
    print()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print(f"Error: '{folder}' is not a directory.")
        sys.exit(1)

    output_csv = Path(sys.argv[2]) if len(sys.argv) >= 3 else folder / "exif_times.csv"

    print(f"Scanning '{folder}' for JPEG files...\n")
    process_folder(folder, output_csv)


if __name__ == "__main__":
    main()
