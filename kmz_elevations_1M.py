#!/usr/bin/env python3
"""
kmz_elevation_profile.py

Reads a KMZ file containing exactly one path (LineString), subdivides it into
N equal segments, fetches the elevation of each endpoint from the USGS
Elevation Point Query Service (EPQS / 3DEP — National Map), and writes the
results to a CSV file.

The EPQS service uses the best available 3DEP data for each location:
  • 1-metre lidar-derived DEMs where available
  • 1/3 arc-second (~10 m) seamless DEMs as a fallback

Coverage is the United States and its territories.

Usage:
    python kmz_elevation_profile.py <input.kmz> <subdivisions> [output.csv]

Arguments:
    input.kmz       KMZ file containing exactly one LineString path.
    subdivisions    Number of equal subdivisions (produces N+1 sample points).
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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from lxml import etree
except ImportError:
    sys.exit("Error: 'lxml' is required.  Install it with:  pip install lxml")

try:
    import requests
except ImportError:
    sys.exit("Error: 'requests' is required.  Install it with:  pip install requests")


# ---------------------------------------------------------------------------
# USGS EPQS constants
# ---------------------------------------------------------------------------

# v1 JSON endpoint — uses best available 3DEP data (up to 1 m lidar)
EPQS_URL = "https://epqs.nationalmap.gov/v1/json"

# Maximum parallel HTTP workers.  The EPQS service is per-point only (no
# batch endpoint), so concurrency is the only way to keep throughput up.
MAX_WORKERS = 8

# Per-request timeout (seconds)
REQUEST_TIMEOUT = 20

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 2.0   # seconds between retries

# EPQS sentinel value that means "no data"
EPQS_NO_DATA = -1_000_000


# ---------------------------------------------------------------------------
# KMZ / KML parsing
# ---------------------------------------------------------------------------

KML_NS = "http://www.opengis.net/kml/2.2"


def extract_kml_from_kmz(kmz_path: str) -> bytes:
    """Unzip the KMZ and return the bytes of the primary KML document."""
    with zipfile.ZipFile(kmz_path, "r") as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            sys.exit("Error: No .kml file found inside the KMZ archive.")
        primary = next((n for n in kml_names if n.lower() == "doc.kml"), kml_names[0])
        return zf.read(primary)


def parse_linestring(kml_bytes: bytes) -> list[tuple[float, float]]:
    """
    Parse the KML and return a list of (lat, lon) tuples from the single
    LineString.  Exits with a clear message on bad input.
    """
    root = etree.fromstring(kml_bytes)

    def find_all(tag: str):
        # Try namespaced first, then bare (handles both KML variants)
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
    """Great-circle distance in metres between two (lat, lon) points."""
    R = 6_371_000.0
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def total_path_length(points: list[tuple[float, float]]) -> float:
    return sum(haversine_distance(points[i], points[i + 1]) for i in range(len(points) - 1))


def interpolate_along_path(
    points: list[tuple[float, float]], frac: float
) -> tuple[float, float]:
    """(lat, lon) at fractional distance frac ∈ [0, 1] along the polyline."""
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
    """n+1 equally-spaced (lat, lon) sample points along the polyline."""
    return [interpolate_along_path(points, i / n) for i in range(n + 1)]


# ---------------------------------------------------------------------------
# Elevation fetching — USGS EPQS (3DEP / National Map)
# ---------------------------------------------------------------------------

def fetch_elevation_single(
    lat: float, lon: float, session: requests.Session
) -> float | None:
    """
    Query the USGS EPQS for one point.  Returns elevation in metres (NAVD 88),
    or None if the service returns no data or the request fails after retries.

    GET https://epqs.nationalmap.gov/v1/json
        ?x=<lon>&y=<lat>&wkid=4326&units=Meters&includeDate=false

    Response: { "value": 1234.567 }   (null or -1000000 → no data)
    """
    params = {
        "x": f"{lon:.8f}",
        "y": f"{lat:.8f}",
        "wkid": "4326",
        "units": "Meters",
        "includeDate": "false",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(EPQS_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            value = data.get("value")
            if value is None or float(value) == EPQS_NO_DATA:
                return None
            return float(value)
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    return None


# ---------------------------------------------------------------------------
# Thread-safe progress bar
# ---------------------------------------------------------------------------

class ProgressBar:
    """Minimal thread-safe ASCII progress bar printed to stdout."""

    def __init__(self, total: int, width: int = 40):
        self.total = total
        self.width = width
        self._done = 0
        self._lock = threading.Lock()
        self._start = time.time()
        self._draw(0)

    def increment(self):
        with self._lock:
            self._done += 1
            self._draw(self._done)

    def _draw(self, done: int):
        pct = done / self.total if self.total else 1.0
        filled = int(self.width * pct)
        bar = "█" * filled + "░" * (self.width - filled)
        elapsed = time.time() - self._start
        eta_str = (
            f"ETA {elapsed / done * (self.total - done):.0f}s" if done > 0 else "ETA --"
        )
        print(
            f"\r  [{bar}] {done}/{self.total}  {elapsed:.1f}s elapsed  {eta_str}   ",
            end="",
            flush=True,
        )

    def finish(self):
        self._draw(self.total)
        print()


# ---------------------------------------------------------------------------
# Concurrent fetch for all points
# ---------------------------------------------------------------------------

def fetch_all_elevations(
    pts: list[tuple[float, float]],
) -> list[float | None]:
    """
    Fetch elevations for all points concurrently using a thread pool.
    Returns a list in the same order as pts.
    """
    total = len(pts)
    results: list[float | None] = [None] * total
    bar = ProgressBar(total)

    # requests.Session is safe to share across threads; urllib3 manages the
    # per-thread connection pool internally.
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "kmz-elevation-profile/2.0 (python-requests; USGS EPQS)"}
    )

    futures: dict = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for idx, (lat, lon) in enumerate(pts):
            future = executor.submit(fetch_elevation_single, lat, lon, session)
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception:
                results[idx] = None
            bar.increment()

    bar.finish()
    session.close()
    return results


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_csv(
    path: str,
    pts: list[tuple[float, float]],
    elevations: list[float | None],
) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["point_index", "latitude", "longitude", "elevation_m"])
        for i, ((lat, lon), elev) in enumerate(zip(pts, elevations)):
            writer.writerow([
                i,
                f"{lat:.8f}",
                f"{lon:.8f}",
                f"{elev:.3f}" if elev is not None else "",
            ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract a path from a KMZ, subdivide it into equal segments, "
            "fetch 3DEP elevations from the USGS National Map EPQS, "
            "and write a CSV."
        )
    )
    parser.add_argument("kmz_file", help="Input KMZ file (exactly one LineString path).")
    parser.add_argument(
        "subdivisions",
        type=int,
        help="Number of equal subdivisions (produces N+1 sample points).",
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

    # 1. Parse KMZ -----------------------------------------------------------
    print(f"Reading KMZ: {kmz_path}")
    kml_bytes = extract_kml_from_kmz(kmz_path)
    original_points = parse_linestring(kml_bytes)
    print(f"  Found LineString with {len(original_points)} original vertices.")

    path_len_m = total_path_length(original_points)
    print(f"  Total path length: {path_len_m / 1000:.3f} km  ({path_len_m:.1f} m)")

    # 2. Subdivide -----------------------------------------------------------
    n = args.subdivisions
    sample_points = subdivide_path(original_points, n)
    segment_len_m = path_len_m / n
    print(
        f"  Subdivided into {n} segment(s) → {len(sample_points)} sample points "
        f"(~{segment_len_m:.1f} m / {segment_len_m / 1000:.3f} km each)."
    )

    # 3. Fetch elevations ----------------------------------------------------
    print(
        f"\nFetching elevations from USGS 3DEP EPQS"
        f"\n  Endpoint : {EPQS_URL}"
        f"\n  Data     : 1 m lidar where available, 1/3 arc-second (~10 m) elsewhere"
        f"\n  Coverage : United States and territories only"
        f"\n  Workers  : {MAX_WORKERS} concurrent requests\n"
    )
    elevations = fetch_all_elevations(sample_points)

    missing = sum(1 for e in elevations if e is None)
    if missing:
        print(
            f"  Warning: {missing} point(s) returned no elevation data "
            "(ocean, outside US coverage, or transient API error)."
        )

    valid = [e for e in elevations if e is not None]
    if valid:
        print(
            f"  Elevation range : {min(valid):.1f} m – {max(valid):.1f} m  "
            f"(Δ {max(valid) - min(valid):.1f} m)"
        )

    # 4. Write CSV -----------------------------------------------------------
    output_path = args.output_csv
    write_csv(output_path, sample_points, elevations)
    print(f"\nDone.  CSV written to: {output_path}")
    print("  Columns: point_index, latitude, longitude, elevation_m")
    print("  Vertical datum: NAVD 88 (metres)")


if __name__ == "__main__":
    main()
