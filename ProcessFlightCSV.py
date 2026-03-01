#!/usr/bin/env python3
"""
Process drone flight path CSV in two phases:
  Phase 1: Keep columns b,c,d,f,h,i; insert empty GndElev between h and i;
           add empty alt_m column; add Distance Along Path column.
  Phase 2: Select every 10th row, fetch ground elevation from OpenTopoData API,
           populate GndElev, and compute alt_m = GndElev + height_m.
           Output contains only those every-10th rows.

Outputs:
  <output_base>.csv  - tabular results
  <output_base>.kml  - flight path with altitudeMode=absolute, red line, width 3
  <output_base>.png  - plot of HeightAboveGround vs Distance Along Path (optional)
"""

import argparse
import pandas as pd
import requests
import time
import sys
from typing import List, Tuple


def write_plot(df: pd.DataFrame, plot_path: str, kml_name: str) -> None:
    """
    Save a plot of HeightAboveGround (y) vs Distance Along Path (x).
    """
    import matplotlib.pyplot as plt

    print(f"\n--- Writing plot: {plot_path} ---")

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(df['Distance Along Path'], df['HeightAboveGround'], linewidth=1.2)
    ax.set_xlabel('Distance Along Path (m)')
    ax.set_ylabel('Height Above Ground (m)')
    ax.set_title(kml_name)
    ax.grid(True, linestyle='--', alpha=0.5)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    print(f"  Plot saved with {len(df)} points.")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Process drone flight path CSV: column filtering, elevation lookup, distance'
    )
    parser.add_argument('input_csv', help='Input CSV file path')
    parser.add_argument('output_base', help='Base name for output files (.csv and .kml will be appended)')
    parser.add_argument('kml_name', help='Value for the <n> tag in the KML file')
    parser.add_argument(
        '--plot',
        action='store_true',
        help='If present, save a plot of HeightAboveGround vs Distance Along Path as <output_base>.png'
    )
    parser.add_argument(
        '--dataset',
        choices=['aster30m', 'ned10m'],
        default='aster30m',
        help='OpenTopoData dataset to use (default: aster30m)'
    )
    return parser.parse_args()


def fetch_elevations_batch(locations: List[Tuple[float, float]], dataset: str) -> List[float]:
    """
    Fetch elevations for a batch of coordinates from OpenTopoData API.
    """
    locations_str = "|".join([f"{lat},{lon}" for lat, lon in locations])
    url = f"https://api.opentopodata.org/v1/{dataset}?locations={locations_str}"

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        data = response.json()

        if data['status'] != 'OK':
            raise Exception(f"API returned non-OK status: {data.get('status')}")

        elevations = [result['elevation'] for result in data['results']]
        return elevations

    except requests.exceptions.RequestException as e:
        raise Exception(f"API request failed: {e}")
    except (KeyError, ValueError) as e:
        raise Exception(f"Failed to parse API response: {e}")


