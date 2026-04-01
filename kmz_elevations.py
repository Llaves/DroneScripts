#!/usr/bin/env python3
"""
kmz_elevation_profile.py

Reads a KMZ file containing exactly one path (LineString), subdivides it into
N equal segments, fetches the elevation of each endpoint from the OpenTopoData
API, and writes the results to a CSV file.

Usage:
    python kmz_elevation_profile.py <input.kmz> <subdivisions> [output.csv]

Arguments:
    input.kmz       Path to the KMZ file containing exactly one LineString path.
    subdivisions    Number of equal subdivisions (produces N+1 points).
    output.csv      Optional output filename (default: elevation_profile.csv).

Requirements:
    pip install lxml requests
"""

import sys
import zipfile
import math
import csv
import time
import argparse
from pathlib import Path

try:
    from lxml import etree
except ImportError:
    sys.exit("Error: 'lxml' is required. Install it with: pip install lxml")

try:
    import requests
except ImportError:
    sys.exit("Error: 'requests' is required. Install it with: pip install requests")


# ---------------------------------------------------------------------------
# KMZ / KML parsing
# ---------------------------------------------------------------------------

# KML namespace — Google Earth uses this universally
KML_NS = "http://www.opengis.net/kml/2.2"


def extract_kml_from_kmz(kmz_path: str) -> bytes:
    """Unzip the KMZ and return the bytes of the primary KML document."""
    with zipfile.ZipFile(kmz_path, "r") as zf:
        names = zf.namelist()
        # The main document is typically doc.kml, but fall back to any .kml
        kml_names = [n for n in names if n.lower().endswith(".kml")]
        if not kml_names:
            sys.exit("Error: No .kml file found inside the KMZ archive.")
        # Prefer doc.kml if it exists
        primary = next((n for n in kml_names if n.lower() == "doc.kml"), kml_names[0])
        return zf.read(primary)


