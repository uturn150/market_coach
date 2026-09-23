"""market_coach live dashboard — FastAPI, served via a Cloudflare Tunnel
(marketcoach.twofound.cv -> 127.0.0.1:8015), the SAME pattern already proven for
warmly on this box. Not Caddy: this box's `kierr` user has no sudo (lost the
password, no passwordless entry configured — a known, previously-investigated
issue), so this deliberately needs none. Auth is done IN the app (HTTP Basic),
since the tunnel itself doesn't provide it for free the way Caddy's basic_auth
directive would have.

/              the dashboard page (polls /api/snapshot on an interval)
/api/snapshot  fresh account data — SERVER-SIDE CACHED (SNAPSHOT_TTL seconds) so
               rapid browser polling never turns into rapid upstream API calls
/healthz       plain health check, no auth (matches the other sites' convention)

Run: uvicorn webapp:app --host 127.0.0.1 --port 8015
"""
import os, secrets, time
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from marketcoach import snapshot as snap_mod

app = FastAPI()
security = HTTPBasic()

BASE = Path(__file__).parent
INDEX_HTML = BASE / "webapp_static" / "index.html"
PASSWORD_FILE = BASE / "paper_state" / "webapp_password.txt"

SNAPSHOT_TTL = 5
_cache = {"data": None, "at": 0}


def _password():
    if os.environ.get("WEBAPP_PASSWORD"):
        return os.environ["WEBAPP_PASSWORD"]
    return PASSWORD_FILE.read_text().strip()


def require_auth(creds: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(creds.username, "kierr")
    ok_pass = secrets.compare_digest(creds.password, _password())
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})
    return True


def get_snapshot():
    now = time.time()
    if _cache["data"] is None or now - _cache["at"] > SNAPSHOT_TTL:
        _cache["data"] = snap_mod.build()
        _cache["at"] = now
    return _cache["data"]


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/snapshot")
def api_snapshot(_auth: bool = Depends(require_auth)):
    return JSONResponse(get_snapshot())


_atlas = {"data": None, "at": 0}


@app.get("/api/atlas")
def api_atlas(_auth: bool = Depends(require_auth)):
    """Static-ish map of every bot and lab (what it does, live vs history). Cached 60s; reads state files only."""
    now = time.time()
    if _atlas["data"] is None or now - _atlas["at"] > 60:
        from marketcoach import bot_atlas
        _atlas["data"] = bot_atlas.build(); _atlas["at"] = now
    return JSONResponse(_atlas["data"])


@app.get("/", response_class=HTMLResponse)
def index(_auth: bool = Depends(require_auth)):
    return INDEX_HTML.read_text()
