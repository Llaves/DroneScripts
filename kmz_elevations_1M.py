#!/usr/bin/env python3
"""
kmz_elevation_profile.py (GEE Version)

Reads a KMZ file, subdivides it, and fetches elevations from a custom 
Google Earth Engine backend using batch requests.
"""

import sys
import zipfile
import math
import csv
import time
import argparse
import threading
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
# API configuration
# ---------------------------------------------------------------------------
GEE_URL = "https://creative-melisandra-droneteam-fb5207f5.koyeb.app/gee/elevation"

# The API handles batches. 50-100 points per request is a good balance.
BATCH_SIZE = 500 
REQUEST_TIMEOUT = 30

# ---------------------------------------------------------------------------
# KMZ / KML parsing (Logic remains the same)
# ---------------------------------------------------------------------------
KML_NS = "http://www.opengis.net/kml/2.2"

def extract_kml_from_kmz(kmz_path: str) -> bytes:
    with zipfile.ZipFile(kmz_path, "r") as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            sys.exit("Error: No .kml file found inside the KMZ archive.")
        primary = next((n for n in kml_names if n.lower() == "doc.kml"), kml_names[0])
        return zf.read(primary)

def parse_linestring(kml_bytes: bytes) -> list[tuple[float, float]]:
    """
    Parse the KML and return a list of (lat, lon) tuples.
    Fixed to avoid FutureWarning and handle coordinate lookup more robustly.
    """
    root = etree.fromstring(kml_bytes)

    def find_all(tag: str):
        results = root.findall(f".//{{{KML_NS}}}{tag}")
        if len(results) == 0:
            results = root.findall(f".//{tag}")
        return results

    linestrings = find_all("LineString")
    if len(linestrings) == 0:
        sys.exit("Error: No LineString found in the KML file.")
    
    # We take the first LineString found
    ls = linestrings[0]
    
    # FIX: Explicitly check for None to avoid FutureWarning
    coord_el = ls.find(f"{{{KML_NS}}}coordinates")
    if coord_el is None:
        coord_el = ls.find("coordinates")

    # FIX: Check if coord_el is None or if text is empty/whitespace
    if coord_el is None or coord_el.text is None or not coord_el.text.strip():
        sys.exit("Error: LineString has no <coordinates> content.")

    points: list[tuple[float, float]] = []
    # Split by whitespace to handle various KML formatting styles
    for token in coord_el.text.strip().split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            # KML is Lon, Lat, [Alt] -> We want (Lat, Lon)
            lon, lat = float(parts[0]), float(parts[1])
            points.append((lat, lon))
        except ValueError:
            continue

    if len(points) < 2:
        sys.exit(f"Error: Found {len(points)} points; need at least 2 for a path.")

    return points

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def haversine_distance(p1, p2):
    R = 6371000.0
    lat1, lon1, lat2, lon2 = map(math.radians, [p1[0], p1[1], p2[0], p2[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def total_path_length(points):
    return sum(haversine_distance(points[i], points[i+1]) for i in range(len(points)-1))

def interpolate_along_path(points, frac):
    if frac <= 0: return points[0]
    if frac >= 1: return points[-1]
    total = total_path_length(points)
    target = frac * total
    accumulated = 0.0
    for i in range(len(points)-1):
        seg_len = haversine_distance(points[i], points[i+1])
        if accumulated + seg_len >= target:
            t = (target - accumulated) / seg_len if seg_len > 0 else 0.0
            return (points[i][0] + t*(points[i+1][0]-points[i][0]), 
                    points[i][1] + t*(points[i+1][1]-points[i][1]))
        accumulated += seg_len
    return points[-1]

def subdivide_path(points, n):
    return [interpolate_along_path(points, i/n) for i in range(n+1)]

# ---------------------------------------------------------------------------
# Elevation fetching — GEE Batch API
# ---------------------------------------------------------------------------
def fetch_elevations_batch(pts: list[tuple[float, float]]) -> list[dict]:
    """
    Fetches elevations in batches from the GEE proxy.
    Returns a list of dicts: {'elevation': float|None, 'resolution': str}
    """
    all_results = []
    total_pts = len(pts)
    
    print(f"Fetching elevations for {total_pts} points in batches of {BATCH_SIZE}...")

    session = requests.Session()
    
    for i in range(0, total_pts, BATCH_SIZE):
        batch = pts[i : i + BATCH_SIZE]
        # Format: "lat,lon|lat,lon|..."
        loc_string = "|".join([f"{lat},{lon}" for lat, lon in batch])
        
        try:
            resp = session.post(
                GEE_URL, 
                json={"locations": loc_string}, 
                timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("status") == "OK":
                all_results.extend(data.get("results", []))
            else:
                # Fill with None if API status is not OK
                all_results.extend([{"elevation": None, "resolution": "null"}] * len(batch))
        
        except Exception as e:
            print(f"\n  Batch error: {e}")
            all_results.extend([{"elevation": None, "resolution": "null"}] * len(batch))
        
        # Simple progress update
        progress = min(i + BATCH_SIZE, total_pts)
        print(f"\r  Progress: {progress}/{total_pts} points processed", end="", flush=True)

    print() # Newline after progress
    return all_results

# ---------------------------------------------------------------------------
# Main Execution
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="KMZ to Elevation Profile via GEE")
    parser.add_argument("kmz_file", help="Input KMZ file")
    parser.add_argument("subdivisions", type=int, help="Number of segments")
    parser.add_argument("output_csv", nargs="?", default="elevation_profile.csv")
    args = parser.parse_args()

    # 1. Load KMZ
    kml_bytes = extract_kml_from_kmz(args.kmz_file)
    original_points = parse_linestring(kml_bytes)
    
    # 2. Subdivide
    sample_points = subdivide_path(original_points, args.subdivisions)
    
    # 3. Fetch from GEE
    results = fetch_elevations_batch(sample_points)

    # 4. Write CSV
    with open(args.output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "latitude", "longitude", "elevation_m", "resolution", "elev_1m", "elev_10m"])
        for i, ((lat, lon), res) in enumerate(zip(sample_points, results)):
            writer.writerow([
                i, 
                f"{lat:.8f}", 
                f"{lon:.8f}", 
                res.get("elevation"), 
                res.get("resolution"),
                res.get("elevation_1m"),
                res.get("elevation_10m")
            ])

    print(f"Success! Profile saved to {args.output_csv}")

if __name__ == "__main__":
    main()