def parse_linestring(kml_bytes: bytes) -> list[tuple[float, float]]:
    """
    Parse the KML and return a list of (lat, lon) tuples from the single
    LineString found in the document.  Raises SystemExit on bad input.
    """
    root = etree.fromstring(kml_bytes)

    # Search with and without the namespace prefix so we handle both variants
    def find_all(tag: str):
        results = root.findall(f".//{{{KML_NS}}}{tag}")
        if not results:
            results = root.findall(f".//{tag}")
        return results

    linestrings = find_all("LineString")
    if len(linestrings) == 0:
        sys.exit("Error: No LineString found in the KML file.")
    if len(linestrings) > 1:
        sys.exit(
            f"Error: Expected exactly one LineString, found {len(linestrings)}. "
            "Please provide a KMZ with a single path."
        )

    ls = linestrings[0]

    # <coordinates> holds "lon,lat[,alt] lon,lat[,alt] …"
    coord_el = ls.find(f"{{{KML_NS}}}coordinates")
    if coord_el is None:
        coord_el = ls.find("coordinates")
    if coord_el is None or not coord_el.text:
        sys.exit("Error: LineString has no <coordinates> element.")

    points: list[tuple[float, float]] = []
    for token in coord_el.text.split():
        token = token.strip()
        if not token:
            continue
        parts = token.split(",")
        if len(parts) < 2:
            sys.exit(f"Error: Malformed coordinate token: '{token}'")
        lon, lat = float(parts[0]), float(parts[1])
        points.append((lat, lon))

    if len(points) < 2:
        sys.exit("Error: LineString must contain at least 2 points.")

    return points


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine_distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Return the great-circle distance in metres between two (lat, lon) points."""
    R = 6_371_000.0
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def total_path_length(points: list[tuple[float, float]]) -> float:
    """Sum of great-circle segment lengths along the path (metres)."""
    return sum(haversine_distance(points[i], points[i + 1]) for i in range(len(points) - 1))


def interpolate_along_path(
    points: list[tuple[float, float]], frac: float
) -> tuple[float, float]:
    """
    Return the (lat, lon) at fractional distance `frac` (0..1) along the
    polyline defined by `points`.  Uses linear interpolation in geographic
    coordinates — adequate for paths where consecutive segments are short
    relative to Earth's radius.
    """
    if frac <= 0.0:
        return points[0]
    if frac >= 1.0:
        return points[-1]

    total = total_path_length(points)
    target = frac * total
    accumulated = 0.0

    for i in range(len(points) - 1):
        seg_len = haversine_distance(points[i], points[i + 1])
        if accumulated + seg_len >= target:
            t = (target - accumulated) / seg_len if seg_len > 0 else 0.0
            lat = points[i][0] + t * (points[i + 1][0] - points[i][0])
            lon = points[i][1] + t * (points[i + 1][1] - points[i][1])
            return (lat, lon)
        accumulated += seg_len

    return points[-1]


def subdivide_path(
    points: list[tuple[float, float]], n: int
) -> list[tuple[float, float]]:
    """Return the n+1 equally-spaced (lat, lon) points along the polyline."""
    return [interpolate_along_path(points, i / n) for i in range(n + 1)]


# ---------------------------------------------------------------------------
# Elevation API
# ---------------------------------------------------------------------------

# OpenTopoData supports up to 100 locations per request
BATCH_SIZE = 100
API_URL = "https://api.opentopodata.org/v1/srtm30m"


def fetch_elevations_batch(
    batch: list[tuple[float, float]]
) -> list[float | None]:
    """
    POST a batch of (lat, lon) pairs to OpenTopoData and return a list of
    elevation values (metres, or None on failure) in the same order.
    """
    locations = "|".join(f"{lat},{lon}" for lat, lon in batch)
    try:
        resp = requests.post(
            API_URL,
            data={"locations": locations},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"\n  Warning: API request failed — {exc}", file=sys.stderr)
        return [None] * len(batch)

    results = data.get("results", [])
    elevations: list[float | None] = []
    for r in results:
        elev = r.get("elevation")
        elevations.append(float(elev) if elev is not None else None)

    # Pad if the API returned fewer results than expected
    while len(elevations) < len(batch):
        elevations.append(None)

    return elevations


def fetch_all_elevations(
    pts: list[tuple[float, float]]
) -> list[float | None]:
    """
    Fetch elevations for all points, batching requests and displaying a
    progress bar.
    """
    total = len(pts)
    elevations: list[float | None] = []

    bar_width = 40
    start_time = time.time()

    def draw_bar(done: int):
        pct = done / total
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)
        elapsed = time.time() - start_time
        eta = (elapsed / done * (total - done)) if done > 0 else 0
        print(
            f"\r  [{bar}] {done}/{total}  elapsed {elapsed:.1f}s  ETA {eta:.1f}s",
            end="",
            flush=True,
        )

    draw_bar(0)

    for start in range(0, total, BATCH_SIZE):
        batch = pts[start : start + BATCH_SIZE]
        elevations.extend(fetch_elevations_batch(batch))
        draw_bar(len(elevations))

        # Be polite to the free API — small delay between batches
        if start + BATCH_SIZE < total:
            time.sleep(0.5)

    print()  # newline after progress bar
    return elevations


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_csv(
    path: str,
    pts: list[tuple[float, float]],
    elevations: list[float | None],
) -> None:
    """Write lat, lon, elevation_m to a CSV file."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["point_index", "latitude", "longitude", "elevation_m"])
        for i, ((lat, lon), elev) in enumerate(zip(pts, elevations)):
            writer.writerow([
                i,
                f"{lat:.8f}",
                f"{lon:.8f}",
                f"{elev:.2f}" if elev is not None else "",
            ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract a path from a KMZ file, subdivide it into equal segments, "
            "fetch elevations from OpenTopoData, and write a CSV."
        )
    )
    parser.add_argument("kmz_file", help="Input KMZ file containing exactly one path.")
    parser.add_argument(
        "subdivisions",
        type=int,
        help="Number of subdivisions (produces N+1 sample points).",
    )
    parser.add_argument(
        "output_csv",
        nargs="?",
        default="elevation_profile.csv",
        help="Output CSV filename (default: elevation_profile.csv).",
    )
    args = parser.parse_args()

    if args.subdivisions < 1:
        sys.exit("Error: subdivisions must be at least 1.")

    kmz_path = args.kmz_file
    if not Path(kmz_path).is_file():
        sys.exit(f"Error: File not found: {kmz_path}")

    # 1. Parse KMZ
    print(f"Reading KMZ: {kmz_path}")
    kml_bytes = extract_kml_from_kmz(kmz_path)
    original_points = parse_linestring(kml_bytes)
    print(f"  Found LineString with {len(original_points)} original vertices.")

    path_len_m = total_path_length(original_points)
    print(f"  Total path length: {path_len_m / 1000:.3f} km")

    # 2. Subdivide
    n = args.subdivisions
    sample_points = subdivide_path(original_points, n)
    segment_len_m = path_len_m / n
    print(
        f"  Subdivided into {n} segments → {len(sample_points)} sample points "
        f"({segment_len_m / 1000:.3f} km each)."
    )

    # 3. Fetch elevations
    print(f"\nFetching elevations from OpenTopoData ({API_URL}) …")
    elevations = fetch_all_elevations(sample_points)

    missing = sum(1 for e in elevations if e is None)
    if missing:
        print(f"  Warning: {missing} point(s) returned no elevation data.")

    # 4. Write CSV
    output_path = args.output_csv
    write_csv(output_path, sample_points, elevations)
    print(f"\nDone. CSV written to: {output_path}")
    print(f"  Columns: point_index, latitude, longitude, elevation_m")


if __name__ == "__main__":
    main()
