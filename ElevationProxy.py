import httpx
import os
import asyncio
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="OpenTopoData Proxy Bridge")

# 1. CORS Configuration
# This allows your browser to send POST requests and custom headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# 2. Rate Limiting Logic
# Semaphore(1) ensures only one request hits the external API at a time
rate_limiter = asyncio.Semaphore(1)

# Schema for POST requests
class LocationRequest(BaseModel):
    locations: str

@app.get("/health")
async def health_check():
    """Simple endpoint to verify the proxy is running."""
    return {"status": "ok", "message": "Bridge is active"}

@app.api_route("/v1/{dataset}", methods=["GET", "POST"])
async def get_elevation(dataset: str, request: Request, locations: str = Query(None)):
    """
    Main proxy endpoint. 
    Supports GET (via query params) and POST (via JSON body).
    """
    loc_str = locations

    # If it's a POST request, extract locations from the JSON body
    if request.method == "POST":
        try:
            body = await request.json()
            loc_str = body.get("locations")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not loc_str:
        raise HTTPException(status_code=400, detail="No locations provided")

    target_url = f"https://api.opentopodata.org/v1/{dataset}"
    
    # 3. Queue and Execute
    async with rate_limiter:
        async with httpx.AsyncClient() as client:
            try:
                # We use POST to OpenTopoData even if the incoming was GET 
                # because it handles long coordinate strings much better.
                response = await client.post(
                    target_url, 
                    data={"locations": loc_str},
                    timeout=30.0
                )
                
                # Enforce the 1-second delay
                await asyncio.sleep(1)
                
                # Return the result back to the browser
                return response.json()

            except httpx.RequestError as exc:
                print(f"An error occurred while requesting {exc.request.url!r}.")
                raise HTTPException(status_code=502, detail="External API unreachable")
            except Exception as e:
                print(f"Internal Proxy Error: {e}")
                raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Set the port (Render/Koyeb provide this via environment variables)
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting proxy on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)