import base64

import httpx
import os
import json
import asyncio
import ee
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import base64

VERSION = "2.0 (GEE dual-resolution support)"

app = FastAPI(title="OpenTopoData Proxy Bridge")

# ---------------------------------------------------------------------------
# 1. CORS Configuration
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 2. Rate Limiting
#    Semaphore(1) ensures only one request hits any external API at a time.
# ---------------------------------------------------------------------------
rate_limiter = asyncio.Semaphore(1)

# ---------------------------------------------------------------------------
# 3. Google Earth Engine initialisation
#    Set the environment variable GEE_SERVICE_ACCOUNT_JSON to the full JSON
#    key file content of a GEE-enabled service account.
#    If the variable is absent the GEE endpoint will return 503 rather than
#    crashing the whole server on startup.
# ---------------------------------------------------------------------------
_ee_ready = False

def _init_ee():
    """
    Supports two ways to supply the service account credential, checked in order:

    1. GEE_KEY_FILE  (recommended for local/Windows)
       Set this to the path of your downloaded .json key file, e.g.:
           $env:GEE_KEY_FILE = "C:/keys/my-project-sa-key.json"

    2. GEE_SERVICE_ACCOUNT_JSON  (recommended for cloud deployments)
       Set this to the raw JSON content of the key file.
       On Windows PowerShell use single quotes to avoid interpolation:
           $env:GEE_SERVICE_ACCOUNT_JSON = (Get-Content -Raw "C:/keys/my-project-sa-key.json")
    """
    global _ee_ready
    try:
        sa_info = None

        # --- Option 1: path to key file (safest on Windows) ------------------
        key_file = os.environ.get("GEE_KEY_FILE", "").strip()
        if key_file:
            if not os.path.isfile(key_file):
                print(f"GEE init failed — GEE_KEY_FILE path not found: {key_file!r}")
                return
            print(f"GEE: loading key file from {key_file!r}")
            with open(key_file, "r", encoding="utf-8") as fh:
                sa_info = json.load(fh)

        # --- Option 2: raw JSON in env var -----------------------------------
        else:
            sa_json = os.environ.get("GEE_SERVICE_ACCOUNT_JSON", "").strip()
            sa_json = base64.b64decode(sa_json).decode("utf-8")
            print(sa_json)
            if not sa_json:
                print("WARNING: neither GEE_KEY_FILE nor GEE_SERVICE_ACCOUNT_JSON is set — "
                      "/gee/elevation will be unavailable.")
                return
            print(f"GEE_SERVICE_ACCOUNT_JSON length: {len(sa_json)} chars, "
                  f"starts with: {repr(sa_json[:6])}")
            try:
                sa_info = json.loads(sa_json)
            except json.JSONDecodeError as exc:
                print(f"GEE init failed — could not parse GEE_SERVICE_ACCOUNT_JSON as JSON: {exc}")
                print("  Tip: on Windows, use GEE_KEY_FILE instead to avoid newline mangling.")
                return

        # --- Validate expected fields ----------------------------------------
        for field in ("client_email", "private_key"):
            if field not in sa_info:
                print(f"GEE init failed — missing field '{field}' in service account JSON.")
                return

        print(f"GEE service account: {sa_info['client_email']}")

        # --- Initialise ------------------------------------------------------
        credentials = ee.ServiceAccountCredentials(
            sa_info["client_email"], key_data=sa_info["private_key"]
        )
        ee.Initialize(credentials)
        _ee_ready = True
        print("Google Earth Engine initialised successfully.")

    except Exception as exc:
        print(f"GEE initialisation failed: {exc}")

_init_ee()

# ---------------------------------------------------------------------------
# Pydantic schema for POST bodies
# ---------------------------------------------------------------------------
class LocationRequest(BaseModel):
    locations: str   # "lat,lon|lat,lon|..."


# ---------------------------------------------------------------------------
# Helper: parse the pipe-delimited locations string
# ---------------------------------------------------------------------------
def _parse_locations(loc_str: str) -> list[tuple[float, float]]:
    pairs = []
    for part in loc_str.strip().split("|"):
        part = part.strip()
        if not part:
            continue
        lat_s, lon_s = part.split(",", 1)
        pairs.append((float(lat_s.strip()), float(lon_s.strip())))
    return pairs