def phase1(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 1: Keep columns b,c,d,f,h,i; insert empty GndElev and alt_m columns;
    add Distance Along Path column.
    """
    print("\n--- Phase 1: Column filtering and Distance Along Path ---")

    keep_indices = [0, 1, 2, 5, 7, 8]
    if max(keep_indices) >= len(df.columns):
        raise ValueError(f"Input CSV has only {len(df.columns)} columns; need at least 9.")

    cols = df.columns.tolist()
    kept_cols = [cols[i] for i in keep_indices]
    col_i = kept_cols[-1]

    df_out = df[kept_cols].copy()
    df_out['GndElev'] = None
    df_out['Distance Along Path'] = None
    df_out['FlightAltitude'] = None
    df_out['HeightAboveGround'] = None

    col_i_values = df[col_i].values
    distances = [0.0]
    for val in col_i_values[1:]:
        distances.append(0.1 * val + distances[-1])

    print(f"  Computed 'Distance Along Path' for {len(distances)} rows")
    df_out['Distance Along Path'] = distances

    print(f"  Columns: {df_out.columns.tolist()}")
    print(f"  Rows: {len(df_out)}")

    return df_out


def phase2(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """
    Phase 2: Select every 10th row, fetch ground elevation, populate GndElev,
    compute FlightAltitude and HeightAboveGround.
    """
    print("\n--- Phase 2: Ground elevation lookup and alt_m computation ---")

    for col in ['lat', 'lng', 'GndElev', 'height_m']:
        if col not in df.columns:
            raise ValueError(f"Required column '{col}' not found. "
                             f"Available columns: {df.columns.tolist()}")

    df_selected = df.iloc[::10].copy().reset_index(drop=True)
    total_points = len(df_selected)
    print(f"  Selected {total_points} rows (every 10th) for elevation lookup")

    locations = list(zip(df_selected['lat'], df_selected['lng']))

    batch_size = 100
    all_elevations = []

    print(f"  Fetching elevations for {total_points} points (dataset: {dataset})...")

    for batch_start in range(0, total_points, batch_size):
        batch = locations[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (total_points + batch_size - 1) // batch_size

        print(f"    Fetching batch {batch_num}/{total_batches} ({len(batch)} points)...")

        elevations = fetch_elevations_batch(batch, dataset)
        all_elevations.extend(elevations)

        if batch_start + batch_size < total_points:
            time.sleep(1)

    print(f"  Successfully fetched {len(all_elevations)} elevation values")

    df_selected['GndElev'] = [round(e, 2) for e in all_elevations]
    launch_height = df_selected['GndElev'].iloc[0]
    df_selected['FlightAltitude'] = df_selected['height_m'] + launch_height
    df_selected['HeightAboveGround'] = df_selected['FlightAltitude'] - df_selected['GndElev']

    return df_selected


def write_kml(df: pd.DataFrame, kml_path: str, kml_name: str) -> None:
    """
    Write a KML file containing a single LineString path using lat, lng, and
    FlightAltitude from every row in df.
    altitudeMode: absolute. Line style: red (ff0000ff), width 3.
    """
    print(f"\n--- Writing KML file: {kml_path} ---")

    coord_lines = []
    for _, row in df.iterrows():
        lon = row['lng']
        lat = row['lat']
        alt = row['FlightAltitude']
        coord_lines.append(f"          {lon},{lat},{alt:.4f}")

    coordinates_block = "\n".join(coord_lines)

    # KML colors are in aabbggrr format; red = ff0000ff
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        '  <Document>',
        f'    <n>{kml_name}</n>',
        '    <Style id="flightPathStyle">',
        '      <LineStyle>',
        '        <color>ff0000ff</color>',
        '        <width>3</width>',
        '      </LineStyle>',
        '    </Style>',
        '    <Placemark>',
        f'      <n>{kml_name}</n>',
        '      <styleUrl>#flightPathStyle</styleUrl>',
        '      <LineString>',
        '        <altitudeMode>absolute</altitudeMode>',
        '        <coordinates>',
        coordinates_block,
        '        </coordinates>',
        '      </LineString>',
        '    </Placemark>',
        '  </Document>',
        '</kml>',
    ]
    kml_content = "\n".join(lines) + "\n"

    with open(kml_path, 'w', encoding='utf-8') as f:
        f.write(kml_content)

    print(f"  Written {len(df)} coordinate points to KML.")


def main():
    args = parse_arguments()

    output_csv = args.output_base + ".csv"
    output_kml = args.output_base + ".kml"

    try:
        print(f"Reading input file: {args.input_csv}")
        df_input = pd.read_csv(args.input_csv)
        print(f"  Loaded {len(df_input)} rows, {len(df_input.columns)} columns")

        df_phase1 = phase1(df_input)
        df_phase2 = phase2(df_phase1, args.dataset)

        print(f"\nWriting CSV output: {output_csv}")
        df_phase2.to_csv(output_csv, index=False)
        print(f"  Done! CSV contains {len(df_phase2)} rows.")

        write_kml(df_phase2, output_kml, args.kml_name)
        print(f"  Done! KML written to {output_kml}")

        if args.plot:
            output_plot = args.output_base + ".png"
            write_plot(df_phase2, output_plot, args.kml_name)

    except FileNotFoundError:
        print(f"Error: Input file '{args.input_csv}' not found", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