# ---------------------------------------------------------------------------
# GEE sampling — runs in a thread because the ee SDK is synchronous
# ---------------------------------------------------------------------------
def _sample_gee(pairs: list[tuple[float, float]]) -> list[dict]:
    """
    For each (lat, lon) pair, return a dict:
      { "elevation_1m": float|None, "elevation_10m": float|None }

    Both layers are sampled in a single reduceRegions call by stacking them
    into a two-band image. This minimises round-trips to the EE servers.
    """
    # --- Build the two-band image -------------------------------------------
    # Band names will be "elevation_1m" and "elevation_10m" in the result.
    img_1m  = ee.ImageCollection("USGS/3DEP/1m").mosaic().rename("elevation_1m")
    img_10m = ee.Image("USGS/3DEP/10m").rename("elevation_10m")
    stacked = img_1m.addBands(img_10m)

    # --- Build a FeatureCollection of points with an index property ---------
    features = [
        ee.Feature(ee.Geometry.Point([lon, lat]), {"idx": i})
        for i, (lat, lon) in enumerate(pairs)
    ]
    fc = ee.FeatureCollection(features)

    # --- Sample both bands at once ------------------------------------------
    # scale=1 is used so the 1 m layer is read at native resolution;
    # the 10 m band is resampled automatically by EE to match.
    sampled = stacked.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.first(),
        scale=1,
    )

    # --- Pull results back to Python ----------------------------------------
    props_list = sampled.aggregate_array("idx").getInfo()   # ordering handle
    feat_list  = sampled.toList(sampled.size()).getInfo()

    # Map idx → feature properties
    idx_to_props = {}
    for feat in feat_list:
        p = feat.get("properties", {})
        idx_to_props[p["idx"]] = p

    results = []
    for i in range(len(pairs)):
        props = idx_to_props.get(i, {})
        results.append({
            "elevation_1m":  props.get("elevation_1m"),   # None if outside coverage
            "elevation_10m": props.get("elevation_10m"),  # None if outside coverage
        })

    return results


# ===========================================================================
# ROUTES
# ===========================================================================

@app.get("/")
async def root():
    return {
        "status":  "healthy",
        "version": VERSION,
        "message": "OpenTopoData + GEE Proxy Bridge is running",
        "gee_ready": _ee_ready,
    }


@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Bridge is active", "gee_ready": _ee_ready}


# ---------------------------------------------------------------------------
# Existing OpenTopoData proxy endpoint (unchanged behaviour)
# ---------------------------------------------------------------------------
@app.api_route("/v1/{dataset}", methods=["GET", "POST"])
async def get_elevation(dataset: str, request: Request, locations: str = Query(None)):
    """
    Proxy to api.opentopodata.org.
    Supports GET (query params) and POST (JSON body).
    """
    loc_str = locations

    if request.method == "POST":
        try:
            body = await request.json()
            loc_str = body.get("locations")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not loc_str:
        raise HTTPException(status_code=400, detail="No locations provided")

    target_url = f"https://api.opentopodata.org/v1/{dataset}"

    async with rate_limiter:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    target_url,
                    data={"locations": loc_str},
                    timeout=30.0,
                )
                await asyncio.sleep(1)   # respect OpenTopoData rate limit
                return response.json()
            except httpx.RequestError as exc:
                print(f"OpenTopoData request error: {exc.request.url!r}")
                raise HTTPException(status_code=502, detail="External API unreachable")
            except Exception as exc:
                print(f"Internal proxy error: {exc}")
                raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# NEW: Google Earth Engine dual-resolution endpoint
# ---------------------------------------------------------------------------
@app.post("/gee/elevation")
async def gee_elevation(request: Request):
    """
    Query USGS 3DEP 1 m and 10 m layers via Google Earth Engine in a single
    server-side call.

    Request body (JSON):
        { "locations": "lat,lon|lat,lon|..." }

    Response:
        {
          "status": "OK",
          "results": [
            {
              "elevation":    1842.3,   // best available: 1 m if not null, else 10 m
              "elevation_1m":  1842.3,  // raw 1 m value (null if outside coverage)
              "elevation_10m": 1839.0,  // raw 10 m value (null if outside coverage)
              "resolution":   "1m"      // "1m" | "10m" | "null"
            },
            ...
          ]
        }
    """
    if not _ee_ready:
        raise HTTPException(
            status_code=503,
            detail="Google Earth Engine is not initialised. Set GEE_SERVICE_ACCOUNT_JSON.",
        )

    try:
        body = await request.json()
        loc_str = body.get("locations", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not loc_str:
        raise HTTPException(status_code=400, detail="No locations provided")

    try:
        pairs = _parse_locations(loc_str)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse locations: {exc}")

    # Run the synchronous EE SDK call in a thread so we don't block the loop.
    async with rate_limiter:
        try:
            raw = await asyncio.to_thread(_sample_gee, pairs)
        except Exception as exc:
            print(f"GEE sampling error: {exc}")
            raise HTTPException(status_code=502, detail=f"GEE error: {exc}")

    # Build the merged response
    results = []
    for item in raw:
        e1  = item["elevation_1m"]
        e10 = item["elevation_10m"]

        if e1 is not None:
            best       = e1
            resolution = "1m"
        elif e10 is not None:
            best       = e10
            resolution = "10m"
        else:
            best       = None
            resolution = "null"

        results.append({
            "elevation":     best,
            "elevation_1m":  e1,
            "elevation_10m": e10,
            "resolution":    resolution,
        })

    return {"status": "OK", "results": results}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting proxy on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
